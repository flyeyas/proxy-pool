"""Immutable, validated rule snapshots; no network or database at import time."""
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
import math
import re
import time
from urllib.parse import urlsplit
from types import MappingProxyType

from .models import VARIANTS
from setting import VERSION as ENGINE_VERSION


@dataclass(frozen=True)
class TargetPolicy:
    url: str
    allowed_hosts: tuple
    positive: tuple
    negative: tuple = ('captcha', 'verify you are human', 'unusual traffic',
                       'consent.google', '验证码', '人机验证', 'proxy error')


def default_targets():
    return {
        'domestic': TargetPolicy('https://www.qq.com', ('qq.com', 'www.qq.com'),
                                 ('腾讯', 'tencent', 'qq.com')),
        'overseas': TargetPolicy('https://www.google.com', ('google.com', 'www.google.com'),
                                 ('google',)),
    }


@dataclass(frozen=True)
class ValidationPolicy:
    targets: dict = field(default_factory=default_targets)
    domestic_countries: tuple = ('CN',)
    connect_timeout: float = 3
    read_timeout: float = 5
    round_deadline: float = 30
    shutdown_grace: float = 2
    response_limit: int = 32768
    max_redirects: int = 3
    retries: int = 1
    workers: int = 4
    target_rps: float = 2
    check_interval: float = 120
    fresh_seconds: float = 600
    max_hard_failures: int = 3
    unavailable_interval: float = 900
    candidate_ttl: float = 86400
    max_candidates: int = 5000
    target_ca: str = ''
    proxy_ca: str = ''
    allow_tls_discovery: bool = False
    http_probe_url: str = ''
    exit_geo_url: str = 'https://api.ip.sb/geoip'
    hooks: tuple = ()

    def __post_init__(self):
        bounds = {'connect_timeout': (0.05, 30), 'read_timeout': (0.05, 60),
                  'round_deadline': (0.1, 120), 'shutdown_grace': (0.1, 5),
                  'workers': (1, 20), 'target_rps': (0.1, 20), 'max_redirects': (0, 3),
                  'retries': (0, 1), 'response_limit': (1024, 65536),
                  'check_interval': (1, 3600), 'fresh_seconds': (1, 3600),
                  'max_hard_failures': (1, 10), 'candidate_ttl': (60, 604800),
                  'unavailable_interval': (1, 86400),
                  'max_candidates': (1, 50000)}
        integer_fields = {'workers', 'max_redirects', 'retries', 'response_limit',
                          'max_hard_failures', 'max_candidates'}
        for name, (low, high) in bounds.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError('invalid validation option: ' + name)
            if name in integer_fields and not isinstance(value, int):
                raise ValueError('integer required: ' + name)
        if type(self.allow_tls_discovery) is not bool:
            raise ValueError('allow_tls_discovery must be boolean')
        if not isinstance(self.hooks, (list, tuple)) or any(not isinstance(h, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9.]*:[A-Za-z_][A-Za-z_0-9]*', h) for h in self.hooks):
            raise ValueError('hooks require installed module:function entries')
        if not all(isinstance(v, str) for v in (self.http_probe_url, self.exit_geo_url, self.target_ca, self.proxy_ca)):
            raise ValueError('URL and CA options must be strings')
        if self.exit_geo_url:
            parsed_exit = urlsplit(self.exit_geo_url)
            if parsed_exit.scheme != 'https' or not parsed_exit.hostname or parsed_exit.username or parsed_exit.password:
                raise ValueError('exit identity lookup requires HTTPS without credentials')
        if self.http_probe_url:
            parsed_http = urlsplit(self.http_probe_url)
            if parsed_http.scheme != 'http' or not parsed_http.hostname or parsed_http.username or parsed_http.password:
                raise ValueError('HTTP capability probe requires an explicit HTTP URL without credentials')
        if set(self.targets) != {'domestic', 'overseas'}:
            raise ValueError('both target profiles are required')
        for target in self.targets.values():
            if not isinstance(target, TargetPolicy):
                raise ValueError('invalid target policy')
            parsed = urlsplit(target.url)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.hostname not in target.allowed_hosts or parsed.username or parsed.password or parsed.fragment:
                raise ValueError('target must be allowlisted HTTPS without credentials')
            if not target.positive or not all(isinstance(x, str) and x for x in (*target.allowed_hosts, *target.positive, *target.negative)):
                raise ValueError('target evidence rules cannot be empty')
        if not self.domestic_countries or any(not isinstance(c, str) or len(c) != 2 or not c.isascii() or not c.isalpha() or c != c.upper() for c in self.domestic_countries):
            raise ValueError('invalid domestic country mapping')
        object.__setattr__(self, 'targets', MappingProxyType(dict(self.targets)))
        object.__setattr__(self, 'domestic_countries', tuple(self.domestic_countries))
        object.__setattr__(self, 'hooks', tuple(self.hooks))

    @property
    def version(self):
        # Operational tuning must not empty a healthy pool. Only evidence rules
        # invalidate cached successes; the full fingerprint drives config reload.
        operational = {'workers', 'target_rps', 'shutdown_grace', 'check_interval',
                       'fresh_seconds', 'max_hard_failures', 'candidate_ttl',
                       'max_candidates', 'allow_tls_discovery'}
        operational.add('unavailable_interval')
        rules = {k: v for k, v in self.to_dict().items() if k not in operational}
        return sha256(json.dumps({'engine': ENGINE_VERSION, 'rules': rules}, sort_keys=True).encode()).hexdigest()[:20]

    @property
    def fingerprint(self):
        return sha256(json.dumps({'engine': ENGINE_VERSION, 'config': self.to_dict()}, sort_keys=True).encode()).hexdigest()[:20]

    def profiles_for(self, country):
        if not country:
            return ('domestic', 'overseas')
        return ('domestic',) if country in self.domestic_countries else ('overseas',)

    def variants_for(self, endpoint, now=None):
        if endpoint.explicit_scheme and set(endpoint.protocol_hints) <= {endpoint.explicit_scheme}:
            return (endpoint.explicit_scheme,)
        # Provider https labels commonly describe CONNECT capability, not TLS-to-proxy.
        hints = [p for p in endpoint.protocol_hints if p in VARIANTS]
        preferred = [endpoint.explicit_scheme] if endpoint.explicit_scheme else []
        for hint in hints:
            if hint == endpoint.explicit_scheme:
                continue
            preferred.extend({'https': ['http', 'https'], 'socks5': ['socks5h', 'socks5'],
                              'socks4': ['socks4a', 'socks4']}.get(hint, [hint]))
        if not preferred:
            preferred = ['http', 'socks5h', 'socks4a']
            if self.allow_tls_discovery:
                preferred.append('https')
        preferred = list(dict.fromkeys(preferred))
        now = time.time() if now is None else now
        proven = []
        for variant, data in endpoint.variants.items():
            results = data.get('targets', {}).values()
            last = max((r.get('checked_at', 0) for r in results), default=0)
            current_rules = any(r.get('policy_version') == self.version for r in results)
            cooling = current_rules and data.get('hard_failures', 0) >= self.max_hard_failures and now < last + self.unavailable_interval
            if not cooling and any(r.get('code') == 'available' or r.get('last_success', 0) > 0 for r in results):
                proven.append(variant)
        attempted = set(endpoint.attempted_variants or endpoint.discovered)
        unexplored = [v for v in preferred if v not in attempted][:2]
        if unexplored:
            return tuple(dict.fromkeys(proven + unexplored))
        if proven:
            return tuple(proven)
        offset = endpoint.discovery_cursor % len(preferred)
        return tuple((preferred[offset:] + preferred[:offset])[:2])

    def to_dict(self):
        data = {name: getattr(self, name) for name in self.__dataclass_fields__ if name != 'targets'}
        data['targets'] = {k: asdict(v) for k, v in self.targets.items()}
        return data

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError('validation policy must be an object')
        values = dict(data)
        unknown = set(values) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError('unknown validation options: ' + ','.join(sorted(unknown)))
        if 'targets' in values:
            targets = values['targets']
            if not isinstance(targets, dict) or any(not isinstance(t, dict) for t in targets.values()):
                raise ValueError('targets must contain target objects')
            for target in targets.values():
                if not isinstance(target.get('url'), str):
                    raise ValueError('target URL must be a string')
                for field_name in ('allowed_hosts', 'positive', 'negative'):
                    if field_name in target and not isinstance(target[field_name], (list, tuple)):
                        raise ValueError('target evidence and allowlists must be arrays')
            try:
                values['targets'] = {k: TargetPolicy(**{n: tuple(v) if n != 'url' else v
                                                      for n, v in t.items()})
                                     for k, t in targets.items()}
            except (TypeError, KeyError):
                raise ValueError('invalid target options') from None
        if 'domestic_countries' in values:
            if not isinstance(values['domestic_countries'], (list, tuple)):
                raise ValueError('domestic countries must be an array')
            values['domestic_countries'] = tuple(values['domestic_countries'])
        return cls(**values)
