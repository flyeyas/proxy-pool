<script setup>
import { ref, computed } from 'vue'
import { api } from '../api'
import { usePolling } from '../composables/usePolling'

const config = ref(null)
const status = ref({ mode: 'legacy', online: false })
const records = ref([])
const selected = ref(null)
const message = ref('')
const busy = ref(false)
const nextCursor = ref(null)
const cursor = ref(0)
const history = ref([])
const total = ref(0)
const job = ref(null)
const jobId = ref('')
const formInitialized = ref(false)
const form = ref({ workers: 4, target_rps: 2, connect_timeout: 3, read_timeout: 5, round_deadline: 30 })
const labels = { workers: '检测并发', target_rps: '每站每秒请求数', connect_timeout: '连接超时（秒）',
  read_timeout: '读取超时（秒）', round_deadline: '整轮截止（秒）' }
const filter = ref({ protocol: '', profile: '', status: 'all', region: '', max_latency_ms: '' })
const configured = computed(() => config.value?.mode !== 'legacy')
const effectiveMode = computed(() => status.value.mode || 'legacy')

async function load() {
  const [settings, snapshot] = await Promise.all([api.validationConfig(), api.validationStatus()])
  config.value = settings.data
  if (!formInitialized.value) {
    beginEdit()
    formInitialized.value = true
  }
  status.value = snapshot.data
  if (configured.value) {
    const response = await api.validationProxies({ ...filter.value, cursor: cursor.value, page_size: 50 })
    records.value = response.data.proxies
    nextCursor.value = response.data.next_cursor
    total.value = response.data.catalog_total
  } else {
    records.value = []
  }
  if (jobId.value) job.value = (await api.validationJobStatus(jobId.value)).data
}
const polling = usePolling(load, 5000)
polling.start()

function beginEdit() {
  if (!config.value) return
  for (const key of Object.keys(form.value)) form.value[key] = config.value.policy[key]
}

async function execute(fn) {
  busy.value = true
  message.value = ''
  try {
    await fn()
    await polling.run()
  } catch (error) {
    message.value = error.message
  } finally {
    busy.value = false
  }
}

function enableShadow() {
  return execute(async () => {
    await api.validationSaveConfig({ mode: 'shadow' })
    message.value = '已启用影子检测；生产接口继续使用旧规则。'
  })
}

function save() {
  return execute(async () => {
    const locked = new Set(config.value?.locked || [])
    const policy = Object.fromEntries(Object.entries(form.value).filter(([key]) => !locked.has(key)))
    await api.validationSaveConfig({ policy })
    message.value = '配置已保存；生效版本更新后，受影响结果会重新检测。'
  })
}

function startJob(action) {
  return execute(async () => {
    if (['activate', 'rollback'].includes(action) && !window.confirm(action === 'activate'
      ? '覆盖率与可用数门槛通过后，将排空旧检测任务并切换生产接口，继续？'
      : '恢复旧检测模式和切换前的代理池备份，继续？')) return
    const result = await api.validationJob({ action })
    jobId.value = result.data.job_id
    message.value = '任务已提交，可在下方查看状态。'
  })
}

function applyFilter() {
  cursor.value = 0
  history.value = []
  return execute(load)
}

function next() {
  history.value.push(cursor.value)
  cursor.value = nextCursor.value
  return execute(load)
}

function previous() {
  cursor.value = history.value.pop() || 0
  return execute(load)
}

function routes(record) {
  return record.available_routes.map(route => route.variant + ' / ' + route.profile).join(', ') || '—'
}
</script>

