"""Migration coverage must represent a completed primary HTTPS check."""
import json
from dataclasses import replace

import pytest

from helper.validation.config import save_desired
from helper.validation.migration import activate
from tests.integration.test_validation_store import store, enqueue, result


def configure(store, tmp_path, monkeypatch, **options):
    repo, policy, control, owner = store
    monkeypatch.setattr('helper.runtimeConfig.CONFIG_FILE', str(tmp_path / 'runtime.json'))
    if options:
        policy = replace(policy, **options)
        updated = {**control, 'policy': policy.to_dict(), 'policy_version': policy.version,
                   'config_version': policy.fingerprint}
        assert repo.update_control(control, updated)
        control = updated
    save_desired({'mode': 'shadow', 'policy': policy.to_dict()})
    good, task = enqueue((repo, policy, control, owner))
    assert repo.commit(task, [result(policy)], policy, owner)
    return (repo, policy, control, owner), good


def assert_uncovered(store, good, candidate):
    repo, policy, control, owner = store
    values = {c.address: json.dumps({'proxy': c.address, 'region': c.country})
              for c in (good, candidate)}
    repo.redis.hset(repo.table, mapping=values)
    with pytest.raises(ValueError, match='coverage'):
        activate(repo, policy, minimum=1)
    assert repo.control()['mode'] == 'shadow'
    assert repo.redis.hgetall(repo.table) == values
    assert not list(repo.redis.scan_iter(repo.table + ':rollback:*'))


@pytest.mark.parametrize('attempt_code', ['deferred', 'environment_error', 'unsupported'])
def test_inconclusive_pending_placeholder_is_not_completed_coverage(store, tmp_path, monkeypatch, attempt_code):
    configured, good = configure(store, tmp_path, monkeypatch)
    repo, policy, control, owner = configured
    candidate, task = enqueue(configured, '9.9.9.9:80')
    attempt = result(policy)
    attempt['code'] = attempt_code
    assert repo.commit(task, [attempt], policy, owner)
    saved = repo.load(candidate.endpoint_id).variants['http']['targets'][attempt['profile']]
    assert saved['code'] == 'pending' and saved['last_attempt']['code'] == attempt_code
    assert_uncovered(configured, good, candidate)


@pytest.mark.parametrize('code', ['unknown', 'future_result', None, 'deadline_exceeded'])
def test_unfinished_or_unrecognized_result_does_not_grant_migration_coverage(store, tmp_path, monkeypatch, code):
    configured, good = configure(store, tmp_path, monkeypatch)
    repo, policy, control, owner = configured
    candidate, task = enqueue(configured, '9.9.9.9:80')
    attempt = result(policy)
    attempt['code'] = code
    assert repo.commit(task, [attempt], policy, owner)
    assert_uncovered(configured, good, candidate)


def test_http_only_success_cannot_replace_primary_https_coverage(store, tmp_path, monkeypatch):
    configured, good = configure(store, tmp_path, monkeypatch, http_probe_url='http://example.test/probe')
    repo, policy, control, owner = configured
    candidate, task = enqueue(configured, '9.9.9.9:80')
    attempt = result(policy)
    attempt['profile'] = 'http_only'
    assert repo.commit(task, [attempt], policy, owner)
    assert_uncovered(configured, good, candidate)


def test_secondary_profile_success_cannot_replace_known_country_primary(store, tmp_path, monkeypatch):
    configured, good = configure(store, tmp_path, monkeypatch)
    repo, policy, control, owner = configured
    candidate, task = enqueue(configured, {'proxy': '9.9.9.9:80', 'country': 'CN'})
    attempt = result(policy)
    attempt['profile'] = 'overseas'
    assert repo.commit(task, [attempt], policy, owner)
    assert_uncovered(configured, good, candidate)


@pytest.mark.parametrize('code', ['proxy_connect_failed', 'target_restricted', 'read_timeout'])
def test_conclusive_main_failure_counts_as_checked_without_becoming_available(store, tmp_path, monkeypatch, code):
    configured, good = configure(store, tmp_path, monkeypatch)
    repo, policy, control, owner = configured
    candidate, task = enqueue(configured, '9.9.9.9:80')
    attempt = result(policy)
    attempt['code'] = code
    assert repo.commit(task, [attempt], policy, owner)
    values = {c.address: json.dumps({'proxy': c.address, 'region': c.country}) for c in (good, candidate)}
    repo.redis.hset(repo.table, mapping=values)
    activation = activate(repo, policy, minimum=1)
    assert activation['completed'] == activation['legacy_completed'] == 2
    assert activation['compatible_available'] == 1
    assert set(repo.redis.hkeys(repo.table)) == {good.address}
    assert repo.redis.hgetall(activation['rollback_hash']) == values
