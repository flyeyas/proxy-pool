"""Authenticated V2 inspection, connections and asynchronous migration actions."""
from functools import wraps
import json
import math
import threading
import time
from uuid import uuid4

from flask import Blueprint, request, session
from redis.exceptions import RedisError

from api.response import apiError, apiOk
from helper.validation import service
from helper.validation.config import desired, save_desired
from helper.validation.events import emit
from helper.validation.health import TargetHealth
from helper.validation.models import VARIANTS
from helper.validation.state import available_routes, public_endpoint

validation_bp = Blueprint('validation', __name__, url_prefix='/api/v2')
STATUSES = {'available', 'all', 'pending', 'suspect', 'unavailable', 'target_restricted',
            'auth_required', 'auth_failed', 'tls_invalid', 'content_inconclusive', 'stale', 'http_only'}


@validation_bp.before_request
def proxy_serving_mode():
    if not request.path.startswith('/api/v2/proxies'):
        return None
    current = service.mode()
    if current == 'legacy':
        return apiError(409, 'V2 proxy serving is disabled in legacy mode')
    control = service.repository().control()
    if control and control.get('draining'):
        return apiError(409, 'validation transition in progress')
    from handler.configHandler import ConfigHandler
    if current == 'shadow' and ConfigHandler().webuiPassword and not session.get('webui_login'):
        return apiError(403, 'shadow results require an administrator session')
    return None


def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        from handler.configHandler import ConfigHandler
        if ConfigHandler().webuiPassword and not session.get('webui_login'):
            return apiError(403, 'administrator session required')
        return fn(*args, **kwargs)
    return wrapped


@validation_bp.errorhandler(ValueError)
def bad_request(exc):
    return apiError(400, str(exc))


@validation_bp.errorhandler(RedisError)
def database_unavailable(exc):
    return apiError(503, 'validation store unavailable')


def body_object():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError('JSON object required')
    return value


def arguments(listing=False):
    allowed = {'protocol', 'dns_mode', 'profile', 'region', 'max_latency_ms', 'token'}
    if listing:
        allowed |= {'status', 'page', 'page_size', 'cursor'}
    if set(request.args) - allowed:
        raise ValueError('unknown query parameters')
    protocol = request.args.get('protocol')
    dns = request.args.get('dns_mode')
    if protocol and protocol not in ('http', 'https', 'socks4', 'socks5'):
        raise ValueError('unsupported protocol')
    if dns and (dns not in ('local', 'remote') or protocol not in ('socks4', 'socks5')):
        raise ValueError('DNS mode requires SOCKS4/5')
    variants = [protocol] if protocol else list(VARIANTS)
    if protocol in ('socks4', 'socks5'):
        remote = protocol + ('a' if protocol == 'socks4' else 'h')
        variants = [protocol] if dns == 'local' else [remote] if dns == 'remote' else [remote, protocol]
    profile = request.args.get('profile')
    if profile and profile not in ('domestic', 'overseas'):
        raise ValueError('unsupported profile')
    latency = request.args.get('max_latency_ms')
    latency = float(latency) if latency is not None else None
    if latency is not None and (not math.isfinite(latency) or latency < 0):
        raise ValueError('invalid latency threshold')
    regions = request.args.get('region', '')
    if regions and any(len(r.strip()) != 2 or not r.strip().isascii() or not r.strip().isalpha() for r in regions.split(',')):
        raise ValueError('region requires comma-separated two-letter codes')
    return variants, profile, regions.upper() or None, latency


