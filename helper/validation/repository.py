"""Redis 8 repository. Bounded CAS scripts fence every destructive operation."""
import json
import time
from uuid import uuid4

from redis import Redis

from .models import PROFILES, VARIANTS, ProxyEndpointV2
from .policy import ValidationPolicy
from .state import apply_results, available_routes, eligible

VERIFIED_LUA = '''
local function verified(record)
 if tonumber(record.last_success_at or 0)>0 then return true end
 for _,variant in pairs(record.variants or {}) do
  for profile,result in pairs(variant.targets or {}) do
   if (profile=='domestic' or profile=='overseas') and tonumber(result.last_success or 0)>0 then return true end
  end
 end
 return false
end
'''

UPSERT = VERIFIED_LUA + '''
local current=redis.call('HGET',KEYS[1],ARGV[1])
local incoming=cjson.decode(ARGV[2])
local new_evidence=not current
if current then
 local old=cjson.decode(current)
 if not old.country_hints then
  old.country_hints={}
  if old.country_source=='source' and old.country~='' then table.insert(old.country_hints,old.country) end
 end
 for _,field in ipairs({'sources','protocol_hints','country_hints'}) do
  old[field]=old[field] or {}
  local seen={}
  for _,v in ipairs(old[field]) do seen[v]=true end
  for _,v in ipairs(incoming[field] or {}) do
   if not seen[v] then
    table.insert(old[field],v); seen[v]=true
    if field~='sources' then new_evidence=true end
   end
  end
 end
 if #old.country_hints>1 and (old.country_source=='source' or old.country_source=='conflict') then
  old.country='';old.endpoint_country='';old.country_source='conflict';old.country_confidence='unknown'
 elseif old.country=='' and incoming.country~='' then
  old.country=incoming.country; old.endpoint_country=incoming.endpoint_country
  old.country_source=incoming.country_source; old.country_confidence=incoming.country_confidence
  new_evidence=true
 end
 if old.explicit_scheme=='' and incoming.explicit_scheme~='' then old.explicit_scheme=incoming.explicit_scheme;new_evidence=true end
 old.updated_at=incoming.updated_at
 incoming=old
elseif redis.call('HLEN',KEYS[1])>=tonumber(ARGV[4]) then return 0 end
redis.call('HSET',KEYS[1],ARGV[1],cjson.encode(incoming))
if not current and incoming.credential_ref~='' then redis.call('HINCRBY',KEYS[6],incoming.credential_ref,1) end
redis.call('ZADD',KEYS[3],'NX',incoming.created_at,ARGV[1])
if redis.call('EXISTS',KEYS[4])==0 then
 local due=redis.call('ZSCORE',KEYS[2],ARGV[1])
 if not due then due=tonumber(ARGV[3])
 elseif new_evidence then due=math.min(tonumber(due),tonumber(ARGV[3])) end
 redis.call('ZADD',KEYS[2],due,ARGV[1])
 local queue=verified(incoming) and KEYS[8] or KEYS[7]
 local other=queue==KEYS[7] and KEYS[8] or KEYS[7]
 redis.call('ZADD',queue,due,ARGV[1]); redis.call('ZREM',other,ARGV[1])
end
if ARGV[5]~='' then redis.call('HSET',KEYS[5],incoming.credential_ref,ARGV[5]) end
return current and 2 or 1
'''

CLAIM = VERIFIED_LUA + '''
if redis.call('GET',KEYS[3])~=ARGV[3] then return nil end
local control=redis.call('GET',KEYS[4])
if not control then return nil end
local state=cjson.decode(control)
if state.epoch~=ARGV[4] or state.mode=='legacy' or state.draining then return nil end
if redis.call('ZCOUNT',KEYS[5],'-inf',ARGV[1])+redis.call('ZCOUNT',KEYS[6],'-inf',ARGV[1])==0 then return nil end
local first=redis.call('INCR',KEYS[7])%5==0 and KEYS[5] or KEYS[6]
local second=first==KEYS[5] and KEYS[6] or KEYS[5]
for _,queue in ipairs({first,second}) do
 local ids=redis.call('ZRANGEBYSCORE',queue,'-inf',ARGV[1],'LIMIT',0,32)
 for _,id in ipairs(ids) do
  local record=redis.call('HGET',KEYS[1],id)
  if record then
   local correct=verified(cjson.decode(record)) and KEYS[6] or KEYS[5]
   if correct~=queue then
    local due=redis.call('ZSCORE',KEYS[2],id) or ARGV[1]
    redis.call('ZREM',queue,id); redis.call('ZADD',correct,due,id)
   elseif redis.call('SET',ARGV[5]..id,ARGV[2],'NX','PX',45000) then
    redis.call('ZADD',KEYS[2],tonumber(ARGV[1])+45,id)
    redis.call('ZADD',queue,tonumber(ARGV[1])+45,id)
    return {id,record,queue==KEYS[5] and 'new' or 'use'}
   end
  else
   redis.call('ZREM',KEYS[2],id);redis.call('ZREM',KEYS[5],id);redis.call('ZREM',KEYS[6],id)
  end
 end
end
return nil
'''

