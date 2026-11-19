"""Real local protocol probes, including TLS-in-TLS and remote DNS."""
import socket
import time
from dataclasses import replace
from urllib.parse import quote

import pytest

from helper.validation.normalize import normalize_candidate
from helper.validation.policy import TargetPolicy, ValidationPolicy
from helper.validation.transport import probe
from helper.validation.worker import validate_round
from tests.fixtures.proxy_network import certificates, proxy_service, target_service


def _blocked_dns_worker(connection, candidate, variants, policy_data, country):
    import socket
    from helper.validation.worker import _round
    original = socket.getaddrinfo
    def resolve(host, *args, **kwargs):
        if host == 'blocked-proxy.test':
            time.sleep(60)
        return original(host, *args, **kwargs)
    socket.getaddrinfo = resolve
    _round(connection, candidate, variants, policy_data, country)


def _orphan_controller(connection, candidate, policy_data):
    from helper.validation.worker import RoundWorker
    worker = RoundWorker(candidate, ('http',), ValidationPolicy.from_dict(policy_data), 'CN')
    connection.send(worker.process.pid)
    connection.close()
    while not worker.poll():
        time.sleep(0.01)


@pytest.fixture(scope='module')
def certs(tmp_path_factory):
    return certificates(tmp_path_factory.mktemp('validation-certs'))


def policy_for(port, ca, path='/ok', remote=False, **overrides):
    host = 'remote.test' if remote else 'localhost'
    target = TargetPolicy('https://%s:%d%s' % (host, port, path), (host,), ('google', 'tencent'))
    return ValidationPolicy(targets={'domestic': target, 'overseas': target},
                            target_ca=ca, proxy_ca=ca, retries=0, **overrides)


