"""Application facade: legacy remains the opt-in migration boundary."""
from contextlib import contextmanager
from functools import lru_cache
import json
import time
from uuid import uuid4
from redis.exceptions import RedisError

from .config import desired, identity_key
from .normalize import normalize_candidate
from .policy import ValidationPolicy
from .repository import ValidationRepository
from .state import available_routes


class TransitionInProgress(RuntimeError):
    pass


@lru_cache(maxsize=4)
def _repository(url, table):
    return ValidationRepository(url, table)


def repository():
    from handler.configHandler import ConfigHandler
    config = ConfigHandler()
    return _repository(config.dbConn, config.tableName)


def mode():
    settings = desired()
    requested = settings['mode']
    if requested == 'legacy':
        if not settings['configured']:
            return 'legacy'
        from handler.configHandler import ConfigHandler
        if not ConfigHandler().dbConn.startswith(('redis://', 'rediss://')):
            return 'legacy'
    control = repository().control()
    if control:
        if control['mode'] == 'legacy' and requested == 'shadow':
            return 'shadow'  # Pending re-enable: safely enqueue, keep legacy serving.
        return control['mode']
    if requested == 'active' or settings['persisted_mode'] == 'active':
        raise RedisError('active validation control is missing; migration or restore required')
    return 'shadow' if requested == 'shadow' else 'legacy'


def candidate_for(proxy):
    if getattr(proxy, 'validation_candidate', None) is not None:
        candidate = proxy.validation_candidate
        if candidate.credential_ref and candidate.username is None and '@' in proxy.proxy:
            from dataclasses import replace
            parsed = normalize_candidate(proxy.proxy, identity_key=identity_key())
            candidate = replace(candidate, username=parsed.username, password=parsed.password)
        return candidate
    raw = proxy.proxy if hasattr(proxy, 'proxy') else proxy
    candidate = normalize_candidate(raw, source=getattr(proxy, 'source', ''),
                                    identity_key=identity_key() if '@' in str(raw) else None)
    return candidate


def submit(proxies):
    settings = desired()
    if mode() == 'legacy':
        return 0
    repo = repository()
    if repo.control() is None:
        if settings['mode'] == 'active':
            raise RedisError('active validation control is missing')
        repo.initialize('shadow', settings['policy'])
    count = 0
    for proxy in proxies:
        count += bool(repo.submit(candidate_for(proxy), settings['policy']))
    return count


@contextmanager
def legacy_writer():
    if desired()['mode'] == 'legacy':
        yield True
        return
    repo = repository()
    key, token = repo.prefix + 'legacy_writers', uuid4().hex
    script = """
    local c=redis.call('GET',KEYS[1])
    if c then local v=cjson.decode(c); if v.mode=='active' or v.draining then return 0 end end
    redis.call('ZADD',KEYS[2],ARGV[1],ARGV[2]); return 1
    """
    allowed = bool(repo.redis.eval(script, 2, repo.control_key, key, time.time() + 600, token))
    try:
        yield allowed
    finally:
        if allowed:
            repo.redis.zrem(key, token)


def effective():
    repo = repository()
    control = repo.control()
    if control is None and desired()['mode'] == 'active':
        raise RedisError('active validation control is missing')
    policy = ValidationPolicy.from_dict(control['policy']) if control else desired()['policy']
    return repo, control, policy


def legacy_get(region=None, consume=False):
    from helper.proxy import Proxy
    repo, control, policy = effective()
    route = repo.route(policy, variant='http', region=region, consume=consume)
    if not route:
        return None
    endpoint, _, _ = route
    _, projection = repo.legacy_projection(endpoint, policy, time.time())
    return Proxy.createFromJson(projection) if projection else None


def legacy_all(region=None):
    from helper.proxy import Proxy
    repo, control, policy = effective()
    cursor, proxies = 0, []
    regions = {r.upper() for r in region.split(',')} if region else None
    while True:
        endpoints, cursor, _ = repo.page(cursor, 200)
        for endpoint in endpoints:
            if regions and endpoint.country not in regions:
                continue
            _, projection = repo.legacy_projection(endpoint, policy, time.time())
            if projection:
                proxies.append(Proxy.createFromJson(projection))
        if cursor is None:
            return proxies


def legacy_delete(proxy):
    repo = repository()
    return repo.remove(candidate_for(proxy).endpoint_id)