<template>
  <div>
    <div class="cards">
      <div class="card"><div class="stat-value">{{ effectiveMode }}</div><div class="stat-label">生效检测模式</div></div>
      <div class="card"><div class="stat-value">{{ status.online ? '在线' : '未运行' }}</div><div class="stat-label">检测协调器</div></div>
      <div class="card"><div class="stat-value">{{ status.workers || 0 }} / {{ status.worker_limit || 0 }}</div><div class="stat-label">运行中 Worker</div></div>
      <div class="card"><div class="stat-value">{{ Math.round(status.oldest_due_delay || 0) }} 秒</div><div class="stat-label">最老待测任务延迟</div></div>
    </div>
    <div v-if="message" class="flash" style="margin-bottom: 12px">{{ message }}</div>
    <div class="card">
      <div class="toolbar">
        <h3 class="card-title" style="margin: 0">检测策略</h3>
        <div class="spacer"></div>
        <button v-if="!configured" class="btn btn-primary" :disabled="busy" @click="enableShadow">启用影子检测</button>
        <button class="btn" :disabled="busy" @click="polling.run">刷新状态</button>
        <button class="btn" @click="beginEdit">读取当前参数</button>
      </div>
      <p>国内：{{ config?.policy?.targets?.domestic?.url || 'HTTPS 腾讯' }}；国外：{{ config?.policy?.targets?.overseas?.url || 'HTTPS Google' }}。地区未知时分别检测，不能依据网站可达性猜测国家。</p>
      <p>目标状态：国内 {{ status.targets?.domestic || 'unknown' }} ／ 国外 {{ status.targets?.overseas || 'unknown' }}</p>
      <p v-if="status.oldest_due_delay > 300" class="flash">待测任务已积压超过 5 分钟。两分钟是调度间隔，实际完成速度取决于代理响应和检测资源。</p>
      <p class="mono">期望版本：{{ config?.config_version || '—' }} ／ 生效版本：{{ status.effective_version || '—' }}</p>
      <div class="toolbar" style="flex-wrap: wrap">
        <label v-for="(value, key) in form" :key="key" style="display: grid; gap: 6px">
          {{ labels[key] }}
          <input v-model.number="form[key]" type="number" step="any" :disabled="config?.locked?.includes(key)" style="width: 125px" />
        </label>
        <button class="btn" :disabled="busy" @click="save">保存参数</button>
      </div>
      <div v-if="configured" class="toolbar" style="flex-wrap: wrap">
        <button class="btn" :disabled="busy" @click="startJob('import')">导入历史代理</button>
        <button class="btn" :disabled="busy" @click="startJob('recheck')">安排重新校验</button>
        <button v-if="effectiveMode === 'shadow'" class="btn btn-primary" :disabled="busy" @click="startJob('activate')">切换新版检测</button>
        <button v-if="effectiveMode === 'active'" class="btn" :disabled="busy" @click="startJob('rollback')">回滚旧检测</button>
      </div>
      <pre v-if="job" style="white-space: pre-wrap">{{ JSON.stringify(job, null, 2) }}</pre>
    </div>
    <div class="card">
      <div class="toolbar" style="flex-wrap: wrap">
        <h3 class="card-title" style="margin: 0">V2 代理与检测证据</h3>
        <select v-model="filter.protocol"><option value="">全部连接协议</option><option>http</option><option>https</option><option>socks4</option><option>socks5</option></select>
        <select v-model="filter.profile"><option value="">默认地区目标</option><option value="domestic">国内目标</option><option value="overseas">国外目标</option></select>
        <select v-model="filter.status"><option v-for="state in ['all', 'available', 'pending', 'suspect', 'target_restricted', 'auth_required', 'auth_failed', 'tls_invalid', 'content_inconclusive', 'stale', 'http_only', 'unavailable']" :key="state">{{ state }}</option></select>
        <input v-model="filter.region" placeholder="地区码，如 CN、US" style="width: 150px" />
        <input v-model.number="filter.max_latency_ms" type="number" min="0" step="1" placeholder="最大耗时（毫秒）" aria-label="最大耗时（毫秒）" style="width: 150px" />
        <button class="btn" :disabled="busy || !configured" @click="applyFilter">筛选</button>
      </div>
      <p v-if="!configured">当前为旧版检测。启用影子模式后可对照新规则，不会立即切换生产代理接口。</p>
      <div v-else class="table-wrap">
        <table><thead><tr><th>代理</th><th>地区与来源</th><th>状态</th><th>已通过的协议 / 目标</th><th></th></tr></thead>
          <tbody><tr v-for="record in records" :key="record.endpoint_id">
            <td class="mono">{{ record.host }}:{{ record.port }}</td>
            <td>{{ record.country || 'unknown' }} / {{ record.country_source }}</td>
            <td>{{ record.status }}</td><td>{{ routes(record) }}</td>
            <td><button class="btn" @click="selected = selected?.endpoint_id === record.endpoint_id ? null : record">详情</button></td>
          </tr></tbody>
        </table>
        <p v-if="!records.length">本页没有符合条件的结果。筛选按候选目录分页，可继续下一页。</p>
        <div class="toolbar"><span>候选目录 {{ total }} 条</span><div class="spacer"></div>
          <button class="btn" :disabled="!history.length || busy" @click="previous">上一页</button>
          <button class="btn" :disabled="nextCursor === null || busy" @click="next">下一页</button>
        </div>
      </div>
      <pre v-if="selected" style="white-space: pre-wrap; overflow-wrap: anywhere; max-height: 600px; overflow: auto">{{ JSON.stringify(selected, null, 2) }}</pre>
    </div>
  </div>
</template>
