# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     fetcherProxy.py
   Description :   每个采集线程独立的已验证代理选择与直连回退
   Author :        flyeyas
   date：          2026/11/12
-------------------------------------------------
   Change Activity:
                   2026/11/12: HTTP/HTTPS 适配、失败换代理与线程隔离
-------------------------------------------------
"""
__author__ = 'flyeyas'

import random
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import urlsplit

from handler.proxyHandler import ProxyHandler
from handler.logHandler import LogHandler
from helper.fetcherConfig import getFetcherUseProxy

_CURRENT_ROUTE = ContextVar('fetcher_proxy_route', default=None)


class FetcherProxyRoute:
    def __init__(self, use_proxy):
        self.use_proxy = use_proxy
        self._selected = {}
        self._failed = {'http': set(), 'https': set()}
        self._handler = None
        self._db_error_logged = False

    def proxiesFor(self, url):
        scheme = urlsplit(url).scheme.lower()
        direct = {'http': None, 'https': None, 'all': None, 'no_proxy': '*'}
        if not self.use_proxy or scheme not in ('http', 'https'):
            return direct
        try:
            if self._handler is None:
                self._handler = ProxyHandler()
            candidates = self._handler.getAll(https=scheme == 'https')
            available = {proxy.proxy for proxy in candidates
                         if proxy.last_status is True and proxy.proxy
                         and (scheme != 'https' or proxy.https is True)}
            self._db_error_logged = False
        except Exception:
            if not self._db_error_logged:
                LogHandler('fetcher').warning('无法读取已验证代理池, 本轮请求直连采集')
            self._db_error_logged = True
            return direct
        if not available:
            self._selected.pop(scheme, None)
            return direct
        choices = available - self._failed[scheme]
        if not choices:
            self._failed[scheme].clear()
            choices = available
        selected = self._selected.get(scheme)
        if selected not in choices:
            selected = random.choice(sorted(choices))
            self._selected[scheme] = selected
        # HTTPS 目标通过已验证支持 CONNECT 的 HTTP 代理隧道访问。
        proxy_url = 'http://' + selected
        return {'http': proxy_url, 'https': proxy_url, 'all': None, 'no_proxy': '*'}

    def failed(self, url):
        scheme = urlsplit(url).scheme.lower()
        selected = self._selected.pop(scheme, None)
        if selected is not None:
            self._failed[scheme].add(selected)


def getCurrentFetcherProxyRoute():
    return _CURRENT_ROUTE.get()


@contextmanager
def fetcherProxyContext(class_name):
    route = FetcherProxyRoute(getFetcherUseProxy(class_name))
    token = _CURRENT_ROUTE.set(route)
    try:
        yield route
    finally:
        _CURRENT_ROUTE.reset(token)
