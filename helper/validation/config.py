"""Desired validation configuration, separate from legacy lazy settings."""
import os
import secrets

from helper.runtimeConfig import readConfig, updateConfig
from .policy import ValidationPolicy

MODES = ('legacy', 'shadow', 'active')
ENV_OPTIONS = {'VALIDATION_WORKERS': ('workers', int),
               'VALIDATION_TARGET_RPS': ('target_rps', float),
               'VALIDATION_ROUND_DEADLINE': ('round_deadline', float)}


def desired():
    section = readConfig().get('validation', {})
    if not isinstance(section, dict):
        raise ValueError('invalid validation configuration')
    mode = os.environ.get('VALIDATION_MODE', section.get('mode', 'legacy'))
    if mode not in MODES:
        raise ValueError('invalid validation mode')
    options = dict(section.get('policy', {}))
    locked = []
    for env, (name, converter) in ENV_OPTIONS.items():
        if env in os.environ:
            options[name] = converter(os.environ[env])
            locked.append(name)
    policy = ValidationPolicy.from_dict(options)
    return {'mode': mode, 'policy': policy, 'locked': locked,
            'persisted_mode': section.get('mode', 'legacy'),
            'configured': bool(section.get('enabled_once') or section.get('mode') in ('shadow', 'active')
                               or mode in ('shadow', 'active')),
            'mode_locked': 'VALIDATION_MODE' in os.environ}


def save_desired(changes):
    if not isinstance(changes, dict) or not changes or set(changes) - {'mode', 'policy'}:
        raise ValueError('expected mode and/or policy')
    current = desired()
    if 'mode' in changes:
        if current['mode_locked']:
            raise ValueError('validation mode is locked by environment')
        if changes['mode'] not in MODES:
            raise ValueError('invalid validation mode')
    if 'policy' in changes and not isinstance(changes['policy'], dict):
        raise ValueError('policy must be an object')
    if set(changes.get('policy', {})) & set(current['locked']):
        raise ValueError('validation options are locked by environment')

    def mutate(config):
        section = dict(config.get('validation', {}))
        options = dict(section.get('policy', {}))
        options.update(changes.get('policy', {}))
        ValidationPolicy.from_dict(options)
        section['policy'] = options
        if 'mode' in changes:
            section['mode'] = changes['mode']
            if changes['mode'] in ('shadow', 'active'):
                section['enabled_once'] = True
        config['validation'] = section
    updateConfig(mutate)
    return desired()


def identity_key():
    """Persist a random HMAC key without overwriting Tokens or source settings."""
    def mutate(config):
        section = config.setdefault('validation_secrets', {})
        if 'identity_key' not in section:
            section['identity_key'] = secrets.token_hex(32)
        return bytes.fromhex(section['identity_key'])
    return updateConfig(mutate)
