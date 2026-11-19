"""Evidence-based target protection; lack of reference proxies is not health."""
import json
import time

TARGET_ERRORS = {'target_restricted', 'target_http_error', 'read_timeout',
                 'content_inconclusive', 'tls_invalid', 'deadline_exceeded'}


class TargetHealth:
    def __init__(self, repository):
        self.repository = repository

    def read(self, profile):
        raw = self.repository.redis.get(self.repository.prefix + 'health:' + profile)
        return json.loads(raw) if raw else {'state': 'unknown', 'window': {}, 'references': {},
                                           'recovery': {}, 'next_canary': 0, 'cooldown': 60}

    def observe(self, endpoint_id, result, now=None):
        now = time.time() if now is None else now
        profile, code = result['profile'], result['code']
        if profile not in ('domestic', 'overseas'):
            return 'unknown'
        data = self.read(profile)
        data['window'] = {k: v for k, v in data['window'].items() if v['time'] > now - 60}
        data['references'] = {k: v for k, v in data['references'].items() if v > now - 600}
        if data['state'] in ('open', 'half_open') and len(data['references']) < 3:
            data.update(state='unknown', recovery={})
        if code in ('deferred', 'environment_error', 'unsupported'):
            return data['state']
        data['window'][endpoint_id] = {'time': now, 'code': code}
        prior_reference = endpoint_id in data['references']
        if code == 'available':
            data['references'][endpoint_id] = now
        if len(data['references']) > 128:
            data['references'] = dict(sorted(data['references'].items(), key=lambda item: item[1], reverse=True)[:128])
        if data['state'] in ('open', 'half_open'):
            if prior_reference and code == 'available':
                data['recovery'][endpoint_id] = now
                data['state'] = 'half_open'
                if len(data['recovery']) >= 2:
                    data.update(state='healthy', recovery={}, cooldown=60)
                    # Start a new observation window after successful recovery.
                    data['window'] = {endpoint_id: {'time': now, 'code': code}}
            elif prior_reference and code in TARGET_ERRORS:
                data.update(state='open', recovery={}, next_canary=now + data['cooldown'])
                data['cooldown'] = min(900, data['cooldown'] * 2)
        else:
            window = data['window']
            bad = sum(v['code'] in TARGET_ERRORS for v in window.values())
            abnormal = len(window) >= 10 and bad / len(window) >= 0.8
            if abnormal:
                reference_failures = sum(k in data['references'] and v['code'] in TARGET_ERRORS
                                         for k, v in window.items())
                if len(data['references']) >= 3 and reference_failures >= 2:
                    data.update(state='open', recovery={}, next_canary=now + 60)
                else:
                    data['state'] = 'suspect' if len(data['references']) >= 3 else 'unknown'
            else:
                data['state'] = 'healthy' if len(data['references']) >= 3 else 'unknown'
        self.repository.redis.set(self.repository.prefix + 'health:' + profile,
                                   json.dumps(data), ex=3600)
        return data['state']

    def allow(self, profile, endpoint_id, now=None):
        now = time.time() if now is None else now
        data = self.read(profile)
        if data['state'] in ('open', 'half_open'):
            references = {k: v for k, v in data['references'].items() if v > now - 600}
            if len(references) < 3:
                # Losing reference proxies is missing evidence, not a permanent
                # outage. Allow bounded discovery to rebuild the reference set.
                data.update(state='unknown', recovery={}, references=references)
                self.repository.redis.set(self.repository.prefix + 'health:' + profile,
                                          json.dumps(data), ex=3600)
                return True
        if data['state'] not in ('open', 'half_open'):
            return True
        if endpoint_id not in data['references'] or now < data['next_canary']:
            return False
        return bool(self.repository.redis.set(self.repository.prefix + 'canary:' + profile,
                                              '1', nx=True, ex=30))

    def states(self):
        return {p: self.read(p)['state'] for p in ('domestic', 'overseas')}

    def schedule(self, now=None):
        """Actively enqueue reference probes; never repeatedly select one survivor."""
        now = time.time() if now is None else now
        repo = self.repository
        for profile in ('domestic', 'overseas'):
            data = self.read(profile)
            if data['state'] not in ('suspect', 'open', 'half_open'):
                continue
            refs = [key for key, checked in data['references'].items() if checked > now - 600][:128]
            if refs:
                pipe = repo.redis.pipeline(transaction=False)
                for key in refs:
                    pipe.hexists(repo.records, key)
                refs = [key for key, exists in zip(refs, pipe.execute()) if exists]
            if len(refs) < 3:
                data.update(state='unknown', recovery={}, references={k: data['references'][k] for k in refs})
                repo.redis.set(repo.prefix + 'health:' + profile, json.dumps(data), ex=3600)
                continue
            if data['state'] != 'suspect' and now < data['next_canary']:
                continue
            if not repo.redis.set(repo.prefix + 'health_schedule:' + profile, '1', nx=True, ex=30):
                continue
            refs.sort(key=lambda key: (key in data['recovery'], data['window'].get(key, {}).get('time', 0), key))
            # Open/half-open allow two requests per minute; use a distinct,
            # least-recent reference for the next slot, not a fixed lexical first.
            repo.prioritize(refs[:3] if data['state'] == 'suspect' else refs[:1], now, profiles=(profile,))
