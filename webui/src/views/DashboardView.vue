<script setup>
import { ref, computed } from 'vue'
import { api } from '../api'
import { usePolling } from '../composables/usePolling'

const stats = ref({ count: 0, http_type: {}, source: {} })
const proxies = ref([])
const filterType = ref('all') // all | https
const keyword = ref('')
const message = ref('')
const messageIsErr = ref(false)
const refreshBusy = ref(false)

const httpCount = computed(() => stats.value.http_type?.http || 0)
const httpsCount = computed(() => stats.value.http_type?.https || 0)
const sourceCount = computed(() => Object.keys(stats.value.source || {}).length)
const protoTotal = computed(() => httpCount.value + httpsCount.value || 1)
const sourceDist = computed(() =>
  Object.entries(stats.value.source || {}).sort((a, b) => b[1] - a[1])
)
const maxSourceCount = computed(() => (sourceDist.value[0] ? sourceDist.value[0][1] : 0))

const filteredProxies = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return proxies.value.filter((p) => {
    if (filterType.value === 'https' && !p.https) return false
    if (!kw) return true
    return (
      (p.proxy || '').toLowerCase().includes(kw) ||
      (p.source || '').toLowerCase().includes(kw) ||
      (p.region || '').toLowerCase().includes(kw)
    )
  })
})

async function loadData() {
  const [countData, allData] = await Promise.all([api.getCount(), api.getAll()])
  stats.value = countData
  proxies.value = allData
}

const polling = usePolling(loadData, 5000)
polling.start()

function flash(text, isErr = false) {
  message.value = text
  messageIsErr.value = isErr
}

async function triggerRefresh() {
  refreshBusy.value = true
  try {
    const r = await api.refresh()
    flash(r.src === 'started' ? '已触发抓取 + 校验，后台执行中' : '已有任务在执行中，请稍候')
  } catch (e) {
    flash('触发失败: ' + e.message, true)
  } finally {
    refreshBusy.value = false
  }
}

async function removeProxy(proxy) {
  if (!window.confirm(`确认删除代理 ${proxy} ?`)) return
  try {
    await api.deleteProxy(proxy)
    flash(`已删除 ${proxy}`)
    await polling.run()
  } catch (e) {
    flash('删除失败: ' + e.message, true)
  }
}
</script>

<template>
  <div>
    <div class="cards">
      <div class="card">
        <div class="stat-value">{{ stats.count }}</div>
        <div class="stat-label">代理总数</div>
      </div>
      <div class="card">
        <div class="stat-value">{{ httpCount }}</div>
        <div class="stat-label">HTTP</div>
      </div>
      <div class="card">
        <div class="stat-value">{{ httpsCount }}</div>
        <div class="stat-label">HTTPS</div>
      </div>
      <div class="card">
        <div class="stat-value">{{ sourceCount }}</div>
        <div class="stat-label">来源数</div>
      </div>
    </div>

    <div class="card">
      <div class="toolbar">
        <h3 class="card-title" style="margin: 0">协议分布</h3>
        <div class="spacer"></div>
        <button class="btn" :class="{ active: polling.running.value }" @click="polling.toggle()">
          {{ polling.running.value ? '⏸ 暂停刷新' : '▶ 自动刷新' }}
        </button>
        <button class="btn" :disabled="polling.loading.value" @click="polling.run()">
          ⟳ 立即刷新
        </button>
        <button class="btn btn-primary" :disabled="refreshBusy" @click="triggerRefresh">
          {{ refreshBusy ? '触发中…' : '⚡ 立即抓取并校验' }}
        </button>
      </div>
      <div class="bar-row">
        <span class="bar-name">http</span>
        <span class="bar-track">
          <span class="bar-fill" :style="{ width: (httpCount / protoTotal) * 100 + '%' }"></span>
        </span>
        <span class="bar-count">{{ httpCount }}</span>
      </div>
      <div class="bar-row">
        <span class="bar-name">https</span>
        <span class="bar-track">
          <span class="bar-fill green" :style="{ width: (httpsCount / protoTotal) * 100 + '%' }"></span>
        </span>
        <span class="bar-count">{{ httpsCount }}</span>
      </div>
    </div>

    <div class="card" v-if="sourceDist.length">
      <h3 class="card-title">来源分布</h3>
      <div class="bar-row" v-for="[name, count] in sourceDist" :key="name">
        <span class="bar-name" :title="name">{{ name }}</span>
        <span class="bar-track">
          <span class="bar-fill" :style="{ width: (count / maxSourceCount) * 100 + '%' }"></span>
        </span>
        <span class="bar-count">{{ count }}</span>
      </div>
    </div>

    <div class="card">
      <div class="toolbar">
        <h3 class="card-title" style="margin: 0">代理列表</h3>
        <span style="color: var(--text-secondary); font-size: 12px">
          {{ filteredProxies.length }} / {{ proxies.length }}
        </span>
        <div class="spacer"></div>
        <select v-model="filterType">
          <option value="all">全部协议</option>
          <option value="https">仅 HTTPS</option>
        </select>
        <input type="text" v-model="keyword" placeholder="搜索 ip / 来源 / 地区…" style="width: 200px" />
      </div>
      <div v-if="message" class="flash" :class="{ err: messageIsErr }" style="margin-bottom: 12px">
        {{ message }}
      </div>
      <div class="table-wrap">
        <table v-if="filteredProxies.length">
          <thead>
            <tr>
              <th>代理</th>
              <th>协议</th>
              <th>来源</th>
              <th>地区</th>
              <th>失败次数</th>
              <th>状态</th>
              <th>最后校验</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in filteredProxies" :key="p.proxy">
              <td class="mono">{{ p.proxy }}</td>
              <td>
                <span class="badge" :class="p.https ? 'badge-ok' : 'badge-off'">
                  {{ p.https ? 'https' : 'http' }}
                </span>
              </td>
              <td>{{ p.source }}</td>
              <td>{{ p.region || '-' }}</td>
              <td>{{ p.fail_count }}</td>
              <td>
                <span class="badge" :class="p.last_status === true ? 'badge-ok' : 'badge-err'">
                  {{ p.last_status === true ? '可用' : '异常' }}
                </span>
              </td>
              <td style="color: var(--text-secondary)">{{ p.last_time || '-' }}</td>
              <td>
                <button class="btn btn-danger" @click="removeProxy(p.proxy)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty">暂无代理数据</div>
      </div>
    </div>
  </div>
</template>
