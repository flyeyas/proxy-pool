"""Idempotent import and one fenced global cutover, never mixed legacy writers."""
import json
import time
from uuid import uuid4

from .config import identity_key, save_desired
from .normalize import normalize_candidate
from .repository import encode


def import_legacy(repo, policy):
    imported, rejected = 0, 0
    for address, value in repo.redis.hscan_iter(repo.table, count=200):
        try:
            old = json.loads(value)
            candidate = normalize_candidate({'proxy': address, 'country': old.get('region', ''),
                                             'source': old.get('source', '')}, identity_key=identity_key() if '@' in address else None)
            repo.submit(candidate, policy)
            imported += 1
        except (ValueError, TypeError):
            rejected += 1
    return {'imported': imported, 'rejected': rejected}


def activate(repo, policy, minimum=20, wait_seconds=120):
    from .config import desired
    if desired()['mode_locked']:
        raise ValueError('unset VALIDATION_MODE before requesting production transitions')
    if not 1 <= minimum <= 50000:
        raise ValueError('minimum must be between 1 and 50000')
    lock = repo.prefix + 'transition'
    token = uuid4().hex
    if not repo.redis.set(lock, token, nx=True, ex=180):
        raise ValueError('another transition is running')
    original = repo.control()
    committed = False
    temp = repo.prefix + 'projection:' + token
    backup = repo.table + ':rollback:' + token
    try:
        if not original or original['mode'] != 'shadow' or original.get('draining'):
            raise ValueError('activation requires shadow mode')
        if original['policy_version'] != policy.version:
            raise ValueError('wait for the desired policy to become effective')
        # Coverage must include the actual legacy pool, even when an operator did not
        # explicitly submit the import job before requesting activation.
        import_legacy(repo, policy)
        pending = completed = available = 0
        cursor = 0
        while True:
            endpoints, cursor, _ = repo.page(cursor, 200)
            for endpoint in endpoints:
                if any(r.get('policy_version') == policy.version and r.get('code') not in
                       ('deferred', 'environment_error', 'unsupported')
                       for variant in endpoint.variants.values() for r in variant.get('targets', {}).values()):
                    completed += 1
                else:
                    pending += 1
                _, projection = repo.legacy_projection(endpoint, policy, time.time())
                available += bool(projection)
            if cursor is None:
                break
        if completed == 0 or completed / (completed + pending) < 0.95 or available < minimum:
            raise ValueError('activation coverage or compatible availability gate not met')
        draining = {**original, 'draining': True, 'epoch': uuid4().hex}
        if not repo.update_control(original, draining):
            raise ValueError('control changed; retry activation')
        deadline = time.monotonic() + wait_seconds
        writers = repo.prefix + 'legacy_writers'
        while True:
            repo.redis.zremrangebyscore(writers, '-inf', time.time())
            if repo.redis.zcard(writers) == 0:
                break
            if time.monotonic() >= deadline:
                raise ValueError('legacy writers did not drain')
            time.sleep(0.1)
        legacy_existed = bool(repo.redis.exists(repo.table))
        if legacy_existed:
            repo.redis.copy(repo.table, backup)
        cursor = 0
        count = 0
        while True:
            endpoints, cursor, _ = repo.page(cursor, 200)
            projection = {}
            for endpoint in endpoints:
                address, value = repo.legacy_projection(endpoint, policy, time.time())
                if value:
                    projection[address] = value
            if projection:
                repo.redis.hset(temp, mapping=projection)
                count += len(projection)
            if cursor is None:
                break
        if count < minimum:
            raise ValueError('availability expired while draining')
        active = {**draining, 'mode': 'active', 'draining': False, 'epoch': uuid4().hex,
                  'rollback_hash': backup if legacy_existed else None,
                  'rollback_existed': legacy_existed, 'activated_at': time.time()}
        script = """
        if redis.call('GET',KEYS[1])~=ARGV[1] or redis.call('GET',KEYS[4])~=ARGV[3] then return 0 end
        if redis.call('ZCARD',KEYS[5])~=0 or redis.call('EXISTS',KEYS[2])==0 then return 0 end
        redis.call('RENAME',KEYS[2],KEYS[3]); redis.call('SET',KEYS[1],ARGV[2]); return 1
        """
        raw = repo.redis.get(repo.control_key)
        if not raw or json.loads(raw) != draining or not repo.redis.eval(script, 5,
                repo.control_key, temp, repo.table, lock, writers, raw, encode(active), token):
            raise ValueError('activation fenced by concurrent state change')
        committed = True
        save_desired({'mode': 'active'})
        repo.reschedule_all()
        return {'mode': 'active', 'compatible_available': count, 'rollback_hash': active['rollback_hash']}
    finally:
        if not committed:
            current = repo.control()
            if original and current and current.get('draining') and current.get('epoch') != original.get('epoch'):
                repo.update_control(current, {**original, 'epoch': uuid4().hex, 'draining': False})
        repo.redis.delete(temp)
        repo.redis.eval("if redis.call('GET',KEYS[1])==ARGV[1] then redis.call('DEL',KEYS[1]) end", 1, lock, token)


def _rollback(repo):
    from .config import desired
    if desired()['mode_locked']:
        raise ValueError('unset VALIDATION_MODE before requesting production transitions')
    current = repo.control()
    if not current:
        raise ValueError('no active validation control')
    backup = current.get('rollback_hash')
    legacy = {**current, 'mode': 'legacy', 'draining': False, 'epoch': uuid4().hex}
    action = 'none'
    if current['mode'] == 'active':
        action = 'copy' if current.get('rollback_existed', bool(backup)) else 'empty'
    raw = repo.redis.get(repo.control_key)
    script = """
    if redis.call('GET',KEYS[1])~=ARGV[1] then return 0 end
    if ARGV[3]=='copy' then
      if redis.call('EXISTS',KEYS[3])==0 then return -1 end
      redis.call('COPY',KEYS[3],KEYS[2],'REPLACE')
    elseif ARGV[3]=='empty' then redis.call('DEL',KEYS[2]) end
    redis.call('SET',KEYS[1],ARGV[2]); return 1
    """
    if raw is None or json.loads(raw) != current:
        raise ValueError('concurrent transition')
    changed = repo.redis.eval(script, 3, repo.control_key, repo.table,
                              backup or repo.prefix + 'no_backup', raw, encode(legacy), action)
    if changed != 1:
        raise ValueError('rollback backup missing or concurrent transition; active mode unchanged')
    save_desired({'mode': 'legacy'})
    return {'mode': 'legacy', 'restored_backup': action == 'copy', 'restored_empty': action == 'empty'}


def rollback(repo):
    key, owner = repo.prefix + 'transition', uuid4().hex
    if not repo.redis.set(key, owner, nx=True, ex=180):
        raise ValueError('another transition is running')
    try:
        return _rollback(repo)
    finally:
        repo.redis.eval("if redis.call('GET',KEYS[1])==ARGV[1] then redis.call('DEL',KEYS[1]) end", 1, key, owner)
