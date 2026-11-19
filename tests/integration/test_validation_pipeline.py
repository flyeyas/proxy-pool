import json
import threading
import time
import pytest

from helper.validation.config import save_desired
from helper.validation.coordinator import ValidationCoordinator
from helper.validation.migration import activate, rollback
from helper.validation.normalize import normalize_candidate
from tests.fixtures.proxy_network import certificates, proxy_service, target_service
from tests.integration.test_validation_protocols import policy_for
from tests.integration.test_validation_store import store, enqueue, result


def _delayed_round(connection, candidate, variants, policy_data, country):
    from helper.validation.worker import _round
    time.sleep(0.8)
    _round(connection, candidate, variants, policy_data, country)


@pytest.mark.parametrize('target_path', ['/ok', '/redirect'])
def test_real_proxy_worker_to_redis_and_route(store, tmp_path, monkeypatch, client, target_path):
    from queue import Queue
    from helper.check import Checker
    from helper.fetch import Fetcher
    from fetcher.sources.geonode import GeonodeFetcher
    from helper.validation import service
    from util.webRequest import WebRequest
    repo, old_policy, control, owner = store
    monkeypatch.setattr('helper.runtimeConfig.CONFIG_FILE', str(tmp_path / 'runtime.json'))
    monkeypatch.setattr(service, 'repository', lambda: repo)
    ca, tls = certificates(tmp_path)
    payload = {}
    with target_service(tls, source_payload=payload) as (target_port, targets), proxy_service('http') as (port, events):
        policy = policy_for(target_port, ca, path=target_path, remote=True)
        save_desired({'mode': 'shadow', 'policy': policy.to_dict()})
        updated = {**control, 'mode': 'active', 'policy': policy.to_dict(), 'policy_version': policy.version}
        assert repo.update_control(control, updated)
        repo.release_leader(owner)
        payload['data'] = [{'ip': '127.0.0.1', 'port': port, 'country': 'CN', 'protocols': ['http']}]
        original_get = WebRequest.get
        requested_urls = []
        def local_source(self, url, *args, **kwargs):
            requested_urls.append(url)
            kwargs.update(verify=ca, retry_time=1)
            return original_get(self, 'https://localhost:%d/source' % target_port, *args, **kwargs)
        monkeypatch.setattr(WebRequest, 'get', local_source)
        monkeypatch.setattr('helper.fetch._discover_fetchers', lambda excluded: [GeonodeFetcher])
        queue = Queue()
        candidates = list(Fetcher().run(fetcher_names={'GeonodeFetcher'}))
        assert candidates and candidates[0].validation_candidate.country == 'CN'
        for candidate in candidates:
            queue.put(candidate)
        Checker('raw', queue)
        assert 'limit=500' in requested_urls[0]
        coordinator = ValidationCoordinator(repo)
        thread = threading.Thread(target=coordinator.run)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            route = None
            while time.monotonic() < deadline and route is None:
                route = repo.route(policy)
                time.sleep(0.05)
            assert route is not None
            assert route[0].sources == ['geonode']
            assert targets and events
            response = client.get('/api/v2/proxies/random/?protocol=http')
            assert response.status_code == 200
            assert response.json['data']['proxy_url'] == 'http://127.0.0.1:%d' % port
        finally:
            coordinator.stop.set()
            thread.join(timeout=8)
            assert not thread.is_alive()


def test_activation_and_rollback_preserve_backup_and_config(store, tmp_path, monkeypatch):
    from helper.runtimeConfig import readConfig, writeConfig
    repo, policy, control, owner = store
    monkeypatch.setattr('helper.runtimeConfig.CONFIG_FILE', str(tmp_path / 'runtime.json'))
    writeConfig({'api_tokens': [{'name': 'keep'}], 'fetcher_exclude': ['Disabled']})
    save_desired({'mode': 'shadow', 'policy': policy.to_dict()})
    original = json.dumps({'proxy': '1.2.3.4:80', 'https': False})
    repo.redis.hset(repo.table, '1.2.3.4:80', original)
    candidate, task = enqueue(store)
    assert repo.commit(task, [result(policy)], policy, owner)
    activated = activate(repo, policy, minimum=1)
    assert repo.control()['mode'] == 'active'
    assert repo.redis.hget(repo.table, '1.2.3.4:80') is not None
    assert repo.redis.hget(activated['rollback_hash'], '1.2.3.4:80') == original
    rollback(repo)
    assert repo.redis.hget(repo.table, '1.2.3.4:80') == original
    assert readConfig()['api_tokens'] == [{'name': 'keep'}]
    assert readConfig()['fetcher_exclude'] == ['Disabled']


def test_activation_does_not_count_unchecked_legacy_pool_as_covered(store, tmp_path, monkeypatch):
    import pytest
    repo, policy, control, owner = store
    monkeypatch.setattr('helper.runtimeConfig.CONFIG_FILE', str(tmp_path / 'runtime.json'))
    save_desired({'mode': 'shadow', 'policy': policy.to_dict()})
    repo.redis.hset(repo.table, '9.9.9.9:80', json.dumps({'proxy': '9.9.9.9:80'}))
    candidate, task = enqueue(store)
    assert repo.commit(task, [result(policy)], policy, owner)
    with pytest.raises(ValueError, match='coverage'):
        activate(repo, policy, minimum=1)
    assert repo.control()['mode'] == 'shadow'


