"""Use an actual isolated Redis namespace, not fakeredis, for Lua/lease races."""
from concurrent.futures import ThreadPoolExecutor
import os
import time
from uuid import uuid4

import pytest

from helper.validation.models import TargetResult
from helper.validation.normalize import normalize_candidate
from helper.validation.policy import ValidationPolicy
from helper.validation.repository import ValidationRepository


@pytest.fixture
def store():
    url = os.getenv('V2_TEST_REDIS_URL')
    if not url:
        pytest.skip('set V2_TEST_REDIS_URL to run real Redis 8 contract tests')
    repo = ValidationRepository(url, 'test_validation_' + uuid4().hex)
    repo.redis.ping()
    policy = ValidationPolicy(retries=0)
    control = repo.initialize('shadow', policy)
    owner = uuid4().hex
    assert repo.acquire_leader(owner)
    yield repo, policy, control, owner
    keys = list(repo.redis.scan_iter(repo.table + '*'))
    if keys:
        repo.redis.delete(*keys)
    repo.redis.close()


def enqueue(store, candidate='http://1.2.3.4:80', now=None):
    repo, policy, control, owner = store
    now = time.time() if now is None else now
    c = normalize_candidate(candidate, source='test')
    assert repo.submit(c, policy, now)
    task = repo.claim(owner, control, now)
    assert task
    return c, task


def result(policy, code='available', now=None, variant='http', profile='overseas'):
    return TargetResult(variant, profile, code, 'test', checked_at=time.time() if now is None else now,
                        policy_version=policy.version).to_dict()


def test_source_merge_and_single_claim(store):
    repo, policy, control, owner = store
    c, task = enqueue(store)
    other = normalize_candidate({'proxy': c.address, 'source': 'second', 'protocols': ['socks5']})
    assert repo.submit(other, policy) == 2
    assert repo.claim(owner, control) is None
    assert repo.commit(task, [result(policy)], policy, owner)
    saved = repo.load(c.endpoint_id)
    assert set(saved.sources) == {'test', 'second'}
    assert 'socks5' in saved.protocol_hints
    assert saved.discovered == ['http']


def test_late_commit_cannot_revive_deleted_endpoint(store):
    repo, policy, control, owner = store
    c, task = enqueue(store)
    assert repo.remove(c.endpoint_id)
    assert not repo.commit(task, [result(policy)], policy, owner)
    assert repo.load(c.endpoint_id) is None
    repo.submit(c, policy)
    assert not repo.commit(task, [result(policy)], policy, owner)
    assert repo.load(c.endpoint_id).variants == {}


def test_lost_leader_cannot_commit(store):
    repo, policy, control, owner = store
    c, task = enqueue(store)
    repo.release_leader(owner)
    assert repo.acquire_leader('new-owner')
    assert not repo.commit(task, [result(policy)], policy, owner)


def test_expired_lease_and_policy_epoch_reject_commit(store):
    repo, policy, control, owner = store
    c, task = enqueue(store)
    repo.redis.delete(repo.keys(c.endpoint_id)[2])
    assert not repo.commit(task, [result(policy)], policy, owner)


def test_expired_results_are_not_served(store):
    repo, policy, control, owner = store
    c, task = enqueue(store, now=100)
    assert repo.commit(task, [result(policy, now=100)], policy, owner, now=100)
    assert repo.route(policy, now=101)
    assert repo.route(policy, now=701) is None


def test_overseas_qq_is_not_default_overseas_success(store):
    repo, policy, control, owner = store
    c, task = enqueue(store, {'proxy': 'http://1.2.3.4:80', 'country': 'US'})
    assert repo.commit(task, [result(policy, profile='domestic')], policy, owner)
    assert repo.route(policy) is None
    assert repo.route(policy, profile='domestic')


