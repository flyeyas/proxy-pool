"""Controlled, isolated coordinator resource soak; not a production proxy collector.

Run in the Linux test container with its own Redis URL. It creates a unique
namespace and only removes that namespace on completion. Public sites are unused.
"""
import argparse
import contextlib
from dataclasses import replace
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import threading
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helper.validation import coordinator as coordinator_module
from helper.validation.normalize import normalize_candidate
from helper.validation.policy import TargetPolicy, ValidationPolicy
from helper.validation.repository import ValidationRepository
from tests.fixtures.proxy_network import certificates, proxy_service, target_service


def resources():
    processes = {}
    for name in os.listdir('/proc'):
        if not name.isdigit():
            continue
        try:
            text = Path('/proc', name, 'status').read_text()
            fields = {k: v.strip() for line in text.splitlines() if ':' in line for k, v in [line.split(':', 1)]}
            processes[int(name)] = (int(fields['PPid']), int(fields.get('VmRSS', '0 kB').split()[0]))
        except (OSError, ValueError, KeyError):
            pass
    selected = {os.getpid()}
    while True:
        added = {pid for pid, (ppid, rss) in processes.items() if ppid in selected} - selected
        if not added:
            break
        selected.update(added)
    fds = 0
    for pid in selected:
        try:
            fds += len(os.listdir('/proc/%d/fd' % pid))
        except OSError:
            pass
    return {'rss_kib': sum(processes.get(pid, (0, 0))[1] for pid in selected),
            'processes': len(selected), 'fds': fds}