RENEW = '''
if redis.call('GET',KEYS[1])~=ARGV[1] then return 0 end
redis.call('PEXPIRE',KEYS[1],ARGV[2]); return 1
'''

COMMIT = '''
if redis.call('GET',KEYS[5])~=ARGV[6] or redis.call('GET',KEYS[3])~=ARGV[4] then return -2 end
local control=redis.call('GET',KEYS[4])
if not control or cjson.decode(control).epoch~=ARGV[5] then return -2 end
if redis.call('HGET',KEYS[1],ARGV[1])~=ARGV[2] then return -1 end
local dead=ARGV[10]=='1'
if dead then
 redis.call('HDEL',KEYS[1],ARGV[1]); redis.call('ZREM',KEYS[2],ARGV[1]); redis.call('ZREM',KEYS[8],ARGV[1])
 redis.call('SET',KEYS[6],ARGV[4],'EX',86400)
 redis.call('ZREM',KEYS[47],ARGV[1]);redis.call('ZREM',KEYS[48],ARGV[1])
 local ref=cjson.decode(ARGV[2]).credential_ref
 if ref~='' and redis.call('HINCRBY',KEYS[22],ref,-1)<=0 then
  redis.call('HDEL',KEYS[22],ref); redis.call('HDEL',KEYS[21],ref)
 end
else
 redis.call('HSET',KEYS[1],ARGV[1],ARGV[3]); redis.call('ZADD',KEYS[2],ARGV[7],ARGV[1])
 local queue=ARGV[13]=='use' and KEYS[48] or KEYS[47]
 local other=queue==KEYS[47] and KEYS[48] or KEYS[47]
 redis.call('ZADD',queue,ARGV[7],ARGV[1]);redis.call('ZREM',other,ARGV[1])
end
local expires=cjson.decode(ARGV[12])
for i=1,12 do
 redis.call('ZREM',KEYS[i+22],ARGV[1])
 if dead or tonumber(expires[i])<=tonumber(ARGV[11]) then redis.call('ZREM',KEYS[i+8],ARGV[1])
 else redis.call('ZADD',KEYS[i+8],expires[i],ARGV[1]) end
 if dead or tonumber(expires[i])<=tonumber(ARGV[11]) then redis.call('ZREM',KEYS[i+34],ARGV[1])
 else redis.call('ZADD',KEYS[i+34],expires[i],ARGV[1]) end
end
if cjson.decode(control).mode=='active' then
 redis.call('HDEL',KEYS[7],ARGV[8])
 if not dead and ARGV[9]~='' then redis.call('HSET',KEYS[7],ARGV[8],ARGV[9]) end
end
redis.call('DEL',KEYS[3]); return 1
'''

REMOVE = '''
if redis.call('HGET',KEYS[1],ARGV[1])~=ARGV[2] then return 0 end
if ARGV[4]~='' and redis.call('GET',KEYS[4])~=ARGV[4] then return 0 end
redis.call('HDEL',KEYS[1],ARGV[1]); redis.call('ZREM',KEYS[2],ARGV[1]); redis.call('DEL',KEYS[3])
redis.call('ZREM',KEYS[8],ARGV[1]); redis.call('SET',KEYS[6],ARGV[3],'EX',86400)
local ref=cjson.decode(ARGV[2]).credential_ref
if ref~='' and redis.call('HINCRBY',KEYS[22],ref,-1)<=0 then
 redis.call('HDEL',KEYS[22],ref); redis.call('HDEL',KEYS[21],ref)
end
for i=9,20 do redis.call('ZREM',KEYS[i],ARGV[1]) end
for i=23,34 do redis.call('ZREM',KEYS[i],ARGV[1]) end
redis.call('ZREM',KEYS[35],ARGV[1]);redis.call('ZREM',KEYS[36],ARGV[1])
local control=redis.call('GET',KEYS[4])
if control and cjson.decode(control).mode=='active' then redis.call('HDEL',KEYS[7],ARGV[5]) end
return 1
'''


