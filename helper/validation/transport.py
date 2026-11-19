"""Explicit proxy-only HTTPS probes. No environment routing or direct fallback."""
import ipaddress
import json
import ssl
import time
import zlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import urllib3
from urllib3 import exceptions as ue
from urllib3.contrib.socks import SOCKSProxyManager

from .models import TargetResult, TRANSIENT
from .policy import TargetPolicy
from .hooks import apply_hooks

HEADERS = {'User-Agent': 'ProxyPool-Validator/2.0', 'Accept': 'text/html,application/xhtml+xml',
           'Accept-Encoding': 'identity', 'Connection': 'close'}


def exception_result(exc):
    """Return bounded classifications, never leak a credential-bearing exception."""
    current = exc
    for _ in range(6):
        nested = getattr(current, 'reason', None) or getattr(current, 'original_error', None)
        if nested is None or nested is current:
            break
        current = nested
    detail = str(current).lower()
    if '407' in detail or 'authentication' in detail or 'auth failed' in detail:
        return 'auth_failed', 'proxy_auth'
    if isinstance(current, (ssl.SSLError, ue.SSLError)) or isinstance(exc, ue.SSLError):
        return 'tls_invalid', 'proxy_tls' if isinstance(exc, ue.ProxyError) else 'target_tls'
    if isinstance(current, ue.NameResolutionError) or 'name resolution' in detail or 'getaddrinfo' in detail:
        return 'environment_error', 'dns'
    if isinstance(current, ue.ReadTimeoutError) or isinstance(exc, ue.ReadTimeoutError):
        return 'read_timeout', 'target_read'
    if isinstance(current, ue.ConnectTimeoutError) and not isinstance(current, ue.NewConnectionError):
        return 'read_timeout', 'connect_or_tunnel'
    if isinstance(current, (ue.NewConnectionError, ConnectionRefusedError)) or 'connection refused' in detail:
        return 'proxy_connect_failed', 'proxy_connect'
    if 'socks' in detail and any(s in detail for s in ('invalid', 'version', 'unexpected')):
        return 'protocol_error', 'proxy_handshake'
    return 'transport_error', 'connect_or_tunnel'


def retry_delay(value):
    if not value:
        return 0
    try:
        return min(86400, max(0, float(value)))
    except (ValueError, TypeError):
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return min(86400, max(0, (dt - datetime.now(timezone.utc)).total_seconds()))
        except (ValueError, TypeError, OverflowError):
            return 0


def bounded_body(response, limit):
    """Bound both compressed input and decompressed output, including zip bombs."""
    encoding = response.headers.get('Content-Encoding', 'identity').strip().lower()
    if encoding not in ('identity', '', 'gzip', 'deflate'):
        return b'', False
    decoder = zlib.decompressobj(31 if encoding == 'gzip' else 15) if encoding in ('gzip', 'deflate') else None
    output = bytearray()
    wire = 0
    for chunk in response.stream(amt=4096, decode_content=False):
        wire += len(chunk)
        if wire > limit * 2:
            break
        remaining = limit - len(output)
        part = decoder.decompress(chunk, remaining + 1) if decoder else chunk
        output.extend(part[:remaining])
        if len(part) > remaining or len(output) >= limit:
            break
    return bytes(output), True


def classify_response(status, body, url, target):
    text = body.decode('utf-8', errors='replace').lower()
    host = urlsplit(url).hostname or ''
    if status in (401, 403, 429) or '/sorry/' in urlsplit(url).path or 'consent.' in host:
        return 'target_restricted'
    if status == 407:
        return 'auth_required'
    if status != 200:
        return 'target_http_error'
    if any(marker.lower() in text for marker in target.negative):
        return 'target_restricted'
    if any(marker.lower() in text for marker in target.positive):
        return 'available'
    return 'content_inconclusive'


