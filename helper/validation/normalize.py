"""Strict endpoint parsing with stable keyed credential identities."""
import hashlib
import hmac
import ipaddress
import re
from urllib.parse import unquote, urlsplit

from .models import ProxyCandidate, VARIANTS


def normalize_candidate(value, source='', identity_key=None):
    if isinstance(value, ProxyCandidate):
        return value
    if isinstance(value, str):
        value = {'proxy': value}
    if not isinstance(value, dict):
        raise ValueError('candidate must be an address or object')
    raw = value.get('proxy') or value.get('url')
    declared = value.get('protocols', value.get('declared_protocols', ()))
    if isinstance(declared, str):
        declared = [declared]
    if not isinstance(declared, (list, tuple, set)):
        raise ValueError('invalid declared protocols')
    protocols = [str(p).strip().lower() for p in declared]
    username = password = None
    scheme = ''
    if raw:
        if not isinstance(raw, str) or any(c.isspace() for c in raw.strip()):
            raise ValueError('invalid proxy address')
        raw = raw.strip()
        explicit = '://' in raw
        try:
            parsed = urlsplit(raw if explicit else '//' + raw)
            host, port = parsed.hostname, parsed.port
            if parsed.path not in ('', '/') or parsed.query or parsed.fragment:
                raise ValueError('proxy address cannot contain path/query/fragment')
            username = unquote(parsed.username, errors='strict') if parsed.username is not None else None
            password = unquote(parsed.password, errors='strict') if parsed.password is not None else None
        except (TypeError, ValueError):
            raise ValueError('invalid proxy URL') from None
        if explicit:
            if parsed.scheme.lower() not in VARIANTS:
                raise ValueError('unsupported proxy scheme')
            # An explicit URL is stronger evidence than a provider capability label.
            protocols.insert(0, parsed.scheme.lower())
            scheme = parsed.scheme.lower()
    else:
        host, port = value.get('host', value.get('ip')), value.get('port')
    if not isinstance(host, str) or not host:
        raise ValueError('missing proxy host')
    host = host.strip().rstrip('.').lower()
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        if ':' in host or re.fullmatch(r'[0-9.]+', host):
            raise ValueError('invalid IP address') from None
        try:
            host = host.encode('idna').decode('ascii')
        except UnicodeError:
            raise ValueError('invalid domain') from None
        if len(host) > 253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                                  for label in host.split('.')):
            raise ValueError('invalid domain')
    if isinstance(port, bool) or not isinstance(port, (str, int)) or not re.fullmatch(r'[0-9]+', str(port)):
        raise ValueError('invalid proxy port')
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError('proxy port out of range')
    credential_ref = ''
    if username is not None:
        if not isinstance(identity_key, bytes) or len(identity_key) < 32:
            raise ValueError('authenticated candidates require a persistent identity key')
        material = (username + '\0' + (password or '')).encode()
        credential_ref = hmac.new(identity_key, material, hashlib.sha256).hexdigest()
    country = str(value.get('country', value.get('country_hint', '')) or '').upper()
    if not re.fullmatch('[A-Z]{2}', country):
        country = ''
    raw_hints = value.get('country_hints') or ()
    if not isinstance(raw_hints, (tuple, list)):
        raise ValueError('country hints must be an array')
    hints = {str(h).upper() for h in raw_hints if isinstance(h, str) and re.fullmatch('[A-Za-z]{2}', h)}
    if country:
        hints.add(country)
    if len(hints) > 1:
        country = ''
    elif hints:
        country = next(iter(hints))
    sources = value.get('sources') or [value.get('source') or source]
    if isinstance(sources, str):
        sources = sources.split('/')
    sources = tuple(sorted({str(s) for s in sources if s}))
    return ProxyCandidate(host, port, tuple(dict.fromkeys(p for p in protocols if p in VARIANTS)),
                          country, sources, credential_ref, username, password, scheme,
                          country_hints=tuple(sorted(hints)))
