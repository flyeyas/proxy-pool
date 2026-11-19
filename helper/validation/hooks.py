"""Explicit trusted, installed post-validation hooks; never upgrade a failed probe."""
from importlib import import_module


def apply_hooks(candidate, result, policy):
    if not result.passed or result.profile not in ('domestic', 'overseas'):
        return result
    for entry in policy.hooks:
        try:
            module, name = entry.rsplit(':', 1)
            verdict = getattr(import_module(module), name)(candidate.public_dict(), result.to_dict())
            if verdict is False:
                result.code, result.stage = 'custom_rejected', 'custom_hook'
                return result
            if verdict is not True:
                raise ValueError('custom hook must return a boolean')
        except Exception:
            result.code, result.stage = 'environment_error', 'custom_hook'
            return result
    return result