class ResourceHealth:
    """Disable target breaker only in this worst-case transport load harness."""
    def __init__(self, repo):
        pass
    def allow(self, *args):
        return True
    def observe(self, *args):
        return 'unknown'
    def states(self):
        return {'domestic': 'unknown', 'overseas': 'unknown'}
    def schedule(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--redis-url', required=True)
    parser.add_argument('--seconds', type=int, default=3600)
    parser.add_argument('--endpoints', type=int, default=500)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if not Path('/proc').exists() or not 12 <= args.seconds <= 86400 or not 1 <= args.endpoints <= 5000:
        parser.error('requires Linux, 12..86400 seconds and 1..5000 endpoints')
    repo = ValidationRepository(args.redis_url, 'validation_soak_' + uuid4().hex)
    samples, stages = [], []
    total_start = time.monotonic()
    current = {}
    original_desired = coordinator_module.desired
    original_health = coordinator_module.TargetHealth
    coordinator_module.desired = lambda: {'mode': 'shadow', 'policy': current['policy']}
    coordinator_module.TargetHealth = ResourceHealth
    coordinator = coordinator_module.ValidationCoordinator(repo)
    thread = None
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='validation-soak-') as temp:
            ca, tls = certificates(Path(temp))
            with target_service(tls) as (target_port, target_events), proxy_service('http') as (proxy_port, proxy_events):
                discarded_paths = {}
                def request_count(path):
                    return discarded_paths.get(path, 0) + sum(event['path'] == path for event in target_events)
                combinations = [(w, scenario) for w in (1, 4, 8, 20) for scenario in ('fast', 'refused', 'slow')]
                for index, (workers, scenario) in enumerate(combinations):
                    path = '/slow' if scenario == 'slow' else '/ok'
                    target = TargetPolicy('https://localhost:%d%s' % (target_port, path), ('localhost',), ('google',))
                    policy = ValidationPolicy(targets={'domestic': target, 'overseas': target},
                        target_ca=ca, workers=workers, target_rps=20, round_deadline=1,
                        retries=0, check_interval=1, unavailable_interval=1, exit_geo_url='')
                    current['policy'] = policy
                    if index == 0:
                        repo.initialize('shadow', policy)
                        thread = threading.Thread(target=coordinator.run)
                        thread.start()
                    # Remove old stage endpoints by fenced deletion; in-flight writes must lose.
                    cursor = 0
                    ids = []
                    while True:
                        endpoints, cursor, _ = repo.page(cursor, 200)
                        ids.extend(e.endpoint_id for e in endpoints)
                        if cursor is None:
                            break
                    for endpoint_id in ids:
                        repo.remove(endpoint_id)
                    port = 1 if scenario == 'refused' else proxy_port
                    for n in range(args.endpoints):
                        candidate = normalize_candidate({'proxy': 'http://bench%d:fixture@127.0.0.1:%d' % (n, port),
                                                         'country': 'CN'}, source='soak', identity_key=b'test-only-identity-key-32-bytes!!!')
                        repo.submit(candidate, policy)
                    reload_deadline = time.monotonic() + 5
                    while repo.control().get('config_version') != policy.fingerprint and time.monotonic() < reload_deadline:
                        time.sleep(0.05)
                    if repo.control().get('config_version') != policy.fingerprint:
                        raise RuntimeError('load profile did not become effective')
                    start = time.monotonic()
                    start_count = coordinator.completed
                    start_requests = request_count(path)
                    until = total_start + args.seconds * (index + 1) / len(combinations)
                    while time.monotonic() < until:
                        sample = {'elapsed': round(time.monotonic() - total_start, 2), 'workers': workers,
                                  'scenario': scenario, 'active': len(coordinator.active), **resources()}
                        samples.append(sample)
                        # The short-lived protocol fixtures retain events for tests.
                        # Bound those lists so the harness itself cannot fake a leak.
                        if len(target_events) > 1000:
                            cut = len(target_events) - 1000
                            removed = target_events[:cut]
                            del target_events[:cut]
                            for event in removed:
                                discarded_paths[event['path']] = discarded_paths.get(event['path'], 0) + 1
                        if len(proxy_events) > 1000:
                            del proxy_events[:-1000]
                        if len(samples) % 60 == 0:
                            print(json.dumps(sample), flush=True)
                        time.sleep(min(0.5, max(0, until - time.monotonic())))
                    stage = {'workers': workers, 'scenario': scenario,
                             'effective_workers': repo.control()['policy']['workers'],
                             'duration': round(time.monotonic() - start, 2),
                             'rounds_completed': coordinator.completed - start_count,
                             'target_requests': request_count(path) - start_requests}
                    stages.append(stage)
                    print(json.dumps({'stage_complete': stage}), flush=True)
    finally:
        coordinator.stop.set()
        if thread:
            thread.join(timeout=10)
        coordinator_module.desired = original_desired
        coordinator_module.TargetHealth = original_health
        summary = {'python': platform.python_version(), 'platform': platform.platform(),
                   'duration_seconds': round(time.monotonic() - total_start, 2),
                   'endpoints_per_stage': args.endpoints, 'test_target_rps': 20,
                   'target_breaker_disabled_for_load': True, 'stages': stages,
                   'peak_rss_kib': max((s['rss_kib'] for s in samples), default=0),
                   'peak_fds': max((s['fds'] for s in samples), default=0),
                   'peak_processes': max((s['processes'] for s in samples), default=0),
                   'coordinator_stopped': thread is None or not thread.is_alive(), 'samples': samples}
        summary['final_resources'] = resources()
        output.write_text(json.dumps(summary, indent=2))
        for batch in (list(repo.redis.scan_iter(repo.table + '*')),):
            if batch:
                repo.redis.delete(*batch)
        repo.redis.close()
    if (len(stages) != 12 or not summary['coordinator_stopped']
            or summary['final_resources']['processes'] > 2
            or any(s['rounds_completed'] == 0 or (s['scenario'] != 'refused' and s['target_requests'] == 0) for s in stages)):
        raise SystemExit('soak incomplete; inspect the report')
    print(json.dumps({k: v for k, v in summary.items() if k != 'samples'}), flush=True)


if __name__ == '__main__':
    main()