def probe(candidate, variant, profile, policy, deadline=None, permit=None):
    start = time.monotonic()
    deadline = deadline if deadline is not None else start + policy.round_deadline
    if profile in ('http_only', 'exit_geo'):
        url = policy.http_probe_url if profile == 'http_only' else policy.exit_geo_url
        target = TargetPolicy(url, (urlsplit(url).hostname,), ())
    else:
        target = policy.targets[profile]
    result = TargetResult(variant, profile, 'deferred', 'budget', checked_at=time.time(),
                          policy_version=policy.version)
    if variant in ('socks4', 'socks4a'):
        if candidate.password is not None:
            result.code, result.stage = 'unsupported', 'authentication'
            return result
        try:
            if ipaddress.ip_address(candidate.host).version == 6:
                result.code, result.stage = 'unsupported', 'address_family'
                return result
        except ValueError:
            pass
    manager = None
    try:
        tls = ssl.create_default_context(cafile=policy.target_ca or None)
        options = {'ssl_context': tls, 'retries': False, 'maxsize': 1, 'block': True}
        if variant.startswith('socks'):
            manager = SOCKSProxyManager(candidate.proxy_url(variant, credentials=True), **options)
        else:
            # Proxy auth is sent only to the proxy, never as an origin header.
            proxy_headers = urllib3.make_headers(proxy_basic_auth='%s:%s' % (candidate.username, candidate.password or '')) if candidate.username is not None else None
            proxy_url = candidate.proxy_url(variant)
            if variant == 'https':
                options['proxy_ssl_context'] = ssl.create_default_context(cafile=policy.proxy_ca or None)
            manager = urllib3.ProxyManager(proxy_url, proxy_headers=proxy_headers, **options)
        for attempt in range(policy.retries + 1):
            result.attempts = attempt + 1
            url = target.url
            try:
                for redirects in range(policy.max_redirects + 1):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        result.code, result.stage = 'deferred', 'budget'
                        return result
                    if permit is not None:
                        waiting = time.monotonic()
                        granted = permit(urlsplit(url).hostname)
                        result.queue_wait_ms += int((time.monotonic() - waiting) * 1000)
                        if not granted:
                            result.code, result.stage = 'deferred', 'rate_limit'
                            return result
                    timeout = urllib3.Timeout(connect=min(policy.connect_timeout, remaining),
                                              read=min(policy.read_timeout, remaining))
                    result.status_code = None
                    response = manager.request('GET', url, headers=HEADERS, timeout=timeout,
                                               retries=False, redirect=False, preload_content=False)
                    try:
                        result.status_code = response.status
                        result.redirects = redirects
                        result.retry_after = retry_delay(response.headers.get('Retry-After'))
                        if profile == 'http_only':
                            result.code = 'available' if response.status in (200, 204) else classify_response(response.status, b'', url, target)
                            result.stage = 'http_capability'
                            return result
                        if profile == 'exit_geo':
                            result.code, result.stage = 'content_inconclusive', 'exit_identity'
                            if response.status == 200:
                                body, supported = bounded_body(response, 4096)
                                try:
                                    identity = json.loads(body)
                                    ip = str(ipaddress.ip_address(identity.get('ip', '')))
                                    country = identity.get('country_code', '').upper()
                                    if supported and len(country) == 2 and country.isascii() and country.isalpha():
                                        result.exit_ip, result.exit_country = ip, country
                                        result.code = 'exit_verified'
                                except (ValueError, TypeError, AttributeError):
                                    pass
                            return result
                        if response.status in (301, 302, 303, 307, 308):
                            location = response.headers.get('Location', '')
                            next_url = urljoin(url, location)
                            parsed = urlsplit(next_url)
                            if not location or parsed.scheme != 'https' or parsed.hostname not in target.allowed_hosts or parsed.username or parsed.password or (parsed.port or 443) != (urlsplit(target.url).port or 443):
                                result.code, result.stage = 'target_restricted', 'redirect'
                                return result
                            if redirects == policy.max_redirects:
                                result.code, result.stage = 'redirect_limit', 'redirect'
                                return result
                            url = next_url
                            continue
                        if response.status != 200:
                            result.code = classify_response(response.status, b'', url, target)
                        else:
                            body, supported = bounded_body(response, policy.response_limit)
                            result.code = classify_response(response.status, body, url, target) if supported else 'content_inconclusive'
                        result.stage = 'proxy_auth' if result.code == 'auth_required' else 'target_response'
                        result.elapsed_ms = max(0, int((time.monotonic() - start) * 1000) - result.queue_wait_ms)
                        return apply_hooks(candidate, result, policy)
                    finally:
                        response.close()
                        response.release_conn()
            except (urllib3.exceptions.HTTPError, OSError, zlib.error) as exc:
                result.code, result.stage = exception_result(exc)
                if result.code not in TRANSIENT or attempt >= policy.retries:
                    return result
                if deadline - time.monotonic() < 0.15:
                    return result
                time.sleep(0.1)
        return result
    except (ValueError, TypeError, OSError):
        result.code, result.stage = 'environment_error', 'configuration'
        return result
    finally:
        result.checked_at = time.time()
        result.elapsed_ms = max(0, int((time.monotonic() - start) * 1000) - result.queue_wait_ms)
        if manager is not None:
            manager.clear()