def test_concurrent_pop_returns_endpoint_only_once(store):
    repo, policy, control, owner = store
    c, task = enqueue(store)
    assert repo.commit(task, [result(policy)], policy, owner)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: repo.route(policy, consume=True), range(16)))
    assert sum(x is not None for x in results) == 1
    assert repo.load(c.endpoint_id) is None


def test_global_permit_is_shared_and_bounded(store):
    repo, policy, control, owner = store
    with ThreadPoolExecutor(max_workers=10) as executor:
        permits = list(executor.map(lambda _: repo.permit('target.test', 2), range(10)))
    assert sum(permits) == 1


def test_hard_failure_count_is_round_not_attempt_and_success_resets(store):
    repo, policy, control, owner = store
    c, task = enqueue(store, now=100)
    assert repo.commit(task, [result(policy, 'proxy_connect_failed', now=100)], policy, owner, now=100)
    assert not repo.commit(task, [result(policy, 'proxy_connect_failed', now=100)], policy, owner, now=100)
    assert repo.load(c.endpoint_id).variants['http']['hard_failures'] == 1
    task = repo.claim(owner, control, now=221)
    assert repo.commit(task, [result(policy, now=221)], policy, owner, now=221)
    assert repo.load(c.endpoint_id).variants['http']['hard_failures'] == 0


def test_target_restriction_does_not_count_as_connection_failure(store):
    repo, policy, control, owner = store
    c, task = enqueue(store)
    assert repo.commit(task, [result(policy, 'target_restricted')], policy, owner)
    assert repo.load(c.endpoint_id).variants['http']['hard_failures'] == 0


def test_ssdb_v2_is_explicitly_unsupported():
    with pytest.raises(ValueError, match='SSDB'):
        ValidationRepository('ssdb://localhost:8888', 'test')


def test_credentials_reference_counts_and_consumption(store):
    repo, policy, control, owner = store
    a = normalize_candidate('http://user:pass@1.2.3.4:80', identity_key=b'x' * 32)
    b = normalize_candidate('http://user:pass@1.2.3.5:80', identity_key=b'x' * 32)
    repo.submit(a, policy)
    repo.submit(b, policy)
    task = repo.claim(owner, control)
    assert repo.commit(task, [result(policy)], policy, owner)
    route = repo.route(policy, consume=True)
    assert repo.credentials(route[0]) == {'username': 'user', 'password': 'pass'}
    assert repo.redis.hget(repo.credentials_key, a.credential_ref) is not None
    remaining = b if route[0].endpoint_id == a.endpoint_id else a
    assert repo.remove(remaining.endpoint_id)
    assert repo.redis.hget(repo.credentials_key, a.credential_ref) is None


def test_legacy_failure_does_not_delete_shadow_v2_or_active_projection(store):
    from helper.proxy import Proxy
    repo, policy, control, owner = store
    c, task = enqueue(store)
    assert repo.commit(task, [result(policy)], policy, owner)
    proxy = Proxy('1.2.3.4:80')
    repo.redis.hset(repo.table, proxy.proxy, proxy.to_json)
    assert repo.guarded_legacy_delete(proxy)
    assert repo.load(c.endpoint_id) is not None
    active = {**control, 'mode': 'active'}
    assert repo.update_control(control, active)
    repo.redis.hset(repo.table, proxy.proxy, proxy.to_json)
    assert not repo.guarded_legacy_delete(proxy)
    assert repo.redis.hexists(repo.table, proxy.proxy)


def test_region_filter_uses_index_beyond_first_global_page(store):
    repo, policy, control, owner = store
    for number in range(1, 112):
        country = 'US' if number == 111 else 'HK'
        candidate = normalize_candidate({'proxy': 'http://198.51.100.%d:80' % number, 'country': country})
        repo.submit(candidate, policy, now=100)
        task = repo.claim(owner, control, now=100)
        assert repo.commit(task, [result(policy, now=100)], policy, owner, now=100)
    selected = repo.route(policy, region='US', now=101)
    assert selected is not None and selected[0].country == 'US'