@validation_bp.route('/proxies/')
def proxies():
    variants, profile, regions, latency = arguments(listing=True)
    status = request.args.get('status', 'available')
    if status not in STATUSES:
        raise ValueError('unsupported status')
    if status != 'available':
        from handler.configHandler import ConfigHandler
        if ConfigHandler().webuiPassword and not session.get('webui_login'):
            return apiError(403, 'administrator session required for diagnostic states')
    limit = int(request.args.get('page_size', 50))
    page = int(request.args.get('page', 1))
    cursor = int(request.args.get('cursor', (page - 1) * limit))
    if not 1 <= limit <= 200 or page < 1 or cursor < 0:
        raise ValueError('invalid pagination')
    repo, control, policy = service.effective()
    endpoints, next_cursor, total = repo.page(cursor, limit)
    selected = []
    now = time.time()
    for endpoint in endpoints:
        if regions and endpoint.country not in {r.strip() for r in regions.split(',')}:
            continue
        if request.args.get('protocol') and not (set(variants) & (set(endpoint.variants) | set(endpoint.protocol_hints))):
            continue
        record = public_endpoint(endpoint, policy, now, profile=profile, variants=variants)
        routes = [(v, p) for v, p in available_routes(endpoint, policy, now, profile=profile) if v in variants]
        if status == 'available' and not routes:
            continue
        if status not in ('available', 'all') and record['status'] != status:
            continue
        if latency is not None and not any(endpoint.variants[v]['targets'][p]['elapsed_ms'] <= latency for v, p in routes):
            continue
        record['available_routes'] = [{'variant': v, 'profile': p} for v, p in routes]
        selected.append(record)
    # Cursor describes the scanned catalog page; filters may produce an empty page.
    return apiOk({'proxies': selected, 'count': len(selected), 'catalog_total': total,
                  'next_cursor': next_cursor, 'page_size': limit})


def connection(consume=False):
    variants, profile, region, latency = arguments()
    repo, control, policy = service.effective()
    for variant in variants:
        route = repo.route(policy, profile=profile, variant=variant, region=region,
                           consume=consume, max_latency_ms=latency)
        if not route:
            continue
        endpoint, variant, matched_profile = route
        result = endpoint.variants[variant]['targets'][matched_profile]
        candidate = endpoint.candidate(repo.credentials(endpoint))
        return apiOk({'endpoint_id': endpoint.endpoint_id, 'proxy_url': candidate.proxy_url(variant, credentials=True),
                      'variant': variant, 'profile': matched_profile, 'country': endpoint.country,
                      'supports_https': True, 'checked_at': result['checked_at'],
                      'valid_until': min(result['valid_until'], result['checked_at'] + policy.fresh_seconds),
                      'elapsed_ms': result['elapsed_ms']})
    return apiError(404, 'no matching validated proxy')


@validation_bp.route('/proxies/random/')
def random_proxy():
    return connection()


@validation_bp.route('/proxies/pop/', methods=['POST'])
def pop_proxy():
    return connection(consume=True)


@validation_bp.route('/proxies/<endpoint_id>/', methods=['DELETE'])
def delete_proxy(endpoint_id):
    if len(endpoint_id) != 64 or any(c not in '0123456789abcdef' for c in endpoint_id):
        raise ValueError('invalid endpoint ID')
    return apiOk({'deleted': True}) if service.repository().remove(endpoint_id) else apiError(404, 'endpoint not found')


@validation_bp.route('/validation/status/')
def status():
    settings = desired()
    if service.mode() == 'legacy':
        return apiOk({'mode': 'legacy', 'online': False, 'desired_version': settings['policy'].fingerprint})
    repo, control, policy = service.effective()
    raw = repo.redis.get(repo.prefix + 'status')
    snapshot = json.loads(raw) if raw else {}
    return apiOk({**snapshot, 'mode': control['mode'] if control else settings['mode'],
                  'online': time.time() - snapshot.get('heartbeat', 0) < 30,
                  'desired_version': settings['policy'].fingerprint,
                  'effective_version': control.get('config_version') if control else None,
                  'targets': TargetHealth(repo).states()})


