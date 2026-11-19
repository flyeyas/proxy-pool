"""One leased coordinator owns workers, writes, rate limits and recovery."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from uuid import uuid4

from .config import desired
from .events import emit
from .health import TargetHealth
from .models import TargetResult
from .policy import ValidationPolicy
from .region import lookup_country
from .repository import ValidationRepository, encode
from .worker import RoundWorker


class ValidationCoordinator:
    def __init__(self, repository=None):
        self.repository = repository
        self.stop = threading.Event()
        self.owner = uuid4().hex
        self.active = {}
        self.geo = None
        self.geo_executor = None
        self.completed = 0

    def _geo_tick(self, endpoint=None):
        repo = self.repository
        if self.geo is not None and self.geo[0].done():
            future, endpoint_id, host = self.geo
            try:
                country = future.result()
            except Exception:
                country = ''
            repo.redis.set(repo.prefix + 'geo:' + host, country or '-', ex=604800 if country else 600)
            if country:
                repo.update_region(endpoint_id, country)
            self.geo = None
        if endpoint is None or endpoint.country_source == 'exit_verified':
            return
        import ipaddress
        try:
            if not ipaddress.ip_address(endpoint.host).is_global:
                return
        except ValueError:
            return
        cached = repo.redis.get(repo.prefix + 'geo:' + endpoint.host)
        if cached and cached != '-' and (endpoint.country != cached or endpoint.country_source == 'source'):
            repo.update_region(endpoint.endpoint_id, cached)
        elif cached is None and self.geo is None and repo.redis.set(repo.prefix + 'geo_rate', '1', nx=True, ex=1):
            if self.geo_executor is None:
                self.geo_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='validation_geo')
            self.geo = (self.geo_executor.submit(lookup_country, endpoint.host), endpoint.endpoint_id, endpoint.host)

    def run(self):
        next_renew = next_status = 0
        control = None
        leader = False
        try:
            while not self.stop.is_set():
                try:
                    settings = desired()
                    if self.repository is None:
                        if settings['mode'] == 'legacy' and not settings.get('configured', False):
                            self.stop.wait(1)
                            continue
                        self.repository = ValidationRepository()
                    repo = self.repository
                    if not leader:
                        leader = repo.acquire_leader(self.owner)
                        if not leader:
                            self.stop.wait(1)
                            continue
                        control = repo.control()
                        if control is None:
                            if settings['mode'] == 'active' or settings.get('persisted_mode') == 'active':
                                raise RuntimeError('active validation control missing')
                            control = repo.initialize('legacy' if settings['mode'] == 'legacy' else 'shadow', settings['policy'])
                        repo.ensure_queue_indexes()
                        emit('coordinator_started', mode=control['mode'], owner=self.owner)
                    now = time.time()
                    if now >= next_renew:
                        if not repo.renew(repo.leader_key, self.owner):
                            raise RuntimeError('leader lease lost')
                        for task, worker, prepaid in self.active.values():
                            if not repo.renew(repo.keys(task['endpoint'].endpoint_id)[2], task['token']):
                                worker.close()
                        next_renew = now + 10
                    current = repo.control()
                    if current and current.get('draining') and not repo.redis.exists(repo.prefix + 'transition'):
                        recovered = {**current, 'draining': False, 'epoch': uuid4().hex}
                        repo.update_control(current, recovered, self.owner)
                        current = repo.control()
                    if current != control:
                        for task, worker, prepaid in self.active.values():
                            worker.close()
                        self.active.clear()
                        control = current
                    if not self.active and control['mode'] == 'legacy' and settings['mode'] == 'shadow':
                        updated = {**control, 'mode': 'shadow', 'epoch': uuid4().hex, 'draining': False}
                        if repo.update_control(control, updated, self.owner):
                            control = repo.control()
                            repo.reschedule_all()
                    reload_pending = settings['policy'].fingerprint != control.get('config_version')
                    if not self.active and reload_pending:
                        rules_changed = settings['policy'].version != control['policy_version']
                        updated = {**control, 'epoch': uuid4().hex, 'policy': settings['policy'].to_dict(),
                                   'policy_version': settings['policy'].version,
                                   'config_version': settings['policy'].fingerprint}
                        if repo.update_control(control, updated, self.owner):
                            # Read the canonical JSON snapshot. Keeping tuple-valued
                            # local policy data looks different from Redis lists and
                            # would wrongly cancel newly spawned workers next tick.
                            control = repo.control()
                            reload_pending = False
                            if rules_changed:
                                repo.redis.delete(*[repo.prefix + kind + profile
                                    for kind in ('health:', 'canary:', 'health_schedule:')
                                    for profile in ('domestic', 'overseas')])
                                repo.reschedule_all()
                    policy = ValidationPolicy.from_dict(control['policy'])
                    health = TargetHealth(repo)
                    for endpoint_id, (task, worker, prepaid) in list(self.active.items()):
                        def permit(host, prepaid=prepaid, worker=worker, task=task):
                            profile = worker.current[1] if worker.current else ''
                            if profile in ('domestic', 'overseas') and profile not in prepaid['allowed_profiles']:
                                if not health.allow(profile, task['endpoint'].endpoint_id):
                                    return False
                                prepaid['allowed_profiles'].add(profile)
                            return repo.permit(host, policy.target_rps)
                        if worker.poll(permit):
                            for result in worker.results:
                                health.observe(endpoint_id, result)
                                emit('probe_result', endpoint_id=endpoint_id, round_id=task['round_id'], **result)
                            committed = repo.commit(task, worker.results, policy, self.owner, health.states())
                            emit('round_complete', endpoint_id=endpoint_id, round_id=task['round_id'],
                                 committed=committed, results=len(worker.results), queue=task.get('queue'))
                            del self.active[endpoint_id]
                            self.completed += 1
                    self._geo_tick()
                    if control['mode'] in ('shadow', 'active') and not control.get('draining') and not reload_pending:
                        for _ in range(policy.workers * 2):
                            if len(self.active) >= policy.workers:
                                break
                            task = repo.claim(self.owner, control)
                            if task is None:
                                break
                            endpoint = task['endpoint']
                            self._geo_tick(endpoint)
                            variants = policy.variants_for(endpoint)
                            profiles = tuple(dict.fromkeys(tuple(endpoint.pending_profiles) + policy.profiles_for(endpoint.country)))
                            planning = profiles + (('http_only',) if policy.http_probe_url else ())
                            backoffs = {variant + '|' + profile: result.get('retry_at', 0)
                                for variant, data in endpoint.variants.items()
                                for profile, result in data.get('targets', {}).items()
                                if result.get('policy_version') == policy.version and result.get('retry_at', 0) > now}
                            ready = [p for p in planning if any(backoffs.get(v + '|' + p, 0) <= now for v in variants)]
                            if not ready:
                                wait = min(backoffs[v + '|' + p] - now for v in variants for p in planning)
                                result = TargetResult(variants[0], profiles[0], 'deferred', 'retry_after',
                                    attempts=0, retry_after=wait, checked_at=now, policy_version=policy.version).to_dict()
                                repo.commit(task, [result], policy, self.owner, health.states())
                                continue
                            first = next((p for p in ready if p == 'http_only' or health.allow(p, endpoint.endpoint_id)), None)
                            from urllib.parse import urlsplit
                            host = urlsplit(policy.http_probe_url if first == 'http_only' else policy.targets[first].url).hostname if first else None
                            # Admission avoids process churn. Every actual request is
                            # separately rate-limited immediately before child I/O.
                            admitted = first is not None and repo.permit(host, policy.target_rps, admission=True)
                            if not admitted:
                                result = TargetResult(variants[0], profiles[0], 'deferred',
                                    'target_health' if first is None else 'rate_limit', attempts=0,
                                    retry_after=30 if first is None else 1 / policy.target_rps,
                                    checked_at=now, policy_version=policy.version).to_dict()
                                repo.commit(task, [result], policy, self.owner, health.states())
                                continue
                            candidate = endpoint.candidate(repo.credentials(endpoint))
                            import ipaddress
                            from dataclasses import replace
                            if endpoint.pending_profiles:
                                candidate = replace(candidate, requested_profiles=tuple(endpoint.pending_profiles))
                            if backoffs:
                                candidate = replace(candidate, profile_backoffs=backoffs)
                            try:
                                public_proxy = ipaddress.ip_address(candidate.host).is_global
                            except ValueError:
                                public_proxy = True
                            if public_proxy and now - endpoint.exit_checked_at >= 86400:
                                candidate = replace(candidate, exit_lookup_due=True)
                            worker = RoundWorker(candidate, variants, policy, endpoint.country)
                            self.active[endpoint.endpoint_id] = (task, worker, {
                                'allowed_profiles': {first}})
                    if now >= next_status:
                        health.schedule(now)
                        oldest = repo.redis.zrange(repo.due, 0, 0, withscores=True)
                        repo.redis.set(repo.prefix + 'status', encode({
                            'heartbeat': now, 'owner': self.owner, 'mode': control['mode'],
                            'policy_version': policy.version, 'workers': len(self.active),
                            'worker_limit': policy.workers, 'completed': self.completed,
                            'queued': repo.redis.zcount(repo.due, '-inf', now),
                            'scheduled_total': repo.redis.zcard(repo.due),
                            'new_scheduled': repo.redis.zcard(repo.new_due),
                            'use_scheduled': repo.redis.zcard(repo.use_due),
                            'oldest_due_delay': max(0, now - oldest[0][1]) if oldest else 0,
                            'targets': health.states()}), ex=30)
                        next_status = now + 5
                    self.stop.wait(0.02 if self.active else 0.2)
                except Exception as exc:
                    # Classification only: never print URLs/DB credentials from exceptions.
                    emit('coordinator_error', error_type=type(exc).__name__)
                    for task, worker, prepaid in self.active.values():
                        worker.close()
                    self.active.clear()
                    if leader and self.repository:
                        try:
                            self.repository.release_leader(self.owner)
                        except Exception:
                            pass
                    leader = False
                    self.stop.wait(1)
        finally:
            for task, worker, prepaid in self.active.values():
                worker.close()
            if self.geo_executor:
                self.geo_executor.shutdown(wait=True, cancel_futures=True)
            if leader and self.repository:
                self.repository.release_leader(self.owner)


def start_coordinator():
    coordinator = ValidationCoordinator()
    thread = threading.Thread(target=coordinator.run, name='validation_coordinator', daemon=True)
    thread.start()
    return coordinator, thread