@pytest.mark.parametrize('variant', ['http', 'https', 'socks4', 'socks4a', 'socks5', 'socks5h'])
def test_real_https_through_every_protocol(certs, variant, monkeypatch):
    ca, tls = certs
    monkeypatch.setenv('ALL_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    remote = variant in ('http', 'https', 'socks4a', 'socks5h')
    kind = 'http' if variant in ('http', 'https') else 'socks5' if variant.startswith('socks5') else 'socks4'
    with target_service(tls) as (target_port, targets), proxy_service(kind, tls=tls if variant == 'https' else None) as (port, events):
        candidate = normalize_candidate('127.0.0.1:%d' % port)
        result = probe(candidate, variant, 'domestic', policy_for(target_port, ca, remote=remote))
        assert result.code == 'available', result
        assert events and events[0]['port'] == target_port
        if remote:
            assert events[0]['host'] == 'remote.test'
        assert targets and targets[0]['proxy_authorization'] is None


@pytest.mark.parametrize('variant', ['http', 'https', 'socks5h'])
def test_authentication(certs, variant):
    ca, tls = certs
    with target_service(tls) as (target_port, targets), proxy_service('socks5' if variant == 'socks5h' else 'http',
                        tls=tls if variant == 'https' else None, authentication=True) as (port, events):
        candidate = normalize_candidate('%s://user:pass@127.0.0.1:%d' % (variant, port), identity_key=b'x' * 32)
        result = probe(candidate, variant, 'overseas', policy_for(target_port, ca, remote=True))
        assert result.passed, result
        assert targets[0]['proxy_authorization'] is None
        bad = normalize_candidate('%s://user:wrong@127.0.0.1:%d' % (variant, port), identity_key=b'x' * 32)
        assert probe(bad, variant, 'overseas', policy_for(target_port, ca)).code in ('auth_failed', 'auth_required')


@pytest.mark.parametrize('path,code', [('/redirect', 'available'), ('/escape', 'target_restricted'),
                                      ('/captcha', 'target_restricted'), ('/limited', 'target_restricted'),
                                      ('/gzip', 'available'), ('/huge', 'available')])
def test_status_redirect_and_body_limits(certs, path, code):
    ca, tls = certs
    with target_service(tls) as (target_port, _), proxy_service('http') as (port, _):
        result = probe(normalize_candidate('127.0.0.1:%d' % port), 'http', 'domestic', policy_for(target_port, ca, path))
        assert result.code == code, result
        if path == '/limited':
            assert result.retry_after == 60


def test_tls_verification_is_not_disabled(certs):
    ca, tls = certs
    with target_service(tls) as (target_port, _), proxy_service('http') as (port, _):
        candidate = normalize_candidate('127.0.0.1:%d' % port)
        policy = replace(policy_for(target_port, ca), target_ca='')
        assert probe(candidate, 'http', 'domestic', policy).code == 'tls_invalid'


def test_proxy_certificate_verification_is_independent(certs):
    ca, tls = certs
    with target_service(tls) as (target_port, requests), proxy_service('http', tls=tls) as (port, _):
        candidate = normalize_candidate('127.0.0.1:%d' % port)
        policy = replace(policy_for(target_port, ca), proxy_ca='')
        result = probe(candidate, 'https', 'domestic', policy)
        assert result.code == 'tls_invalid' and result.stage == 'proxy_tls'
        assert requests == []


@pytest.mark.parametrize('variant', ['socks4', 'socks4a'])
def test_socks4_user_id_is_sent_without_claiming_password_auth(certs, variant):
    ca, tls = certs
    with target_service(tls) as (target_port, _), proxy_service('socks4', authentication=True) as (port, _):
        candidate = normalize_candidate('%s://user@127.0.0.1:%d' % (variant, port), identity_key=b'x' * 32)
        policy = policy_for(target_port, ca)
        assert probe(candidate, variant, 'domestic', policy).passed
        bad = normalize_candidate('%s://wrong@127.0.0.1:%d' % (variant, port), identity_key=b'x' * 32)
        assert not probe(bad, variant, 'domestic', policy).passed
        password = normalize_candidate('%s://user:pass@127.0.0.1:%d' % (variant, port), identity_key=b'x' * 32)
        assert probe(password, variant, 'domestic', policy).code == 'unsupported'


@pytest.mark.parametrize('variant', ['http', 'https', 'socks4', 'socks4a', 'socks5', 'socks5h'])
def test_percent_encoded_proxy_credentials_are_decoded_once(certs, variant):
    ca, tls = certs
    user = 'user%40name'
    password = 'pass:@/%word'
    kind = 'http' if variant in ('http', 'https') else 'socks4' if variant.startswith('socks4') else 'socks5'
    auth = quote(user, safe='')
    if not variant.startswith('socks4'):
        auth += ':' + quote(password, safe='')
    with target_service(tls) as (target_port, targets), proxy_service(
            kind, tls=tls if variant == 'https' else None, authentication=True,
            username=user, password=password) as (port, _):
        candidate = normalize_candidate('%s://%s@127.0.0.1:%d' % (variant, auth, port), identity_key=b'x' * 32)
        assert probe(candidate, variant, 'domestic', policy_for(target_port, ca)).passed
        assert targets[0]['proxy_authorization'] is None


@pytest.mark.skipif(not socket.has_ipv6, reason='IPv6 loopback unavailable')
@pytest.mark.parametrize('variant', ['socks5', 'socks5h'])
def test_real_socks5_ipv6_proxy_and_target(certs, variant):
    ca, tls = certs
    with target_service(tls, listen_host='::1') as (target_port, targets), proxy_service(
            'socks5', listen_host='::1', upstream_host='::1') as (port, events):
        target = TargetPolicy('https://[::1]:%d/ok' % target_port, ('::1',), ('google',))
        policy = ValidationPolicy(targets={'domestic': target, 'overseas': target},
                                  target_ca=ca, retries=0)
        candidate = normalize_candidate('%s://[::1]:%d' % (variant, port))
        assert probe(candidate, variant, 'domestic', policy).passed
        assert events[0]['host'] == '::1' and targets


def test_exit_identity_uses_the_validated_proxy(certs):
    ca, tls = certs
    with target_service(tls) as (target_port, targets), proxy_service('http') as (port, events):
        policy = policy_for(target_port, ca, exit_geo_url='https://localhost:%d/exit' % target_port)
        candidate = normalize_candidate('127.0.0.1:%d' % port)
        result = probe(candidate, 'http', 'exit_geo', policy)
        assert result.code == 'exit_verified'
        assert result.exit_ip == '198.51.100.24' and result.exit_country == 'US'
        assert events and targets[0]['path'] == '/exit'


def test_installed_hook_restricts_real_success(certs):
    ca, tls = certs
    with target_service(tls) as (target_port, _), proxy_service('http') as (port, _):
        policy = policy_for(target_port, ca, hooks=('tests.fixtures.validation_hooks:reject',))
        result = probe(normalize_candidate('127.0.0.1:%d' % port), 'http', 'domestic', policy)
        assert result.code == 'custom_rejected'


def test_explicit_reference_profile_runs_before_primary_without_changing_country(certs):
    ca, tls = certs
    with target_service(tls) as (target_port, _), proxy_service('http') as (port, _):
        candidate = replace(normalize_candidate('127.0.0.1:%d' % port), requested_profiles=('domestic',))
        results = validate_round(candidate, ('http',), policy_for(target_port, ca), 'US')
        assert [r['profile'] for r in results] == ['domestic', 'overseas']


def test_one_target_backoff_preserves_other_target_checks(certs):
    ca, tls = certs
    with target_service(tls) as (target_port, requests), proxy_service('http') as (port, _):
        candidate = replace(normalize_candidate('127.0.0.1:%d' % port),
                             profile_backoffs={'http|overseas': time.time() + 60})
        results = validate_round(candidate, ('http',), policy_for(target_port, ca), '')
        assert results[0]['profile'] == 'domestic' and results[0]['code'] == 'available'
        assert results[1]['profile'] == 'overseas' and results[1]['stage'] == 'retry_after'
        assert results[1]['attempts'] == 0
        assert len(requests) == 1


def test_spawn_worker_and_hard_deadline(certs):
    ca, tls = certs
    with target_service(tls) as (target_port, _), proxy_service('http') as (port, _):
        candidate = normalize_candidate('127.0.0.1:%d' % port)
        policy = policy_for(target_port, ca, '/slow', round_deadline=1.5)
        started = time.monotonic()
        results = validate_round(candidate, ('http',), policy, country='CN')
        assert time.monotonic() - started < 3.5
        assert results and results[0]['code'] == 'deadline_exceeded'


def test_unusable_candidate_cannot_fall_back_direct(certs, monkeypatch):
    ca, tls = certs
    with target_service(tls) as (target_port, targets):
        monkeypatch.setenv('NO_PROXY', '*')
        result = probe(normalize_candidate('127.0.0.1:1'), 'http', 'domestic', policy_for(target_port, ca))
        assert not result.passed
        assert targets == []


def test_hard_deadline_covers_blocked_dns(certs):
    from helper.validation.worker import RoundWorker
    ca, tls = certs
    with target_service(tls) as (target_port, _):
        policy = policy_for(target_port, ca, round_deadline=1)
        worker = RoundWorker(normalize_candidate('blocked-proxy.test:8080'), ('http',), policy,
                             'CN', target=_blocked_dns_worker)
        try:
            started = time.monotonic()
            while not worker.poll():
                time.sleep(0.01)
            assert time.monotonic() - started < 3
            assert worker.results[0]['code'] == 'deadline_exceeded'
            assert worker._closed
        finally:
            worker.close()


def test_hard_deadline_covers_stalled_proxy_tls(certs):
    ca, tls = certs
    with target_service(tls) as (target_port, _), proxy_service('stall') as (port, _):
        policy = policy_for(target_port, ca, round_deadline=1)
        started = time.monotonic()
        results = validate_round(normalize_candidate('127.0.0.1:%d' % port), ('https',), policy, 'CN')
        assert time.monotonic() - started < 3
        assert results and results[0]['code'] != 'available'


def test_wrong_proxy_scheme_is_rejected(certs):
    ca, tls = certs
    with target_service(tls) as (target_port, targets), proxy_service('http') as (port, _):
        policy = policy_for(target_port, ca, connect_timeout=0.1, read_timeout=0.1)
        result = probe(normalize_candidate('127.0.0.1:%d' % port), 'https', 'domestic', policy)
        assert not result.passed
        assert not targets


@pytest.mark.parametrize('variant,local', [('socks4', True), ('socks4a', False),
                                          ('socks5', True), ('socks5h', False)])
def test_dns_occurs_at_selected_side(certs, variant, local, monkeypatch):
    import socket
    ca, tls = certs
    seen = []
    getaddrinfo, gethostbyname = socket.getaddrinfo, socket.gethostbyname
    def addresses(host, *args, **kwargs):
        if host == 'remote.test':
            seen.append(host)
            host = '127.0.0.1'
        return getaddrinfo(host, *args, **kwargs)
    def address(host):
        if host == 'remote.test':
            seen.append(host)
            return '127.0.0.1'
        return gethostbyname(host)
    monkeypatch.setattr(socket, 'getaddrinfo', addresses)
    monkeypatch.setattr(socket, 'gethostbyname', address)
    with target_service(tls) as (target_port, _), proxy_service('socks5' if variant.startswith('socks5') else 'socks4') as (port, _):
        result = probe(normalize_candidate('127.0.0.1:%d' % port), variant, 'domestic', policy_for(target_port, ca, remote=True))
        assert result.passed, result
        assert bool(seen) == local


def test_parent_death_reaps_a_network_worker(certs):
    import multiprocessing
    import os
    ca, tls = certs
    context = multiprocessing.get_context('spawn')
    with target_service(tls) as (target_port, requests), proxy_service('http') as (port, _):
        policy = policy_for(target_port, ca, path='/slow')
        reader, writer = context.Pipe(duplex=False)
        controller = context.Process(target=_orphan_controller,
            args=(writer, normalize_candidate('127.0.0.1:%d' % port), policy.to_dict()))
        controller.start()
        writer.close()
        try:
            assert reader.poll(5)
            child_pid = reader.recv()
            until = time.monotonic() + 5
            while not requests and time.monotonic() < until:
                time.sleep(0.02)
            assert requests
            controller.terminate()
            controller.join(timeout=2)
            until = time.monotonic() + 3
            alive = True
            while alive and time.monotonic() < until:
                try:
                    # PID1 in the test container can reap an adopted grandchild.
                    waited, _ = os.waitpid(child_pid, os.WNOHANG)
                    if waited == child_pid:
                        alive = False
                        break
                except ChildProcessError:
                    pass
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    alive = False
                time.sleep(0.02)
            assert not alive
        finally:
            if controller.is_alive():
                controller.terminate()
                controller.join(timeout=2)
            controller.close()
            reader.close()
