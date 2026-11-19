"""V2 contracts, including cases that legacy regex/HTTPS checks get wrong."""
import json

import pytest

from helper.validation.models import ProxyEndpointV2
from helper.validation.normalize import normalize_candidate
from helper.validation.policy import TargetPolicy, ValidationPolicy
from helper.validation.transport import classify_response, retry_delay


@pytest.mark.parametrize('address,host,port', [
    ('1.2.3.4:80', '1.2.3.4', 80),
    ('http://EXAMPLE.com:8080', 'example.com', 8080),
    ('socks5h://[2001:db8::1]:1080', '2001:db8::1', 1080),
    ('[::1]:1', '::1', 1),
])
def test_addresses(address, host, port):
    c = normalize_candidate(address)
    assert (c.host, c.port) == (host, port)


@pytest.mark.parametrize('address', ['999.1.2.3:80', '1.2.3.4:0', '1.2.3.4:65536',
                                     '1.2.3.4', '1.2.3.4:x', 'x:True', 'x:1/foo',
                                     'http://x:80?a=1', 'ftp://x:80', 'a b:80',
                                     '-bad.example:80', '2001:db8::1:80'])
def test_invalid_addresses(address):
    with pytest.raises(ValueError):
        normalize_candidate(address)


def test_credentials_are_keyed_stable_and_never_public():
    value = 'socks5h://u%40ser:p%3Ass@127.0.0.1:1080'
    with pytest.raises(ValueError):
        normalize_candidate(value)
    a = normalize_candidate(value, identity_key=b'x' * 32)
    b = normalize_candidate(value, identity_key=b'x' * 32)
    c = normalize_candidate(value, identity_key=b'y' * 32)
    assert a.endpoint_id == b.endpoint_id != c.endpoint_id
    assert a.username == 'u@ser' and a.password == 'p:ss'
    assert a.proxy_url('socks5h', credentials=True) == value
    assert 'password' not in a.public_dict() and 'p:ss' not in repr(a)


def test_endpoint_roundtrip_does_not_invent_success():
    c = normalize_candidate({'proxy': '1.2.3.4:80', 'country': 'cn', 'protocols': ['http', 'socks5'], 'source': 'geonode'})
    e = ProxyEndpointV2.from_candidate(c, 100)
    restored = ProxyEndpointV2.from_dict(json.loads(json.dumps(e.to_dict())))
    assert restored.candidate().endpoint_id == c.endpoint_id
    assert restored.variants == {} and restored.country == 'CN'
    assert restored.sources == ['geonode']


@pytest.mark.parametrize('country,expected', [('CN', ('domestic',)), ('US', ('overseas',)),
                                             ('HK', ('overseas',)), ('TW', ('overseas',)),
                                             ('', ('domestic', 'overseas'))])
def test_region_profiles(country, expected):
    assert ValidationPolicy().profiles_for(country) == expected


def test_policy_is_snapshot_and_roundtrips():
    p = ValidationPolicy()
    assert ValidationPolicy.from_dict(p.to_dict()).version == p.version
    with pytest.raises(TypeError):
        p.targets['domestic'] = p.targets['overseas']


@pytest.mark.parametrize('options', [{'workers': 21}, {'workers': True}, {'target_rps': float('nan')},
                                     {'retries': 2}, {'workers': 2.5}, {'unused': 3},
                                     {'domestic_countries': ['cn']}, {'domestic_countries': None},
                                     {'http_probe_url': []}, {'targets': []},
                                     {'targets': {'domestic': {'url': 3}}}])
def test_reject_bad_config(options):
    with pytest.raises(ValueError):
        ValidationPolicy.from_dict(options)


def test_explicit_tls_url_is_not_provider_https_label():
    p = ValidationPolicy()
    tls = ProxyEndpointV2.from_candidate(normalize_candidate('https://x:443'), 0)
    label = ProxyEndpointV2.from_candidate(normalize_candidate({'proxy': 'x:443', 'protocols': ['https']}), 0)
    assert p.variants_for(tls) == ('https',)
    assert p.variants_for(label) == ('http', 'https')


@pytest.mark.parametrize('status,body,code', [(200, b'Google search', 'available'),
                                             (200, b'Google captcha', 'target_restricted'),
                                             (200, b'unknown page', 'content_inconclusive'),
                                             (403, b'Google', 'target_restricted'),
                                             (429, b'', 'target_restricted'),
                                             (407, b'', 'auth_required'),
                                             (500, b'', 'target_http_error')])
def test_response_rules(status, body, code):
    p = ValidationPolicy()
    assert classify_response(status, body, p.targets['overseas'].url, p.targets['overseas']) == code


def test_retry_after_is_bounded():
    assert retry_delay('9999999') == 86400
    assert retry_delay('not a date') == 0


@pytest.mark.parametrize('hook,code', [('accept', 'available'), ('reject', 'custom_rejected'),
                                      ('broken', 'environment_error')])
def test_explicit_hooks_can_only_restrict_success(hook, code):
    from helper.validation.hooks import apply_hooks
    from helper.validation.models import TargetResult
    candidate = normalize_candidate('http://x:80')
    policy = ValidationPolicy(hooks=('tests.fixtures.validation_hooks:' + hook,))
    assert apply_hooks(candidate, TargetResult('http', 'domestic', 'available', 'test'), policy).code == code
    assert apply_hooks(candidate, TargetResult('http', 'domestic', 'proxy_connect_failed', 'test'), policy).code == 'proxy_connect_failed'
