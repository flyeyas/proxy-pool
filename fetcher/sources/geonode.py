# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     geonode.py
   Description :   Geonode代理源
   Author :        JHao
   date：          2026/11/18
-------------------------------------------------
   Change Activity:
                   2026/05/31:
                   2026/11/18: 记录响应诊断并过滤不支持的 SOCKS 代理
-------------------------------------------------
"""
__author__ = 'JHao'

import time

from fetcher.baseFetcher import BaseFetcher
from handler.logHandler import LogHandler
from util.webRequest import WebRequest

logger = LogHandler("fetcher")


class GeonodeFetcher(BaseFetcher):
    """Geonode Free Proxy https://geonode.com/"""

    name = "geonode"
    url = "https://geonode.com/"

    def fetch(self):
        self.last_error = None
        url = ("https://proxylist.geonode.com/api/proxy-list?"
               "filterLastChecked=10&page=1&limit=100&sort_by=lastChecked&sort_type=desc")
        started = time.monotonic()
        request = WebRequest()
        r = request.get(url, timeout=5, retry_time=2, retry_interval=1, verify=False)
        response = getattr(r, 'response', None)
        status = getattr(response, 'status_code', None)
        if not isinstance(status, int):
            status = None
        headers = getattr(response, 'headers', {}) or {}
        content_type = headers.get('Content-Type', '') if hasattr(headers, 'get') else ''
        if not isinstance(content_type, str):
            content_type = ''
        content = getattr(response, 'content', b'')
        body_bytes = len(content) if isinstance(content, (bytes, bytearray)) else len((r.text or '').encode())
        elapsed = time.monotonic() - started

        request_error = getattr(r, 'last_error', None)
        if isinstance(request_error, str) and request_error:
            self.last_error = 'request failed: %s' % request_error[:160]
            logger.warning('ProxyFetch - geonode: request failed; status=unavailable elapsed=%.2fs reason=%s',
                           elapsed, self.last_error)
            return
        if status is not None and not 200 <= status < 300:
            self.last_error = 'HTTP %d' % status
            retry_after = headers.get('Retry-After', '')
            logger.warning('ProxyFetch - geonode: upstream returned HTTP %d; content_type=%s body_bytes=%d retry_after=%s elapsed=%.2fs',
                           status, content_type or 'unknown', body_bytes, retry_after or 'none', elapsed)
            return

        try:
            payload = r.json
            records = payload.get('data') if isinstance(payload, dict) else None
            if isinstance(records, list):
                proxies = []
                unsupported = 0
                malformed = 0
                for item in records:
                    if not isinstance(item, dict):
                        malformed += 1
                        continue
                    protocols = item.get('protocols', [])
                    if isinstance(protocols, str):
                        protocols = [protocols]
                    if not isinstance(protocols, (list, tuple, set)):
                        protocols = []
                    protocols = {str(protocol).strip().lower() for protocol in protocols}
                    if not protocols.intersection({'http', 'https'}):
                        unsupported += 1
                        continue
                    ip, port = item.get('ip'), item.get('port')
                    if not ip or not port:
                        malformed += 1
                        continue
                    proxies.append('%s:%s' % (ip, port))
                unique = list(self.yieldUniqueProxies(proxies))
                logger.info('ProxyFetch - geonode: response status=%s content_type=%s body_bytes=%d total=%s records=%d http_candidates=%d emitted=%d unsupported_protocols=%d malformed=%d elapsed=%.2fs',
                            status if status is not None else 'unknown',
                            content_type or 'unknown', body_bytes, payload.get('total', 'unknown'),
                            len(records), len(proxies), len(unique), unsupported, malformed,
                            time.monotonic() - started)
                if not unique:
                    self.last_error = ('API returned %d records but no supported HTTP/HTTPS proxies'
                                       % len(records))
                for proxy in unique:
                    yield proxy
                return

            if payload or 'json' in content_type.lower():
                keys = sorted(payload) if isinstance(payload, dict) else []
                self.last_error = 'invalid API response: expected data list; keys=%s' % ','.join(keys[:12])
                logger.warning('ProxyFetch - geonode: invalid JSON schema; status=%s content_type=%s body_bytes=%d keys=%s elapsed=%.2fs',
                               status if status is not None else 'unknown', content_type,
                               body_bytes, ','.join(keys[:12]) or 'none', time.monotonic() - started)
                return

            proxies = self.parseProxiesFromText(r.text)
            unique = list(self.yieldUniqueProxies(proxies))
            logger.info('ProxyFetch - geonode: text fallback status=%s content_type=%s body_bytes=%d candidates=%d emitted=%d elapsed=%.2fs',
                        status if status is not None else 'unknown', content_type or 'unknown',
                        body_bytes, len(proxies), len(unique), time.monotonic() - started)
            if not unique:
                self.last_error = 'text response contained no proxy candidates'
            for proxy in unique:
                yield proxy
        except Exception as e:
            self.last_error = '%s: %s' % (type(e).__name__, e)
            logger.error('ProxyFetch - geonode: response parsing failed; status=%s content_type=%s body_bytes=%d elapsed=%.2fs reason=%s',
                         status if status is not None else 'unknown', content_type or 'unknown',
                         body_bytes, time.monotonic() - started, self.last_error, exc_info=True)


if __name__ == '__main__':
    for proxy in GeonodeFetcher().fetch():
        print(proxy)
