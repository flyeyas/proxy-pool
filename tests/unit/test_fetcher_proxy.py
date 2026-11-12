# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     test_fetcher_proxy.py
   Description :   已验证代理选择、直连回退、重试和采集线程隔离测试
   Author :        flyeyas
   date：          2026/11/12
-------------------------------------------------
   Change Activity:
                   2026/11/12: 新增按源采集代理策略测试
-------------------------------------------------
"""
__author__ = 'flyeyas'

import threading
from unittest.mock import MagicMock, patch

import pytest
from requests import Session
from requests.exceptions import ProxyError
from requests.models import Response
from requests.utils import select_proxy

import helper.fetcherProxy as proxy_mod
from helper.fetch import _ThreadFetcher
from helper.fetcherConfig import setFetcherUseProxy
from helper.proxy import Proxy
from fetcher.baseFetcher import BaseFetcher
from util.webRequest import WebRequest


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr('helper.runtimeConfig.CONFIG_FILE', str(tmp_path / 'runtime.json'))


@pytest.fixture
def pool(monkeypatch):
    handler = MagicMock()
    handler.getAll.return_value = []
    monkeypatch.setattr(proxy_mod, 'ProxyHandler', lambda: handler)
    monkeypatch.setattr(proxy_mod.random, 'choice', lambda values: values[0])
    return handler


def verified(address='192.0.2.1:8080', https=True):
    return Proxy(address, last_status=True, check_count=1, https=https)


def response():
    result = Response()
    result.status_code = 200
    result._content = b'ok'
    return result


def test_selects_only_last_successful_proxies(pool):
    pool.getAll.return_value = [Proxy('192.0.2.2:8080', last_status=False),
                                Proxy('192.0.2.3:8080'), verified()]
    route = proxy_mod.FetcherProxyRoute(True)
    assert route.proxiesFor('http://target.example')['http'] == 'http://192.0.2.1:8080'
    pool.getAll.assert_called_once_with(https=False)


def test_https_requires_https_validated_proxy_and_uses_connect(pool):
    pool.getAll.return_value = [verified('192.0.2.1:8080', https=False),
                                verified('192.0.2.2:8080')]
    proxies = proxy_mod.FetcherProxyRoute(True).proxiesFor('https://target.example')
    assert proxies['https'] == 'http://192.0.2.2:8080'
    pool.getAll.assert_called_once_with(https=True)


def test_empty_or_unsuitable_pool_falls_back_to_direct(pool):
    route = proxy_mod.FetcherProxyRoute(True)
    assert select_proxy('http://target.example', route.proxiesFor('http://target.example')) is None
    pool.getAll.return_value = [verified(https=False)]
    assert select_proxy('https://target.example', route.proxiesFor('https://target.example')) is None


def test_disabled_mode_never_reads_database(pool):
    route = proxy_mod.FetcherProxyRoute(False)
    assert select_proxy('https://target.example', route.proxiesFor('https://target.example')) is None
    pool.getAll.assert_not_called()


def test_direct_mode_ignores_environment_proxies(monkeypatch):
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
        monkeypatch.setenv(name, 'http://192.0.2.200:8888')
    with Session() as session:
        proxies = proxy_mod.FetcherProxyRoute(False).proxiesFor('https://target.example')
        settings = session.merge_environment_settings('https://target.example', proxies, False, True, None)
        assert select_proxy('https://target.example', settings['proxies']) is None


def test_reuses_proxy_for_cookie_requests_but_rechecks_pool(pool):
    pool.getAll.return_value = [verified(), verified('192.0.2.2:8080')]
    route = proxy_mod.FetcherProxyRoute(True)
    first = route.proxiesFor('https://target.example')
    assert route.proxiesFor('https://target.example/cookies') == first
    pool.getAll.return_value = []
    assert select_proxy('https://target.example', route.proxiesFor('https://target.example')) is None


def test_proxy_removed_from_pool_is_not_reused(pool):
    pool.getAll.return_value = [verified()]
    route = proxy_mod.FetcherProxyRoute(True)
    route.proxiesFor('http://target.example')
    pool.getAll.return_value = [verified('192.0.2.2:8080')]
    assert route.proxiesFor('http://target.example')['http'] == 'http://192.0.2.2:8080'


def test_database_error_falls_back_without_stopping_collection(pool):
    pool.getAll.side_effect = ConnectionError('unavailable')
    assert select_proxy('http://target.example',
        proxy_mod.FetcherProxyRoute(True).proxiesFor('http://target.example')) is None


@pytest.mark.parametrize('method', ['get', 'post'])
def test_requests_retry_with_another_verified_proxy(pool, method):
    pool.getAll.return_value = [verified(), verified('192.0.2.2:8080')]
    with patch('util.webRequest.requests.' + method, side_effect=[ProxyError('failed'), response()]) as send:
        with proxy_mod.fetcherProxyContext('TestFetcher'):
            result = getattr(WebRequest(), method)('http://target.example', retry_time=2, retry_interval=0)
    assert result.response.status_code == 200
    assert [call.kwargs['proxies']['http'] for call in send.call_args_list] == [
        'http://192.0.2.1:8080', 'http://192.0.2.2:8080']
    pool.delete.assert_not_called()


def test_explicit_validator_proxy_and_unscoped_requests_are_preserved(pool):
    supplied = {'http': 'http://192.0.2.9:8080'}
    with patch('util.webRequest.requests.get', return_value=response()) as send:
        with proxy_mod.fetcherProxyContext('TestFetcher'):
            WebRequest().get('http://target.example', proxies=supplied)
        WebRequest().get('http://target.example')
    assert send.call_args_list[0].kwargs['proxies'] is supplied
    assert 'proxies' not in send.call_args_list[1].kwargs
    pool.getAll.assert_not_called()


def test_shared_session_uses_the_same_proxy_and_preserves_cookies(pool):
    pool.getAll.return_value = [verified()]
    session = MagicMock()
    session.get.return_value = response()
    with proxy_mod.fetcherProxyContext('IhuanFetcher'):
        request = WebRequest(session=session)
        request.get('https://target.example')
        request.get('https://target.example/data')
    assert session.get.call_count == 2
    assert session.get.call_args_list[0].kwargs['proxies'] == session.get.call_args_list[1].kwargs['proxies']


def test_context_is_reset_after_error_and_nested_collection():
    assert proxy_mod.getCurrentFetcherProxyRoute() is None
    with pytest.raises(RuntimeError):
        with proxy_mod.fetcherProxyContext('FirstFetcher') as first:
            with proxy_mod.fetcherProxyContext('SecondFetcher'):
                pass
            assert proxy_mod.getCurrentFetcherProxyRoute() is first
            raise RuntimeError('fetch failed')
    assert proxy_mod.getCurrentFetcherProxyRoute() is None


def test_fetcher_threads_use_independent_runtime_switches(pool):
    pool.getAll.return_value = [verified()]
    barrier = threading.Barrier(2)
    calls = []
    lock = threading.Lock()
    class ProxyFetcher(BaseFetcher):
        name = 'proxy-source'
        def fetch(self):
            barrier.wait(timeout=5)
            WebRequest().get('http://target.example', retry_time=1)
            yield '198.51.100.1:8080'
    class DirectFetcher(ProxyFetcher):
        name = 'direct-source'
    setFetcherUseProxy('ProxyFetcher', True)
    setFetcherUseProxy('DirectFetcher', False)
    def send(url, **kwargs):
        with lock:
            calls.append((threading.current_thread().name, kwargs['proxies']['http']))
        return response()
    workers = [_ThreadFetcher(ProxyFetcher, {}), _ThreadFetcher(DirectFetcher, {})]
    workers[0].name, workers[1].name = 'proxied', 'direct'
    with patch('util.webRequest.requests.get', side_effect=send):
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=6)
            assert not worker.is_alive()
    assert set(calls) == {('proxied', 'http://192.0.2.1:8080'), ('direct', None)}