@validation_bp.route('/validation/config/', methods=['GET', 'PATCH'])
@admin_required
def config():
    if request.method == 'PATCH':
        changes = body_object()
        if changes.get('mode') in ('active', 'legacy'):
            raise ValueError('use activate/rollback transition jobs for production mode changes')
        if 'mode' in changes and service.mode() == 'active':
            raise ValueError('use rollback job to leave active mode')
        if changes.get('mode') == 'shadow':
            service.repository()  # Validate the backend before persisting the requested mode.
        settings = save_desired(changes)
    else:
        settings = desired()
    return apiOk({'mode': settings['mode'], 'policy': settings['policy'].to_dict(),
                  'policy_version': settings['policy'].version, 'config_version': settings['policy'].fingerprint,
                  'locked': settings['locked'],
                  'mode_locked': settings['mode_locked']})


def _action(job_id, data):
    repo = service.repository()
    key = repo.prefix + 'job:' + job_id
    repo.redis.set(key, json.dumps({'state': 'running', 'action': data['action']}), ex=86400)
    try:
        from helper.validation.migration import activate, import_legacy, rollback
        repo, control, policy = service.effective()
        action = data['action']
        if action == 'import':
            result = import_legacy(repo, policy)
        elif action == 'activate':
            result = activate(repo, policy, minimum=data.get('minimum', 20))
        elif action == 'rollback':
            result = rollback(repo)
        else:
            ids = data.get('endpoint_ids')
            if ids:
                count = sum(repo.prioritize(ids[i:i + 3], profiles=data.get('profiles', ()))
                            for i in range(0, len(ids), 3))
                result = {'queued': count}
            else:
                repo.reschedule_all()
                result = {'queued': repo.redis.zcard(repo.due)}
        repo.redis.set(key, json.dumps({'state': 'complete', 'result': result}), ex=86400)
    except Exception as exc:
        repo.redis.set(key, json.dumps({'state': 'error', 'error_type': type(exc).__name__,
                                       'message': str(exc) if isinstance(exc, ValueError) else 'validation action failed'}), ex=86400)
        emit('validation_action_failed', job_id=job_id, error_type=type(exc).__name__)


@validation_bp.route('/validation/jobs/', methods=['POST'])
@admin_required
def jobs():
    data = body_object()
    if set(data) - {'action', 'minimum', 'endpoint_ids', 'profiles'} or data.get('action') not in ('import', 'activate', 'rollback', 'recheck'):
        raise ValueError('unsupported validation job')
    if 'minimum' in data and (isinstance(data['minimum'], bool) or not isinstance(data['minimum'], int) or not 1 <= data['minimum'] <= 50000):
        raise ValueError('invalid availability minimum')
    if any(key in data for key in ('endpoint_ids', 'profiles')):
        ids, profiles = data.get('endpoint_ids'), data.get('profiles', [])
        if (data['action'] != 'recheck' or not isinstance(ids, list) or not 1 <= len(ids) <= 200
                or any(not isinstance(i, str) or len(i) != 64 or any(c not in '0123456789abcdef' for c in i) for i in ids)
                or not isinstance(profiles, list) or len(profiles) > 2
                or any(p not in ('domestic', 'overseas') for p in profiles)):
            raise ValueError('recheck requires up to 200 endpoint IDs and valid optional profiles')
        data['endpoint_ids'] = list(dict.fromkeys(ids))
        data['profiles'] = list(dict.fromkeys(profiles))
    settings = desired()
    if service.mode() == 'legacy':
        raise ValueError('enable shadow mode first')
    job_id = uuid4().hex
    service.repository().redis.set(service.repository().prefix + 'job:' + job_id,
                                   json.dumps({'state': 'pending', 'action': data['action']}), ex=86400)
    threading.Thread(target=_action, args=(job_id, data), daemon=True).start()
    return apiOk({'job_id': job_id}, status=202)


@validation_bp.route('/validation/jobs/<job_id>/')
@admin_required
def job_status(job_id):
    if len(job_id) != 32 or any(c not in '0123456789abcdef' for c in job_id):
        raise ValueError('invalid job ID')
    raw = service.repository().redis.get(service.repository().prefix + 'job:' + job_id)
    return apiOk(json.loads(raw)) if raw else apiError(404, 'job not found')
