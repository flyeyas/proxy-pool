"""V2 authentication and response contracts backed by actual Redis."""
import pytest

from helper.validation import service
from helper.validation.config import save_desired
from tests.integration.test_validation_store import store, enqueue, result


@pytest.fixture
def v2(client, store, monkeypatch, tmp_path):
    monkeypatch.setattr('helper.runtimeConfig.CONFIG_FILE', str(tmp_path / 'runtime.json'))
    save_desired({'mode': 'shadow'})
    monkeypatch.setattr(service, 'repository', lambda: store[0])
    return client, store


def test_v2_list_random_and_pop(v2):
    client, store = v2
    repo, policy, control, owner = store
    candidate, task = enqueue(store)
    assert repo.commit(task, [result(policy)], policy, owner)
    response = client.get('/api/v2/proxies/')
    assert response.status_code == 200
    record = response.json['data']['proxies'][0]
    assert record['status'] == 'available'
    assert 'credential_ref' not in record
    response = client.get('/api/v2/proxies/random/?protocol=http')
    assert response.json['data']['proxy_url'] == 'http://1.2.3.4:80'
    assert client.post('/api/v2/proxies/pop/').status_code == 200
    assert client.post('/api/v2/proxies/pop/').status_code == 404


@pytest.mark.parametrize('query', ['protocol=ftp', 'dns_mode=remote', 'profile=cn',
                                   'page_size=201', 'max_latency_ms=nan', 'unknown=yes',
                                   'region=CHINA', 'status=garbage', 'page=0'])
def test_invalid_parameters(v2, query):
    client, _ = v2
    assert client.get('/api/v2/proxies/?' + query).status_code == 400


def test_new_paths_require_authentication(v2, monkeypatch):
    client, _ = v2
    monkeypatch.setenv('WEBUI_PASSWORD', 'test-secret')
    for route in ('/api/v2/proxies/', '/api/v2/validation/status/', '/api/v2/validation/config/'):
        assert client.get(route).status_code == 401


def test_token_cannot_modify_configuration(v2, monkeypatch):
    from helper.apiToken import generateToken
    client, _ = v2
    monkeypatch.setenv('WEBUI_PASSWORD', 'test-secret')
    token = generateToken('reader')['token']
    headers = {'Authorization': 'Bearer ' + token}
    assert client.get('/api/v2/validation/status/', headers=headers).status_code == 200
    assert client.get('/api/v2/proxies/random/', headers=headers).status_code == 403
    assert client.patch('/api/v2/validation/config/', headers=headers,
                         json={'policy': {'workers': 1}}).status_code == 403
    with client.session_transaction() as session:
        session['webui_login'] = True
    assert client.patch('/api/v2/validation/config/', json={'policy': {'workers': 2}}).status_code == 200


def test_mode_transition_cannot_bypass_migration(v2):
    client, _ = v2
    assert client.patch('/api/v2/validation/config/', json={'mode': 'active'}).status_code == 400


def test_latency_filter_does_not_consume_slow_proxy(v2):
    client, store = v2
    repo, policy, control, owner = store
    candidate, task = enqueue(store)
    record = result(policy)
    record['elapsed_ms'] = 3000
    assert repo.commit(task, [record], policy, owner)
    assert client.post('/api/v2/proxies/pop/?max_latency_ms=10').status_code == 404
    assert repo.load(candidate.endpoint_id) is not None


def test_diagnostic_protocol_filter_does_not_return_other_protocols(v2):
    client, store = v2
    enqueue(store)
    response = client.get('/api/v2/proxies/?status=all&protocol=socks5')
    assert response.status_code == 200
    assert response.json['data']['proxies'] == []


def test_active_legacy_api_only_exposes_http_connections(v2, monkeypatch):
    from handler.proxyHandler import ProxyHandler
    client, store = v2
    repo, policy, control, owner = store
    candidate, task = enqueue(store)
    assert repo.commit(task, [result(policy)], policy, owner)
    candidate, task = enqueue(store, 'socks5h://1.2.3.5:1080')
    assert repo.commit(task, [result(policy, variant='socks5h')], policy, owner)
    assert repo.update_control(control, {**control, 'mode': 'active'})
    monkeypatch.setattr('api.proxyApi.proxy_handler', ProxyHandler())
    response = client.get('/get/?type=https')
    assert response.status_code == 200 and response.json['data']['proxy'] == '1.2.3.4:80'
    response = client.get('/all/')
    assert response.json['data']['count'] == 1
    assert client.get('/pop/').json['data']['proxy'] == '1.2.3.4:80'
    assert client.get('/get/').status_code == 404
    assert client.get('/api/v2/proxies/random/?protocol=socks5').status_code == 200


def test_active_legacy_authenticated_pop_keeps_credentials_until_return(v2, monkeypatch):
    from handler.proxyHandler import ProxyHandler
    from helper.validation.config import identity_key
    from helper.validation.normalize import normalize_candidate
    client, store = v2
    repo, policy, control, owner = store
    candidate = normalize_candidate('http://user:pass@1.2.3.4:80', identity_key=identity_key())
    repo.submit(candidate, policy)
    task = repo.claim(owner, control)
    assert repo.commit(task, [result(policy)], policy, owner)
    assert repo.update_control(control, {**control, 'mode': 'active'})
    monkeypatch.setattr('api.proxyApi.proxy_handler', ProxyHandler())
    response = client.get('/pop/')
    assert response.status_code == 200
    assert response.json['data']['proxy'] == 'user:pass@1.2.3.4:80'
    assert repo.redis.hget(repo.credentials_key, candidate.credential_ref) is None


def test_recheck_job_reports_async_completion(v2):
    import time
    client, store = v2
    response = client.post('/api/v2/validation/jobs/', json={'action': 'recheck'})
    assert response.status_code == 202
    job_id = response.json['data']['job_id']
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        status = client.get('/api/v2/validation/jobs/' + job_id + '/').json['data']
        if status['state'] == 'complete':
            break
        time.sleep(0.02)
    assert status['state'] == 'complete'


def test_targeted_recheck_merges_duplicate_ids_and_keeps_target_demand(v2):
    import time
    client, store = v2
    repo, policy, control, owner = store
    candidate, task = enqueue(store)
    response = client.post('/api/v2/validation/jobs/', json={'action': 'recheck',
        'endpoint_ids': [candidate.endpoint_id, candidate.endpoint_id], 'profiles': ['domestic']})
    assert response.status_code == 202
    job_id = response.json['data']['job_id']
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        data = client.get('/api/v2/validation/jobs/' + job_id + '/').json['data']
        if data['state'] == 'complete':
            break
        time.sleep(0.02)
    assert data['result']['queued'] == 1
    assert repo.load(candidate.endpoint_id).pending_profiles == ['domestic']
