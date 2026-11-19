import json
from types import SimpleNamespace

import fakeredis
import pytest

from helper.validation.config import desired, identity_key, save_desired
from helper.validation.health import TargetHealth
from helper.validation.models import ProxyEndpointV2, TargetResult
from helper.validation.normalize import normalize_candidate
from helper.validation.policy import ValidationPolicy
from helper.validation.state import apply_results, available_routes, public_endpoint


def _blocking_geo_lookup(connection, host):
    import time
    time.sleep(5)


def evidence(policy, code='available', profile='overseas', now=100):
    return TargetResult('http', profile, code, 'test', checked_at=now,
                        policy_version=policy.version).to_dict()


def test_deferred_keeps_old_evidence_but_not_past_freshness():
    policy = ValidationPolicy()
    endpoint = ProxyEndpointV2.from_candidate(normalize_candidate('http://x:80'), 0)
    endpoint, _ = apply_results(endpoint, [evidence(policy)], policy, 'first', 100)
    endpoint, _ = apply_results(endpoint, [evidence(policy, 'deferred', now=200)], policy, 'next', 200)
    assert available_routes(endpoint, policy, 200)
    assert not available_routes(endpoint, policy, 701)
    assert public_endpoint(endpoint, policy, 701)['status'] == 'stale'


def test_different_profile_and_protocol_are_not_collapsed():
    policy = ValidationPolicy()
    endpoint = ProxyEndpointV2.from_candidate(normalize_candidate({'proxy': 'http://x:80', 'country': 'US'}), 0)
    endpoint, _ = apply_results(endpoint, [evidence(policy, profile='domestic'),
        evidence(policy, 'target_restricted')], policy, 'first', 100)
    assert not available_routes(endpoint, policy, 100)
    assert available_routes(endpoint, policy, 100, profile='domestic')
    assert endpoint.variants['http']['hard_failures'] == 0


def test_unknown_health_blocks_automatic_hard_deletion():
    policy = ValidationPolicy()
    endpoint = ProxyEndpointV2.from_candidate(normalize_candidate('http://x:80'), 0)
    for index in range(3):
        endpoint, dead = apply_results(endpoint, [evidence(policy, 'proxy_connect_failed')],
                                        policy, str(index), 100)
        assert not dead
    endpoint, dead = apply_results(endpoint, [evidence(policy, 'proxy_connect_failed')], policy,
                                    'last', 100, {'domestic': 'healthy', 'overseas': 'healthy'})
    assert dead


def test_health_reference_protection_and_recovery():
    repo = SimpleNamespace(redis=fakeredis.FakeRedis(decode_responses=True), prefix='health:test:')
    health = TargetHealth(repo)
    policy = ValidationPolicy()
    for index in range(3):
        health.observe('ref' + str(index), evidence(policy), now=100)
    assert health.states()['overseas'] == 'healthy'
    for endpoint in ['ref0', 'ref1'] + ['bad' + str(i) for i in range(8)]:
        health.observe(endpoint, evidence(policy, 'target_restricted'), now=110)
    assert health.states()['overseas'] == 'open'
    assert not health.allow('overseas', 'ref0', now=120)
    assert not health.allow('overseas', 'not-a-reference', now=200)
    health.observe('ref0', evidence(policy), now=200)
    assert health.states()['overseas'] == 'half_open'
    health.observe('ref1', evidence(policy), now=231)
    assert health.states()['overseas'] == 'healthy'


def test_missing_reference_does_not_make_target_healthy():
    repo = SimpleNamespace(redis=fakeredis.FakeRedis(decode_responses=True), prefix='health:test:')
    health = TargetHealth(repo)
    for index in range(12):
        health.observe(str(index), evidence(ValidationPolicy(), 'target_restricted'), now=100)
    assert health.states()['overseas'] == 'unknown'


def test_config_updates_preserve_other_fields_and_identity_key(tmp_path, monkeypatch):
    from helper.runtimeConfig import readConfig, writeConfig
    monkeypatch.setattr('helper.runtimeConfig.CONFIG_FILE', str(tmp_path / 'config.json'))
    writeConfig({'api_tokens': [{'name': 'existing'}], 'fetcher_exclude': ['Disabled']})
    save_desired({'mode': 'shadow', 'policy': {'workers': 2}})
    secret = identity_key()
    assert len(secret) == 32 and identity_key() == secret
    assert readConfig()['api_tokens'] == [{'name': 'existing'}]
    assert readConfig()['fetcher_exclude'] == ['Disabled']
    assert desired()['policy'].workers == 2
    monkeypatch.setenv('VALIDATION_WORKERS', '3')
    assert desired()['policy'].workers == 3
    with pytest.raises(ValueError):
        save_desired({'policy': {'workers': 4}})


def test_recursive_log_redaction():
    from helper.validation.events import redact
    value = redact({'nested': {'password': 'secret', 'detail': 'https://user:pass@example.com?token=private'},
                    'error': ValueError('password=do-not-print')})
    text = json.dumps(value)
    assert 'secret' not in text and 'user:pass' not in text and 'private' not in text
    assert 'do-not-print' not in text


