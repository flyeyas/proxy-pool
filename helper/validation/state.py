"""Pure state transitions. Attempts, rounds and endpoint capabilities are distinct."""
import copy

from .models import HARD_FAILURES, VARIANTS

INCONCLUSIVE = {'deferred', 'environment_error', 'unsupported'}


def eligible(endpoint, variant, profile, policy, now):
    data = endpoint.variants.get(variant, {})
    result = data.get('targets', {}).get(profile, {})
    return (not data.get('suspended', False) and result.get('code') == 'available'
            and min(result.get('valid_until', 0), result.get('checked_at', 0) + policy.fresh_seconds) > now
            and result.get('policy_version') == policy.version)


def available_routes(endpoint, policy, now, profile=None, variant=None):
    profiles = (profile,) if profile else policy.profiles_for(endpoint.country)
    variants = (variant,) if variant else VARIANTS
    return [(v, p) for v in variants for p in profiles if eligible(endpoint, v, p, policy, now)]


def apply_results(endpoint, results, policy, round_id, now, target_health=None):
    if endpoint.last_round_id == round_id:
        return endpoint, False
    endpoint = copy.deepcopy(endpoint)
    by_variant = {}
    attempted = False
    for result in results:
        if result.get('policy_version') != policy.version or result.get('variant') not in VARIANTS:
            continue
        if result.get('profile') == 'exit_geo':
            if result.get('code') != 'deferred':
                endpoint.exit_checked_at = result['checked_at']
            if result.get('code') == 'exit_verified':
                endpoint.exit_ip, endpoint.exit_country = result['exit_ip'], result['exit_country']
                endpoint.country = endpoint.exit_country
                endpoint.country_source, endpoint.country_confidence = 'exit_verified', 'verified'
                endpoint.country_checked_at = result['checked_at']
            continue
        if result['profile'] in endpoint.pending_profiles and result['code'] not in ('deferred', 'environment_error'):
            endpoint.pending_profiles.remove(result['profile'])
        by_variant.setdefault(result['variant'], []).append(result)
        if result.get('code') == 'available' and result.get('profile') in ('domestic', 'overseas'):
            endpoint.last_success_at = max(endpoint.last_success_at, result['checked_at'])
    for variant, group in by_variant.items():
        data = endpoint.variants.setdefault(variant, {'targets': {}, 'hard_failures': 0, 'suspended': False})
        hard = any(r['code'] in HARD_FAILURES for r in group)
        success = any(r['code'] == 'available' for r in group)
        conclusive = any(r['code'] not in INCONCLUSIVE and r['code'] != 'deadline_exceeded' for r in group)
        if any(r['code'] not in ('deferred', 'environment_error') for r in group):
            attempted = True
            if variant not in endpoint.attempted_variants:
                endpoint.attempted_variants.append(variant)
        if conclusive and variant not in endpoint.discovered:
            endpoint.discovered.append(variant)
        if hard and not success:
            data['hard_failures'] += 1
            data['suspended'] = True
        elif success:
            data['hard_failures'] = 0
            data['suspended'] = False
        for result in group:
            profile = result['profile']
            old = data['targets'].get(profile, {})
            if result['code'] in INCONCLUSIVE:
                if old:
                    old['last_attempt'] = result
                else:
                    data['targets'][profile] = {'code': 'pending', 'stage': result['stage'],
                        'last_attempt': result, 'policy_version': policy.version}
                continue
            value = dict(result)
            value['restriction_count'] = old.get('restriction_count', 0) + 1 if result['code'] == 'target_restricted' else 0
            if value['restriction_count']:
                backoff = (120, 300, 900)[min(2, value['restriction_count'] - 1)]
                value['retry_at'] = result['checked_at'] + max(backoff, result.get('retry_after', 0))
            elif result['code'] in ('auth_required', 'auth_failed', 'tls_invalid'):
                value['retry_at'] = result['checked_at'] + policy.unavailable_interval
            else:
                value['retry_at'] = 0
            value['last_success'] = result['checked_at'] if result['code'] == 'available' else old.get('last_success', 0)
            value['valid_until'] = result['checked_at'] + policy.fresh_seconds if result['code'] == 'available' else 0
            data['targets'][profile] = value
        data['last_round_id'] = round_id
    endpoint.last_round_id = round_id
    if attempted:
        endpoint.discovery_cursor += 1
    endpoint.updated_at = now
    # An incomplete discovery or unknown target health never authorizes hard deletion.
    discovery = set(policy.variants_for(endpoint)) <= set(endpoint.discovered)
    health_ok = all((target_health or {}).get(p) == 'healthy' for p in policy.profiles_for(endpoint.country))
    dead = bool(endpoint.variants) and discovery and health_ok and all(
        data.get('hard_failures', 0) >= policy.max_hard_failures for data in endpoint.variants.values())
    return endpoint, dead


def public_endpoint(endpoint, policy, now, profile=None, variants=None):
    data = endpoint.to_dict()
    selected = {v: d for v, d in endpoint.variants.items() if variants is None or v in variants}
    profiles = (profile,) if profile else policy.profiles_for(endpoint.country)
    routes = [(v, p) for v, p in available_routes(endpoint, policy, now, profile=profile) if v in selected]
    targets = [r for v in selected.values() for p, r in v.get('targets', {}).items() if p in profiles]
    codes = {r.get('code') for r in targets}
    if routes:
        status = 'available'
    elif any(eligible(endpoint, v, 'http_only', policy, now) for v in selected):
        status = 'http_only'
    elif not targets or codes <= {'pending'}:
        status = 'pending'
    elif any(r.get('code') == 'available' and min(r.get('valid_until', 0), r.get('checked_at', 0) + policy.fresh_seconds) <= now for r in targets):
        status = 'stale'
    elif any(v.get('suspended') and v.get('hard_failures', 0) < policy.max_hard_failures for v in selected.values()):
        status = 'suspect'
    else:
        status = next((c for c in ('target_restricted', 'auth_required', 'auth_failed', 'tls_invalid', 'content_inconclusive') if c in codes), 'unavailable')
    data['status'] = status
    data.pop('credential_ref', None)
    data.pop('indexed_country', None)
    data['available_routes'] = [{'variant': v, 'profile': p} for v, p in routes]
    for variant in data['variants'].values():
        for result in variant.get('targets', {}).values():
            if 'valid_until' in result:
                result['valid_until'] = min(result['valid_until'], result.get('checked_at', 0) + policy.fresh_seconds)
    return data
