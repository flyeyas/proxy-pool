<script setup>
import { ref, computed } from 'vue'
import { api } from '../api'
import { usePolling } from '../composables/usePolling'

const snapshot = ref(null)
const error = ref('')
const jobs = computed(() => snapshot.value?.jobs || [])
const history = computed(() => snapshot.value?.history || [])
const online = computed(() => !error.value && snapshot.value?.online)
const runningCount = computed(() => online.value
  ? jobs.value.reduce((count, job) => count + job.running_count, 0) : 0)
const failureCount = computed(() => jobs.value.reduce((count, job) => count + job.error_count, 0))

const schedulerStates = {
  starting: ['启动中', 'badge-warn'],
  running: ['运行中', 'badge-ok'],
  paused: ['调度已暂停', 'badge-warn'],
  stopped: ['已停止', 'badge-off'],
  error: ['启动失败', 'badge-err'],
  offline: ['心跳中断', 'badge-err'],
  not_started: ['尚未启动', 'badge-off'],
  unavailable: ['状态不可用', 'badge-err']
}
const schedulerState = computed(() => error.value
  ? ['连接失败', 'badge-err']
  : schedulerStates[snapshot.value?.status] || ['正在加载', 'badge-off'])
const resultStates = {
  success: ['成功', 'badge-ok'],
  error: ['失败', 'badge-err'],
  missed: ['错过执行', 'badge-warn'],
  skipped: ['并发已满', 'badge-warn']
}

function resultState(result) {
  return resultStates[result] || ['尚未执行', 'badge-off']
}

function jobState(job) {
  if (job.status === 'not_started') return ['尚未启动', 'badge-off']
  if (!online.value) return ['状态未知', 'badge-off']
  if (job.running_count) return [`执行中 · ${job.running_count}`, 'badge-running']
  if (snapshot.value.status === 'paused') return ['等待恢复', 'badge-warn']
  return ['等待执行', 'badge-off']
}

function formatTime(value) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: snapshot.value?.timezone || 'Asia/Shanghai',
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    second: '2-digit', hourCycle: 'h23'
  }).format(new Date(value))
}