def test_http_only_is_explicit_and_not_main_target_eligible():
    policy = ValidationPolicy(http_probe_url='http://example.com/health')
    endpoint = ProxyEndpointV2.from_candidate(normalize_candidate('http://x:80'), 0)
    endpoint, _ = apply_results(endpoint, [evidence(policy, profile='http_only')], policy, 'http', 100)
    assert not available_routes(endpoint, policy, 100)
    assert public_endpoint(endpoint, policy, 100)['status'] == 'http_only'


def test_verified_exit_changes_required_profile_without_guessing():
    policy = ValidationPolicy()
    endpoint = ProxyEndpointV2.from_candidate(normalize_candidate({'proxy': 'http://x:80', 'country': 'CN'}), 0)
    exit_result = evidence(policy, 'exit_verified', profile='exit_geo')
    exit_result.update(exit_ip='198.51.100.24', exit_country='US')
    endpoint, _ = apply_results(endpoint, [evidence(policy, profile='domestic'), exit_result], policy, 'exit', 100)
    assert endpoint.country == 'US' and endpoint.country_source == 'exit_verified'
    assert endpoint.endpoint_country == 'CN'
    assert not available_routes(endpoint, policy, 100)


def test_slow_unknown_protocol_does_not_starve_other_discovery():
    policy = ValidationPolicy()
    endpoint = ProxyEndpointV2.from_candidate(normalize_candidate('x:8080'), 0)
    assert policy.variants_for(endpoint)[0] == 'http'
    failed = evidence(policy, 'deadline_exceeded')
    endpoint, _ = apply_results(endpoint, [failed], policy, 'first', 100)
    assert policy.variants_for(endpoint)[0] == 'socks5h'
    failed = {**failed, 'variant': 'socks5h'}
    endpoint, _ = apply_results(endpoint, [failed], policy, 'second', 100)
    assert policy.variants_for(endpoint)[0] == 'socks4a'


def test_lost_active_control_does_not_silently_fall_back(tmp_path, monkeypatch):
    from redis.exceptions import RedisError
    from helper.validation import service
    monkeypatch.setattr('helper.runtimeConfig.CONFIG_FILE', str(tmp_path / 'runtime.json'))
    save_desired({'mode': 'active'})
    monkeypatch.setattr(service, 'repository', lambda: SimpleNamespace(control=lambda: None))
    with pytest.raises(RedisError):
        service.mode()


def test_worker_tuning_keeps_success_but_stricter_freshness_is_enforced():
    from dataclasses import replace
    policy = ValidationPolicy()
    tuned = replace(policy, workers=2, target_rps=1)
    assert tuned.version == policy.version and tuned.fingerprint != policy.fingerprint
    endpoint = ProxyEndpointV2.from_candidate(normalize_candidate('http://x:80'), 0)
    endpoint, _ = apply_results(endpoint, [evidence(policy)], policy, 'ok', 100)
    assert available_routes(endpoint, tuned, 101)
    shorter = replace(tuned, fresh_seconds=10)
    assert not available_routes(endpoint, shorter, 111)


def test_geonode_v2_preserves_socks_and_country(monkeypatch):
    from helper.validation import service
    import fetcher.sources.geonode as geonode
    payload = {'data': [
        {'ip': '1.2.3.4', 'port': 1080, 'protocols': ['socks5'], 'country': 'US'},
        {'ip': '1.2.3.5', 'port': 1080, 'protocols': ['socks4'], 'country': 'CN'}]}
    response = SimpleNamespace(last_error=None, json=payload, text='{}',
        response=SimpleNamespace(status_code=200, headers={'Content-Type': 'application/json'}, content=b'{}'))
    monkeypatch.setattr(service, 'mode', lambda: 'shadow')
    monkeypatch.setattr(geonode, 'WebRequest', lambda: SimpleNamespace(get=lambda *a, **k: response))
    records = list(geonode.GeonodeFetcher().fetch())
    assert [r['protocols'] for r in records] == [['socks5'], ['socks4']]
    assert [r['country'] for r in records] == ['US', 'CN']


def test_expired_references_cannot_leave_target_permanently_open():
    repo = SimpleNamespace(redis=fakeredis.FakeRedis(decode_responses=True), prefix='health:test:')
    health = TargetHealth(repo)
    repo.redis.set(repo.prefix + 'health:overseas', json.dumps({
        'state': 'open', 'window': {}, 'references': {'a': 100, 'b': 100, 'c': 100},
        'recovery': {}, 'next_canary': 1000, 'cooldown': 900}))
    assert health.allow('overseas', 'new-proxy', now=800)
    assert health.states()['overseas'] == 'unknown'


@pytest.mark.parametrize('body,country', [(b'{"country_code":"US"}', 'US'),
                                         (b'not-json', ''), (b'{}', '')])