def test_drain_fences_pop_and_delete(store):
    repo, policy, control, owner = store
    candidate, task = enqueue(store)
    assert repo.commit(task, [result(policy)], policy, owner)
    assert repo.update_control(control, {**control, 'draining': True})
    assert repo.remove(candidate.endpoint_id) is False
    assert repo.route(policy, consume=True) is None
    assert repo.load(candidate.endpoint_id) is not None


def test_new_candidates_receive_reserved_share_without_starving_rechecks(store):
    repo, policy, control, owner = store
    for number in range(1, 21):
        candidate = normalize_candidate('http://198.51.100.%d:80' % number)
        repo.submit(candidate, policy, now=100)
        task = repo.claim(owner, control, now=100)
        assert repo.commit(task, [result(policy, now=100)], policy, owner, now=100)
    repo.reschedule_all()
    for number in range(21, 41):
        repo.submit(normalize_candidate('http://198.51.100.%d:80' % number), policy)
    repo.redis.set(repo.prefix + 'queue_cursor', '0')
    tasks = [repo.claim(owner, control) for _ in range(20)]
    assert all(tasks)
    assert [task['queue'] for task in tasks].count('new') == 4
    assert [task['queue'] for task in tasks].count('use') == 16


def test_success_and_delete_move_and_clear_partition_indexes(store):
    repo, policy, control, owner = store
    candidate, task = enqueue(store)
    assert repo.redis.zscore(repo.new_due, candidate.endpoint_id) is not None
    assert repo.commit(task, [result(policy)], policy, owner)
    assert repo.redis.zscore(repo.new_due, candidate.endpoint_id) is None
    assert repo.redis.zscore(repo.use_due, candidate.endpoint_id) is not None
    assert repo.remove(candidate.endpoint_id)
    assert repo.redis.zscore(repo.use_due, candidate.endpoint_id) is None


def test_requested_target_merges_during_flight_and_runs_next(store):
    repo, policy, control, owner = store
    candidate, task = enqueue(store)
    assert repo.prioritize([candidate.endpoint_id], profiles=('domestic',)) == 1
    assert repo.commit(task, [result(policy, profile='overseas')], policy, owner)
    pending = repo.load(candidate.endpoint_id)
    assert pending.pending_profiles == ['domestic']
    next_task = repo.claim(owner, control, now=time.time() + 2)
    assert next_task is not None
    assert repo.commit(next_task, [result(policy, profile='domestic')], policy, owner)
    assert repo.load(candidate.endpoint_id).pending_profiles == []


def test_duplicate_source_keeps_due_but_new_protocol_advances_it(store):
    repo, policy, control, owner = store
    candidate, task = enqueue(store, now=100)
    assert repo.commit(task, [result(policy, now=100)], policy, owner, now=100)
    due = repo.redis.zscore(repo.due, candidate.endpoint_id)
    repo.submit(candidate, policy, now=110)
    assert repo.redis.zscore(repo.due, candidate.endpoint_id) == due
    updated = normalize_candidate({'proxy': candidate.address, 'protocols': ['socks5']})
    repo.submit(updated, policy, now=110)
    assert repo.redis.zscore(repo.due, candidate.endpoint_id) == 110


def test_conflicting_country_hints_become_unknown_until_stronger_lookup(store):
    repo, policy, control, owner = store
    a = normalize_candidate({'proxy': '1.2.3.4:80', 'country': 'CN'})
    b = normalize_candidate({'proxy': '1.2.3.4:80', 'country': 'US'})
    repo.submit(a, policy)
    repo.submit(b, policy)
    endpoint = repo.load(a.endpoint_id)
    assert endpoint.country == '' and endpoint.country_source == 'conflict'
    assert set(endpoint.country_hints) == {'CN', 'US'}
    assert repo.update_region(a.endpoint_id, 'US')
    repo.submit(a, policy)
    assert repo.load(a.endpoint_id).country_source == 'ip_lookup'
    assert repo.load(a.endpoint_id).country == 'US'