function formatDuration(value) {
  if (value == null) return '—'
  if (value < 1) return `${Math.round(value * 1000)} 毫秒`
  if (value < 60) return `${value.toFixed(1)} 秒`
  const seconds = Math.round(value)
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`
}

async function loadStatus() {
  try {
    const response = await api.schedulerStatus()
    snapshot.value = response.data
    error.value = ''
  } catch (e) {
    error.value = `获取任务状态失败：${e.message}`
  }
}

const polling = usePolling(loadStatus, 5000)
polling.start()
</script>

<template>
  <div class="scheduler-page">
    <div class="toolbar scheduler-heading">
      <div>
        <h1>定时任务</h1>
        <p class="scheduler-muted">查看代理采集、校验与日志清理的运行情况</p>
      </div>
      <div class="spacer"></div>
      <button class="btn" :class="{ active: polling.running.value }" @click="polling.toggle()">
        {{ polling.running.value ? '⏸ 暂停刷新' : '▶ 自动刷新' }}
      </button>
      <button class="btn" :disabled="polling.loading.value" @click="polling.run()">
        {{ polling.loading.value ? '刷新中…' : '⟳ 立即刷新' }}
      </button>
    </div>

    <div v-if="error" class="flash err scheduler-notice" role="alert">{{ error }}</div>
    <div class="cards">
      <div class="card">
        <div class="stat-label">调度器状态</div>
        <div class="scheduler-state">
          <span class="badge" :class="schedulerState[1]">{{ schedulerState[0] }}</span>
        </div>
        <div class="scheduler-muted">启动于 {{ formatTime(snapshot?.started_at) }}</div>
      </div>
      <div class="card">
        <div class="stat-value">{{ snapshot ? jobs.length : '—' }}</div>
        <div class="stat-label">定时任务</div>
      </div>
      <div class="card">
        <div class="stat-value">{{ snapshot && !error ? runningCount : '—' }}</div>
        <div class="stat-label">正在执行</div>
      </div>
      <div class="card">
        <div class="stat-value">{{ snapshot ? failureCount : '—' }}</div>
        <div class="stat-label">本次启动累计失败</div>
      </div>
    </div>

    <div v-if="snapshot" class="card scheduler-health">
      <span>最近心跳 <strong>{{ formatTime(snapshot.heartbeat_at) }}</strong></span>
      <span class="scheduler-muted">时区 {{ snapshot.timezone }} · 每 5 秒刷新</span>
      <p v-if="snapshot.status === 'starting'" class="scheduler-muted">
        正在执行启动时的日志清理和首次采集，完成后开始定时调度。
      </p>
      <p v-else-if="snapshot.status === 'not_started'" class="scheduler-muted">
        尚未收到调度器状态，请启动调度服务后查看。
      </p>
      <p v-else-if="snapshot.status === 'offline'" class="scheduler-warning">
        超过 {{ snapshot.stale_after }} 秒未收到心跳，请检查调度服务。以下执行记录为最近一次快照。
      </p>
      <p v-else-if="snapshot.error" class="scheduler-warning">{{ snapshot.error }}</p>
    </div>

    <div class="card">
      <h2 class="card-title">任务状态</h2>
      <div class="table-wrap">
        <table v-if="jobs.length" class="scheduler-table">
          <thead>
            <tr>
              <th>任务</th><th>执行频率</th><th>当前状态</th><th>下次执行</th>
              <th>最近完成</th><th>耗时</th><th>成功 / 失败</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="job in jobs" :key="job.id">
              <tr>
                <td class="scheduler-task">
                  <strong>{{ job.name }}</strong>
                  <div class="scheduler-muted scheduler-description">{{ job.description }}</div>
                </td>
                <td>{{ job.schedule }}</td>
                <td>
                  <span class="badge" :class="jobState(job)[1]">{{ jobState(job)[0] }}</span>
                  <div v-if="job.running_count && online" class="scheduler-muted">
                    开始 {{ formatTime(job.running_since) }}
                  </div>
                </td>
                <td>{{ online ? formatTime(job.next_run_time) : '—' }}</td>
                <td>
                  <span class="badge" :class="resultState(job.last_result)[1]">
                    {{ resultState(job.last_result)[0] }}
                  </span>
                  <div class="scheduler-muted">{{ formatTime(job.last_finished_at) }}</div>
                </td>
                <td>{{ formatDuration(job.last_duration) }}</td>
                <td>
                  <span class="scheduler-success">{{ job.success_count }}</span>
                  / <span :class="{ 'scheduler-warning': job.error_count }">{{ job.error_count }}</span>
                  <div v-if="job.missed_count || job.skipped_count" class="scheduler-muted">
                    错过 {{ job.missed_count }} · 跳过 {{ job.skipped_count }}
                  </div>
                </td>
              </tr>
              <tr v-if="job.last_error">
                <td colspan="7" class="scheduler-error">最近错误：{{ job.last_error }}</td>
              </tr>
            </template>
          </tbody>
        </table>
        <div v-else class="empty">{{ polling.loading.value ? '正在加载任务状态…' : '暂无任务状态' }}</div>
      </div>
    </div>

    <div class="card">
      <div class="toolbar">
        <h2 class="card-title" style="margin: 0">最近一小时的执行记录</h2>
        <span class="scheduler-muted">{{ history.length }} 条 · 按记录时间滚动保留，包含启动时执行的任务</span>
      </div>
      <div class="table-wrap">
        <table v-if="history.length" class="scheduler-history">
          <thead>
            <tr>
              <th>任务</th><th>结果</th><th>触发方式</th><th>开始时间</th>
              <th>结束时间</th><th>耗时</th><th>详情</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="record in history" :key="record.id">
              <td>{{ record.name }}</td>
              <td><span class="badge" :class="resultState(record.result)[1]">{{ resultState(record.result)[0] }}</span></td>
              <td>{{ record.source === 'startup' ? '启动时执行' : '定时调度' }}</td>
              <td>{{ formatTime(record.started_at || record.scheduled_at) }}</td>
              <td>{{ formatTime(record.finished_at) }}</td>
              <td>{{ formatDuration(record.duration) }}</td>
              <td class="scheduler-detail" :class="{ 'scheduler-warning': record.error }">{{ record.error || '执行完成' }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty">暂无执行记录</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.scheduler-heading { margin-bottom: 20px; }
.scheduler-heading h1 { margin: 0 0 6px; font-size: 22px; font-weight: 600; }
.scheduler-heading p { margin: 0; }
.scheduler-muted { color: var(--text-secondary); font-size: 12px; line-height: 1.7; }
.scheduler-state { margin: 8px 0; }
.scheduler-state .badge { font-size: 15px; }
.scheduler-notice { margin-bottom: 16px; }
.scheduler-health { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; margin-bottom: 16px; }
.scheduler-health p { width: 100%; margin: 0; }
.scheduler-table { min-width: 1000px; }
.scheduler-task { min-width: 190px; max-width: 220px; }
.scheduler-description { margin-top: 4px; white-space: normal; }
.badge-running { color: var(--primary); background: var(--primary-soft); }
.scheduler-success { color: var(--green); }
.scheduler-warning, .scheduler-error { color: var(--red); }
.scheduler-error { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--red-soft); }
.scheduler-detail { min-width: 180px; max-width: 320px; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