def encode(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False)


class ValidationRepository:
    def __init__(self, url=None, table=None, client=None):
        if url is None or table is None:
            from handler.configHandler import ConfigHandler
            conf = ConfigHandler()
            url, table = url or conf.dbConn, table or conf.tableName
        if not url.startswith(('redis://', 'rediss://')):
            raise ValueError('validation V2 requires Redis; SSDB supports legacy mode only')
        self.redis = client if client is not None else Redis.from_url(
            url, decode_responses=True, socket_timeout=5, socket_connect_timeout=3, protocol=2)
        self.table = table
        self.prefix = table + ':v2:'
        self.records, self.due = self.prefix + 'endpoints', self.prefix + 'due'
        self.new_due, self.use_due = self.prefix + 'due:new', self.prefix + 'due:use'
        self.catalog, self.control_key = self.prefix + 'catalog', self.prefix + 'control'
        self.leader_key = self.prefix + 'leader'
        self.credentials_key = self.prefix + 'credentials'
        self.routes = [(v, p) for v in VARIANTS for p in PROFILES]

    def keys(self, endpoint_id):
        return [self.records, self.due, self.prefix + 'lease:' + endpoint_id,
                self.control_key, self.leader_key, self.prefix + 'tombstone:' + endpoint_id,
                self.table, self.catalog] + [self.ready_key(v, p) for v, p in self.routes] + [
                    self.credentials_key, self.prefix + 'credential_counts']

    def ready_key(self, variant, profile, country=None):
        key = self.prefix + 'ready:' + profile + ':' + variant
        return key + ':country:' + country if country is not None else key

    def country_keys(self, country):
        return [self.ready_key(v, p, country or 'unknown') for v, p in self.routes]

    def control(self):
        raw = self.redis.get(self.control_key)
        return json.loads(raw) if raw else None

    def update_control(self, expected, value, owner=None):
        script = """
        if redis.call('GET',KEYS[1])~=ARGV[1] then return 0 end
        if ARGV[3]~='' and redis.call('GET',KEYS[2])~=ARGV[3] then return 0 end
        redis.call('SET',KEYS[1],ARGV[2]); return 1
        """
        raw = self.redis.get(self.control_key)
        if not raw or json.loads(raw) != json.loads(encode(expected)):
            return False
        return bool(self.redis.eval(script, 2, self.control_key, self.leader_key,
                                    raw, encode(value), owner or ''))

    def reschedule_all(self):
        cursor = 0
        while True:
            endpoints, cursor, _ = self.page(cursor, 200)
            mapping = {e.endpoint_id: time.time() for e in endpoints}
            if mapping:
                pipe = self.redis.pipeline()
                pipe.zadd(self.due, mapping)
                for endpoint in endpoints:
                    queue = self.use_due if endpoint.last_success_at else self.new_due
                    other = self.new_due if endpoint.last_success_at else self.use_due
                    pipe.zadd(queue, {endpoint.endpoint_id: mapping[endpoint.endpoint_id]})
                    pipe.zrem(other, endpoint.endpoint_id)
                pipe.execute()
            if cursor is None:
                break

    def ensure_queue_indexes(self):
        if self.redis.get(self.prefix + 'queue_schema') != '1':
            self.reschedule_all()
            self.redis.set(self.prefix + 'queue_schema', '1')

    def prioritize(self, endpoint_ids, now=None, profiles=()):
        ids = list(dict.fromkeys(endpoint_ids))[:3]
        if not ids:
            return 0
        script = VERIFIED_LUA + """
        local head=redis.call('ZRANGE',KEYS[2],0,0,'WITHSCORES')
        local score=tonumber(ARGV[1])
        if #head>0 then score=math.min(score,tonumber(head[2])) end
        score=math.max(0,score-1)
        local count=0
        for i=4,#ARGV do
          local id=ARGV[i];local raw=redis.call('HGET',KEYS[1],id)
          if raw then
            local record=cjson.decode(raw)
            local pending=record.pending_profiles or {};local seen={}
            for _,p in ipairs(pending) do seen[p]=true end
            for _,p in ipairs(cjson.decode(ARGV[3])) do if not seen[p] then table.insert(pending,p);seen[p]=true end end
            record.pending_profiles=pending
            redis.call('HSET',KEYS[1],id,cjson.encode(record))
            if redis.call('EXISTS',ARGV[2]..id)==0 then
              local queue=verified(record) and KEYS[4] or KEYS[3]
              redis.call('ZADD',KEYS[2],score,id);redis.call('ZADD',queue,score,id)
            end
            count=count+1
          end
        end
        return count
        """
        return self.redis.eval(script, 4, self.records, self.due, self.new_due, self.use_due,
                               time.time() if now is None else now, self.prefix + 'lease:', encode(list(profiles)), *ids)

    def update_region(self, endpoint_id, country, now=None):
        if not country:
            return False
        now = time.time() if now is None else now
        script = VERIFIED_LUA + """
        if redis.call('HGET',KEYS[1],ARGV[1])~=ARGV[2] then return 0 end
        redis.call('HSET',KEYS[1],ARGV[1],ARGV[3]);
        if redis.call('EXISTS',KEYS[3])==0 then
          local queue=verified(cjson.decode(ARGV[3])) and KEYS[5] or KEYS[4]
          local other=queue==KEYS[4] and KEYS[5] or KEYS[4]
          redis.call('ZADD',KEYS[2],ARGV[4],ARGV[1]);redis.call('ZADD',queue,ARGV[4],ARGV[1])
          redis.call('ZREM',other,ARGV[1])
        end
        return 1
        """
        for _ in range(8):
            raw = self.redis.hget(self.records, endpoint_id)
            if not raw:
                return False
            endpoint = ProxyEndpointV2.from_dict(json.loads(raw))
            if endpoint.country_source == 'exit_verified':
                return False
            endpoint.country, endpoint.endpoint_country = country, country
            endpoint.country_source, endpoint.country_confidence = 'ip_lookup', 'inferred'
            endpoint.country_checked_at = now
            if self.redis.eval(script, 5, self.records, self.due, self.prefix + 'lease:' + endpoint_id,
                               self.new_due, self.use_due,
                               endpoint_id, raw, encode(endpoint.to_dict()), now):
                return True
        return False

    def guarded_legacy_put(self, proxy):
        script = """
        local raw=redis.call('GET',KEYS[1])
        if raw then
          local state=cjson.decode(raw)
          if state.mode=='active' or state.draining then return 0 end
        end
        return redis.call('HSET',KEYS[2],ARGV[1],ARGV[2])
        """
        return self.redis.eval(script, 2, self.control_key, self.table, proxy.proxy, proxy.to_json)

    def guarded_legacy_delete(self, proxy):
        script = """
        local raw=redis.call('GET',KEYS[1])
        if raw then local c=cjson.decode(raw); if c.mode=='active' or c.draining then return 0 end end
        return redis.call('HDEL',KEYS[2],ARGV[1])
        """
        return self.redis.eval(script, 2, self.control_key, self.table, proxy.proxy)

    def initialize(self, mode, policy):
        if mode not in ('legacy', 'shadow'):
            raise ValueError('first activation requires a verified migration')
        value = {'mode': mode, 'epoch': uuid4().hex, 'policy': policy.to_dict(),
                 'policy_version': policy.version, 'config_version': policy.fingerprint}
        self.redis.set(self.control_key, encode(value), nx=True)
        return self.control()

    def acquire_leader(self, owner):
        return bool(self.redis.set(self.leader_key, owner, nx=True, px=45000))

    def renew(self, key, owner):
        return bool(self.redis.eval(RENEW, 1, key, owner, 45000))

    def release_leader(self, owner):
        return self.redis.eval("if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) end return 0", 1, self.leader_key, owner)

    def submit(self, candidate, policy, now=None):
        now = time.time() if now is None else now
        endpoint = ProxyEndpointV2.from_candidate(candidate, now)
        credentials = encode({'username': candidate.username, 'password': candidate.password}) if candidate.username is not None else ''
        status = self.redis.eval(UPSERT, 8, self.records, self.due, self.catalog,
                               self.prefix + 'lease:' + endpoint.endpoint_id, self.credentials_key,
                               self.prefix + 'credential_counts', self.new_due, self.use_due,
                               endpoint.endpoint_id, encode(endpoint.to_dict()), now, policy.max_candidates, credentials)
        if not status:
            from .events import emit
            emit('candidate_queue_full', endpoint_id=endpoint.endpoint_id, sources=endpoint.sources)
        return status

    def load(self, endpoint_id):
        raw = self.redis.hget(self.records, endpoint_id)
        return ProxyEndpointV2.from_dict(json.loads(raw)) if raw else None

    def credentials(self, endpoint):
        resolved = getattr(endpoint, '_resolved_credentials', None)
        if resolved is not None:
            return resolved
        raw = self.redis.hget(self.credentials_key, endpoint.credential_ref) if endpoint.credential_ref else None
        return json.loads(raw) if raw else {}

    def claim(self, owner, control, now=None):
        now = time.time() if now is None else now
        token = uuid4().hex
        value = self.redis.eval(CLAIM, 7, self.records, self.due, self.leader_key, self.control_key,
                                self.new_due, self.use_due, self.prefix + 'queue_cursor',
                                now, token, owner, control['epoch'], self.prefix + 'lease:')
        if not value:
            return None
        endpoint = ProxyEndpointV2.from_dict(json.loads(value[1]))
        return {'endpoint': endpoint, 'token': token, 'round_id': uuid4().hex,
                'generation': endpoint.generation, 'epoch': control['epoch'], 'queue': value[2]}

    def permit(self, host, rps, admission=False):
        host = {'www.qq.com': 'qq.com', 'www.google.com': 'google.com'}.get(host, host)
        bucket = 'admit:' if admission else 'rate:'
        return bool(self.redis.set(self.prefix + bucket + host, '1', nx=True,
                                   px=max(1, int(1000 / rps + 0.999))))

    def legacy_projection(self, endpoint, policy, now):
        routes = available_routes(endpoint, policy, now, variant='http')
        candidate = endpoint.candidate(self.credentials(endpoint))
        address = candidate.proxy_url('http', credentials=True).split('://', 1)[1]
        if not routes:
            return address, ''
        times = [endpoint.variants[v]['targets'][p]['checked_at'] for v, p in routes]
        record = {'proxy': address, 'https': True, 'fail_count': 0, 'region': endpoint.country,
                  'anonymous': '', 'source': '/'.join(endpoint.sources), 'check_count': 0,
                  'last_status': True, 'last_time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(max(times)))}
        return address, encode(record)

    def commit(self, task, results, policy, owner, target_health=None, now=None):
        now = time.time() if now is None else now
        endpoint_id = task['endpoint'].endpoint_id
        for _ in range(8):
            expected = self.redis.hget(self.records, endpoint_id)
            if not expected:
                return False
            endpoint = ProxyEndpointV2.from_dict(json.loads(expected))
            if endpoint.generation != task['generation']:
                return False
            updated, dead = apply_results(endpoint, results, policy, task['round_id'], now, target_health)
            if updated is endpoint:
                return False
            last_success = max((r.get('last_success', 0) for d in updated.variants.values()
                                for r in d.get('targets', {}).values()), default=0)
            if now - (last_success or endpoint.created_at) >= policy.candidate_ttl:
                dead = True
            delay = policy.check_interval
            if updated.variants and all(d.get('hard_failures', 0) >= policy.max_hard_failures for d in updated.variants.values()):
                delay = max(delay, policy.unavailable_interval)
            if updated.pending_profiles:
                delay = min(delay, 1)
                if results and all(r['code'] == 'environment_error' for r in results):
                    delay = max(delay, 30)
            if updated.country != task['endpoint'].country:
                delay = min(delay, 1)
            if results and all(r['code'] == 'deferred' for r in results):
                waits = [r.get('retry_after', 0) for r in results]
                delay = max(0.05, min(waits)) if min(waits) > 0 else max(1, 1 / policy.target_rps)
            restricted = [r.get('retry_after', 0) for r in results if r['code'] == 'target_restricted']
            if restricted:
                delay = max(delay, min(86400, max(restricted)))
            if not available_routes(updated, policy, now):
                retry_times = [r.get('retry_at', 0) for d in updated.variants.values()
                               for p, r in d.get('targets', {}).items() if p in policy.profiles_for(updated.country)]
                future = [t for t in retry_times if t > now]
                if future and len(future) == len(retry_times):
                    delay = max(delay, min(future) - now)
            expires = [updated.variants.get(v, {}).get('targets', {}).get(p, {}).get('valid_until', 0)
                       if eligible(updated, v, p, policy, now) else 0 for v, p in self.routes]
            address, projection = self.legacy_projection(updated, policy, now)
            old_country = endpoint.indexed_country
            updated.indexed_country = updated.country or 'unknown'
            commit_keys = (self.keys(endpoint_id) + self.country_keys(old_country)
                           + self.country_keys(updated.indexed_country) + [self.new_due, self.use_due])
            result = self.redis.eval(COMMIT, 48, *commit_keys, endpoint_id, expected,
                                     encode(updated.to_dict()), task['token'], task['epoch'], owner,
                                     now + delay, address, projection, int(dead), now, encode(expires),
                                     'use' if updated.last_success_at else 'new')
            if result == 1:
                return True
            if result == -2:
                return False
        return False

    def remove(self, endpoint_id, expected=None, control_raw=''):
        current_control = self.redis.get(self.control_key)
        if current_control and json.loads(current_control).get('draining'):
            return False
        if control_raw and current_control != control_raw:
            return False
        control_raw = current_control or ''
        expected = expected or self.redis.hget(self.records, endpoint_id)
        if not expected:
            return False
        endpoint = ProxyEndpointV2.from_dict(json.loads(expected))
        candidate = endpoint.candidate(self.credentials(endpoint))
        address = candidate.proxy_url('http', credentials=True).split('://', 1)[1]
        remove_keys = self.keys(endpoint_id) + self.country_keys(endpoint.indexed_country) + [self.new_due, self.use_due]
        return bool(self.redis.eval(REMOVE, 36, *remove_keys, endpoint_id,
                                    expected, uuid4().hex, control_raw, address))

    def page(self, cursor=0, limit=50):
        ids = self.redis.zrange(self.catalog, cursor, cursor + limit - 1)
        values = self.redis.hmget(self.records, ids) if ids else []
        endpoints = [ProxyEndpointV2.from_dict(json.loads(v)) for v in values if v]
        total = self.redis.zcard(self.catalog)
        return endpoints, cursor + len(ids) if cursor + len(ids) < total else None, total

    def route(self, policy, profile=None, variant=None, region=None, consume=False, now=None, max_latency_ms=None, exclude=()):
        now = time.time() if now is None else now
        control_raw = self.redis.get(self.control_key)
        if not control_raw:
            return None
        if json.loads(control_raw)['mode'] not in ('shadow', 'active'):
            return None
        if json.loads(control_raw)['policy_version'] != policy.version:
            return None
        regions = {c.upper() for c in region.split(',')} if region else None
        base_choices = [(variant, profile)] if variant and profile else [
            (v, p) for v, p in self.routes if (not variant or v == variant) and (not profile or p == profile)]
        choices = [(v, p, country) for v, p in base_choices for country in (sorted(regions) if regions else [None])]
        from random import SystemRandom
        rng = SystemRandom()
        rng.shuffle(choices)
        # Bounded reads from live route indexes; stale and country-ineligible items are filtered.
        for v, p, country in choices:
            ids = self.redis.zrangebyscore(self.ready_key(v, p, country), '(' + str(now), '+inf', start=0, num=100)
            rng.shuffle(ids)
            for endpoint_id in ids:
                if endpoint_id in exclude:
                    continue
                raw = self.redis.hget(self.records, endpoint_id)
                if not raw:
                    continue
                endpoint = ProxyEndpointV2.from_dict(json.loads(raw))
                if regions and endpoint.country not in regions:
                    continue
                if not profile and p not in policy.profiles_for(endpoint.country):
                    continue
                if not eligible(endpoint, v, p, policy, now):
                    continue
                if max_latency_ms is not None and endpoint.variants[v]['targets'][p].get('elapsed_ms', 0) > max_latency_ms:
                    continue
                if consume:
                    credentials = self.credentials(endpoint)
                    if not self.remove(endpoint_id, raw, control_raw):
                        continue
                    endpoint._resolved_credentials = credentials
                if not consume and self.redis.get(self.control_key) != control_raw:
                    return None
                return endpoint, v, p
        return None