def test_rollback_restores_originally_empty_pool(store, tmp_path, monkeypatch):
    repo, policy, control, owner = store
    monkeypatch.setattr('helper.runtimeConfig.CONFIG_FILE', str(tmp_path / 'runtime.json'))
    save_desired({'mode': 'shadow'})
    candidate, task = enqueue(store)
    assert repo.commit(task, [result(policy)], policy, owner)
    activation = activate(repo, policy, minimum=1)
    assert activation['rollback_hash'] is None
    assert repo.redis.hlen(repo.table) == 1
    assert rollback(repo)['restored_empty']
    assert not repo.redis.exists(repo.table)
    assert repo.route(policy) is None


def test_missing_rollback_backup_does_not_change_active_mode(store, tmp_path, monkeypatch):
    repo, policy, control, owner = store
    monkeypatch.setattr('helper.runtimeConfig.CONFIG_FILE', str(tmp_path / 'runtime.json'))
    save_desired({'mode': 'shadow'})
    repo.redis.hset(repo.table, '1.2.3.4:80', json.dumps({'proxy': '1.2.3.4:80'}))
    candidate, task = enqueue(store)
    assert repo.commit(task, [result(policy)], policy, owner)
    activation = activate(repo, policy, minimum=1)
    repo.redis.delete(activation['rollback_hash'])
    with pytest.raises(ValueError, match='backup missing'):
        rollback(repo)
    assert repo.control()['mode'] == 'active'


def test_shadow_can_be_reenabled_after_rollback(store, tmp_path, monkeypatch):
    repo, policy, control, owner = store
    monkeypatch.setattr('helper.runtimeConfig.CONFIG_FILE', str(tmp_path / 'runtime.json'))
    save_desired({'mode': 'shadow'})
    rollback(repo)
    save_desired({'mode': 'shadow'})
    repo.release_leader(owner)
    coordinator = ValidationCoordinator(repo)
    thread = threading.Thread(target=coordinator.run)
    thread.start()
    try:
        deadline = time.monotonic() + 3
        while repo.control()['mode'] != 'shadow' and time.monotonic() < deadline:
            time.sleep(0.02)
        assert repo.control()['mode'] == 'shadow'
    finally:
        coordinator.stop.set()
        thread.join(timeout=5)


def test_config_reload_is_not_starved_by_a_busy_queue(store, tmp_path, monkeypatch):
    from dataclasses import replace
    import helper.validation.coordinator as coordinator_module
    repo, old, control, owner = store
    ca, tls = certificates(tmp_path)
    with target_service(tls) as (target_port, _), proxy_service('http') as (port, _):
        policy = policy_for(target_port, ca, path='/slow', round_deadline=0.5,
                            workers=1, target_rps=20, check_interval=1)
        assert repo.update_control(control, {**control, 'policy': policy.to_dict(),
            'policy_version': policy.version, 'config_version': policy.fingerprint})
        settings = {'mode': 'shadow', 'policy': policy}
        monkeypatch.setattr(coordinator_module, 'desired', lambda: settings)
        repo.release_leader(owner)
        for index in range(20):
            candidate = normalize_candidate({'proxy': 'http://user%d:p@127.0.0.1:%d' % (index, port),
                                             'country': 'CN'}, identity_key=b'x' * 32)
            repo.submit(candidate, policy)
        coordinator = ValidationCoordinator(repo)
        thread = threading.Thread(target=coordinator.run)
        thread.start()
        try:
            deadline = time.monotonic() + 3
            while not coordinator.active and time.monotonic() < deadline:
                time.sleep(0.01)
            assert coordinator.active
            tuned = replace(policy, workers=2)
            settings['policy'] = tuned
            deadline = time.monotonic() + 3
            while repo.control()['config_version'] != tuned.fingerprint and time.monotonic() < deadline:
                time.sleep(0.02)
            assert repo.control()['config_version'] == tuned.fingerprint
            assert repo.control()['policy']['workers'] == 2
        finally:
            coordinator.stop.set()
            thread.join(timeout=5)


def test_request_rate_is_enforced_after_delayed_worker_start(store, tmp_path, monkeypatch):
    import helper.validation.coordinator as coordinator_module
    from helper.validation.worker import RoundWorker
    repo, old, control, owner = store
    ca, tls = certificates(tmp_path)
    with target_service(tls) as (target_port, requests), proxy_service('http') as (port, _):
        policy = policy_for(target_port, ca, workers=2, round_deadline=5, target_rps=2)
        assert repo.update_control(control, {**control, 'policy': policy.to_dict(),
            'policy_version': policy.version, 'config_version': policy.fingerprint})
        monkeypatch.setattr(coordinator_module, 'desired', lambda: {'mode': 'shadow', 'policy': policy})
        calls = []
        def create(candidate, variants, snapshot, country):
            calls.append(True)
            return RoundWorker(candidate, variants, snapshot, country,
                **({'target': _delayed_round} if len(calls) == 1 else {}))
        monkeypatch.setattr(coordinator_module, 'RoundWorker', create)
        for index in range(2):
            repo.submit(normalize_candidate({'proxy': 'http://user%d:p@127.0.0.1:%d' % (index, port),
                                             'country': 'CN'}, identity_key=b'x' * 32), policy)
        repo.release_leader(owner)
        coordinator = ValidationCoordinator(repo)
        thread = threading.Thread(target=coordinator.run)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while len(requests) < 2 and time.monotonic() < deadline:
                time.sleep(0.02)
            assert len(requests) == 2
            assert requests[1]['time'] - requests[0]['time'] >= 0.45
        finally:
            coordinator.stop.set()
            thread.join(timeout=5)