def test_geo_lookup_tls_bounded_response_and_cleanup(body, country, monkeypatch):
    from unittest.mock import MagicMock
    import helper.validation.region as region
    response = MagicMock(status=200)
    response.read.return_value = body
    manager = MagicMock()
    manager.request.return_value = response
    factory = MagicMock(return_value=manager)
    monkeypatch.setattr(region.urllib3, 'PoolManager', factory)
    connection = MagicMock()
    region._lookup(connection, '198.51.100.1')
    connection.send.assert_called_once_with(country)
    factory.assert_called_once_with(cert_reqs='CERT_REQUIRED')
    response.read.assert_called_once_with(4097)
    response.close.assert_called_once()
    manager.clear.assert_called_once()
    connection.close.assert_called_once()


def test_half_open_schedules_a_distinct_unrecovered_reference():
    from unittest.mock import MagicMock
    redis = fakeredis.FakeRedis(decode_responses=True)
    repo = SimpleNamespace(redis=redis, prefix='health:test:', records='records', prioritize=MagicMock())
    refs = {'a': 150, 'b': 100, 'c': 100}
    redis.hset('records', mapping={key: '{}' for key in refs})
    redis.set(repo.prefix + 'health:overseas', json.dumps({'state': 'half_open',
        'window': {'a': {'time': 150, 'code': 'available'}, 'b': {'time': 100, 'code': 'target_restricted'}},
        'references': refs, 'recovery': {'a': 150}, 'next_canary': 100, 'cooldown': 60}))
    health = TargetHealth(repo)
    health.schedule(now=200)
    scheduled = repo.prioritize.call_args.args[0]
    assert len(scheduled) == 1 and scheduled[0] != 'a'
    health.schedule(now=201)
    repo.prioritize.assert_called_once()


def test_explicit_transport_does_not_erase_another_source_protocol():
    policy = ValidationPolicy()
    endpoint = ProxyEndpointV2.from_candidate(normalize_candidate('http://x:7897'), 100)
    endpoint.protocol_hints.append('socks5')
    assert policy.variants_for(endpoint) == ('http', 'socks5h')


def test_failed_previously_good_variant_still_gets_rechecked():
    policy = ValidationPolicy()
    endpoint = ProxyEndpointV2.from_candidate(normalize_candidate({'proxy': 'x:7897', 'protocols': ['http', 'socks5']}), 100)
    endpoint.attempted_variants = ['http', 'socks5', 'socks5h']
    endpoint.variants = {
        'http': {'targets': {'domestic': {'code': 'proxy_connect_failed', 'last_success': 100}}},
        'socks5h': {'targets': {'domestic': {'code': 'available', 'last_success': 100}}}}
    assert set(policy.variants_for(endpoint)) == {'http', 'socks5h'}


def test_restriction_backoff_increases_and_success_resets_it():
    policy = ValidationPolicy()
    endpoint = ProxyEndpointV2.from_candidate(normalize_candidate('http://x:80'), 0)
    for index, delay in enumerate((120, 300, 900)):
        endpoint, _ = apply_results(endpoint, [evidence(policy, 'target_restricted', now=100 + index)],
                                     policy, str(index), 100 + index)
        assert endpoint.variants['http']['targets']['overseas']['retry_at'] == 100 + index + delay
    endpoint, _ = apply_results(endpoint, [evidence(policy, now=200)], policy, 'ok', 200)
    assert endpoint.variants['http']['targets']['overseas']['restriction_count'] == 0
    assert endpoint.variants['http']['targets']['overseas']['retry_at'] == 0


def test_unavailable_variant_cools_then_can_recover_without_harming_other_protocols():
    policy = ValidationPolicy()
    endpoint = ProxyEndpointV2.from_candidate(normalize_candidate({'proxy': 'x:7897', 'protocols': ['http', 'socks5']}), 0)
    endpoint.attempted_variants = ['http', 'socks5h', 'socks5']
    endpoint.variants = {'http': {'hard_failures': 3, 'targets': {'overseas': {
        'code': 'proxy_connect_failed', 'last_success': 50, 'checked_at': 100, 'policy_version': policy.version}}},
        'socks5h': {'targets': {'overseas': {'code': 'available', 'last_success': 100}}}}
    assert policy.variants_for(endpoint, now=200) == ('socks5h',)
    assert set(policy.variants_for(endpoint, now=1001)) == {'http', 'socks5h'}


def test_reference_history_is_bounded():
    repo = SimpleNamespace(redis=fakeredis.FakeRedis(decode_responses=True), prefix='health:bounded:')
    health = TargetHealth(repo)
    for index in range(150):
        health.observe(str(index), evidence(ValidationPolicy()), now=100 + index * 0.01)
    assert len(health.read('overseas')['references']) == 128


def test_hint_conflicts_are_preserved_before_storage():
    candidate = normalize_candidate({'proxy': 'x:80', 'country': 'CN', 'country_hints': ['US']})
    assert candidate.country == '' and set(candidate.country_hints) == {'CN', 'US'}
    endpoint = ProxyEndpointV2.from_candidate(candidate, 100)
    assert endpoint.country_source == 'conflict'
    assert endpoint.country == ''


def test_geo_task_budget_stops_a_blocked_lookup(monkeypatch):
    import time
    import helper.validation.region as region
    monkeypatch.setattr(region, '_lookup', _blocking_geo_lookup)
    start = time.monotonic()
    assert region.lookup_country('198.51.100.1', deadline=0.7) == ''
    assert time.monotonic() - start < 2
