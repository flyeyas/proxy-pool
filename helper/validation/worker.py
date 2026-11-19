"""Killable process boundary. Workers never touch Redis, locks or log files."""
import multiprocessing
import os
import threading
import time

from .models import HARD_FAILURES, TargetResult
from .policy import ValidationPolicy
from .transport import probe


def supervised(target, args, deadline_at):
    """Keep the network child bounded even if its coordinator is killed or stuck."""
    parent = multiprocessing.parent_process()
    finished = threading.Event()

    def supervise():
        while not finished.wait(0.05):
            if parent is not None and not parent.is_alive():
                os._exit(70)
            if time.monotonic() >= deadline_at:
                os._exit(71)

    guard = threading.Thread(target=supervise, name='validation_parent_guard', daemon=True)
    guard.start()
    try:
        target(*args)
    finally:
        finished.set()
        guard.join(timeout=0.2)


def _entry(target, connection, candidate, variants, policy_data, country, deadline_at):
    supervised(target, (connection, candidate, variants, policy_data, country), deadline_at)


def _round(connection, candidate, variants, policy_data, country):
    try:
        policy = ValidationPolicy.from_dict(policy_data)
        deadline = time.monotonic() + policy.round_deadline
        successful_variant = None

        def permit(host):
            # A same-domain redirect needs a later quota slot. Briefly wait within
            # this round instead of restarting the first redirect forever.
            until = min(deadline, time.monotonic() + 1)
            while time.monotonic() < until:
                connection.send({'event': 'permit', 'host': host})
                if not connection.poll(max(0, until - time.monotonic())):
                    return False
                if connection.recv() is True:
                    return True
                time.sleep(min(0.05, max(0, until - time.monotonic())))
            return False

        for variant in variants:
            if time.monotonic() >= deadline:
                break
            profiles = tuple(dict.fromkeys(candidate.requested_profiles + policy.profiles_for(country)))
            profiles += (('http_only',) if policy.http_probe_url else ())
            for profile in profiles:
                retry_at = candidate.profile_backoffs.get(variant + '|' + profile, 0)
                if retry_at > time.time():
                    result = TargetResult(variant, profile, 'deferred', 'retry_after',
                        attempts=0, checked_at=time.time(), retry_after=retry_at - time.time(),
                        policy_version=policy.version)
                    connection.send({'event': 'result', 'result': result.to_dict()})
                    continue
                connection.send({'event': 'started', 'variant': variant, 'profile': profile})
                result = probe(candidate, variant, profile, policy, deadline, permit)
                connection.send({'event': 'result', 'result': result.to_dict()})
                if result.passed and profile in ('domestic', 'overseas'):
                    successful_variant = variant
                if result.code in HARD_FAILURES or result.stage in ('proxy_auth', 'proxy_tls'):
                    break
        if successful_variant and candidate.exit_lookup_due and policy.exit_geo_url and deadline - time.monotonic() >= 3:
            connection.send({'event': 'started', 'variant': successful_variant, 'profile': 'exit_geo'})
            result = probe(candidate, successful_variant, 'exit_geo', policy, deadline, permit)
            connection.send({'event': 'result', 'result': result.to_dict()})
        connection.send({'event': 'done'})
    except BaseException:
        # Never return raw exception strings containing URLs or credentials.
        try:
            connection.send({'event': 'error', 'code': 'environment_error'})
        except (OSError, EOFError):
            pass
    finally:
        connection.close()


class RoundWorker:
    def __init__(self, candidate, variants, policy, country='', target=_round):
        context = multiprocessing.get_context('spawn')
        self.connection, child = context.Pipe()
        self.started = time.monotonic()
        self.process = context.Process(target=_entry, args=(target, child, candidate, variants,
                                       policy.to_dict(), country, self.started + policy.round_deadline))
        self.process.daemon = False
        self.policy = policy
        self.results = []
        self.current = None
        self.done = False
        self._closed = False
        self.process.start()
        child.close()

    def poll(self, permit=lambda host: True):
        if self._closed:
            return True
        try:
            while self.connection.poll():
                event = self.connection.recv()
                if event['event'] == 'permit':
                    self.connection.send(bool(permit(event['host'])))
                elif event['event'] == 'started':
                    self.current = (event['variant'], event['profile'])
                elif event['event'] == 'result':
                    self.results.append(event['result'])
                    self.current = None
                elif event['event'] in ('done', 'error'):
                    self.done = True
        except (EOFError, OSError):
            self.done = True
        if time.monotonic() - self.started >= self.policy.round_deadline:
            if self.current:
                variant, profile = self.current
                self.results.append(TargetResult(variant, profile, 'deadline_exceeded',
                                                 'round_deadline', checked_at=time.time(),
                                                 elapsed_ms=int(self.policy.round_deadline * 1000),
                                                 policy_version=self.policy.version).to_dict())
            self.done = True
        if not self.process.is_alive():
            self.done = True
        if self.done:
            self.close()
        return self.done

    def close(self):
        if self._closed:
            return
        if self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=self.policy.shutdown_grace / 2)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=self.policy.shutdown_grace / 2)
        self.connection.close()
        self.process.close()
        self._closed = True


def validate_round(candidate, variants, policy, country='', permit=None):
    worker = RoundWorker(candidate, variants, policy, country)
    try:
        while not worker.poll(permit or (lambda host: True)):
            time.sleep(0.01)
        return worker.results
    finally:
        worker.close()
