"""Serializable contracts. Unknown capabilities must not become false evidence."""
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from typing import Optional
from urllib.parse import quote
from uuid import uuid4

VARIANTS = ('http', 'https', 'socks4', 'socks4a', 'socks5', 'socks5h')
PROFILES = ('domestic', 'overseas')
HARD_FAILURES = frozenset(('proxy_connect_failed', 'protocol_error'))
TRANSIENT = frozenset(('proxy_connect_failed', 'read_timeout'))


@dataclass(frozen=True)
class ProxyCandidate:
    host: str
    port: int
    protocols: tuple = ()
    country: str = ''
    sources: tuple = ()
    credential_ref: str = ''
    username: Optional[str] = field(default=None, repr=False, compare=False)
    password: Optional[str] = field(default=None, repr=False, compare=False)
    explicit_scheme: str = ''
    exit_lookup_due: bool = False
    requested_profiles: tuple = ()
    profile_backoffs: dict = field(default_factory=dict, repr=False, compare=False)
    country_hints: tuple = ()

    def __post_init__(self):
        for name in ('protocols', 'sources', 'requested_profiles', 'country_hints'):
            value = getattr(self, name)
            if not isinstance(value, (tuple, list)):
                raise ValueError('candidate collection fields must be arrays')
            object.__setattr__(self, name, tuple(value))

    @property
    def address(self):
        host = '[' + self.host + ']' if ':' in self.host else self.host
        return '%s:%s' % (host, self.port)

    @property
    def endpoint_id(self):
        return sha256(('%s|%s|%s' % (self.host, self.port, self.credential_ref)).encode()).hexdigest()

    def proxy_url(self, variant, credentials=False):
        if variant not in VARIANTS:
            raise ValueError('unsupported proxy variant')
        auth = ''
        if credentials and self.username is not None:
            auth = quote(self.username, safe='')
            if self.password is not None:
                auth += ':' + quote(self.password, safe='')
            auth += '@'
        return '%s://%s%s' % (variant, auth, self.address)

    def public_dict(self):
        return {k: v for k, v in asdict(self).items() if k not in ('username', 'password', 'exit_lookup_due', 'requested_profiles', 'profile_backoffs')}


@dataclass
class TargetResult:
    variant: str
    profile: str
    code: str
    stage: str
    status_code: Optional[int] = None
    elapsed_ms: int = 0
    queue_wait_ms: int = 0
    checked_at: float = 0
    attempts: int = 1
    redirects: int = 0
    retry_after: float = 0
    policy_version: str = ''
    exit_ip: str = ''
    exit_country: str = ''

    @property
    def passed(self):
        return self.code == 'available'

    def to_dict(self):
        return asdict(self)


@dataclass
class ProxyEndpointV2:
    endpoint_id: str
    host: str
    port: int
    generation: str = field(default_factory=lambda: uuid4().hex)
    schema_version: int = 2
    credential_ref: str = ''
    sources: list = field(default_factory=list)
    explicit_scheme: str = ''
    protocol_hints: list = field(default_factory=list)
    country: str = ''
    country_source: str = 'unknown'
    country_hints: list = field(default_factory=list)
    country_confidence: str = 'unknown'
    country_checked_at: float = 0
    indexed_country: str = ''
    endpoint_country: str = ''
    exit_country: str = ''
    exit_ip: str = ''
    exit_checked_at: float = 0
    created_at: float = 0
    updated_at: float = 0
    last_success_at: float = 0
    variants: dict = field(default_factory=dict)
    discovered: list = field(default_factory=list)
    attempted_variants: list = field(default_factory=list)
    discovery_cursor: int = 0
    pending_profiles: list = field(default_factory=list)
    last_round_id: str = ''

    @classmethod
    def from_candidate(cls, candidate, now):
        hints = list(candidate.country_hints or ((candidate.country,) if candidate.country else ()))
        conflict = len(set(hints)) > 1
        country = '' if conflict else candidate.country
        return cls(endpoint_id=candidate.endpoint_id, host=candidate.host,
                   port=candidate.port, credential_ref=candidate.credential_ref,
                   sources=list(candidate.sources), explicit_scheme=candidate.explicit_scheme,
                   protocol_hints=list(candidate.protocols),
                   country=country, endpoint_country=country, country_hints=hints,
                   country_source='conflict' if conflict else 'source' if country else 'unknown',
                   country_confidence='unknown' if conflict else 'hint' if country else 'unknown',
                   created_at=now, updated_at=now)

    @classmethod
    def from_dict(cls, value):
        if value.get('schema_version') != 2:
            raise ValueError('unsupported endpoint schema')
        fields = cls.__dataclass_fields__
        data = {k: v for k, v in value.items() if k in fields}
        # Redis Lua cjson encodes an empty array as {}; normalize known array fields.
        for name in ('sources', 'protocol_hints', 'discovered', 'attempted_variants', 'pending_profiles', 'country_hints'):
            data[name] = list(data.get(name) or [])
        if not data['country_hints'] and data.get('country_source') == 'source' and data.get('country'):
            data['country_hints'] = [data['country']]
        if not data.get('last_success_at'):
            data['last_success_at'] = max((r.get('last_success', 0)
                for v in data.get('variants', {}).values() for p, r in v.get('targets', {}).items()
                if p in PROFILES), default=0)
        return cls(**data)

    def to_dict(self):
        return asdict(self)

    def candidate(self, credentials=None):
        return ProxyCandidate(self.host, self.port, tuple(self.protocol_hints),
                              self.country, tuple(self.sources), self.credential_ref,
                              explicit_scheme=self.explicit_scheme, country_hints=tuple(self.country_hints),
                              **(credentials or {}))
