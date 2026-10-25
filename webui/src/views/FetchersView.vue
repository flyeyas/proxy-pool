<script setup>
import { ref, computed } from 'vue'
import { api } from '../api'

const fetchers = ref([])
const loading = ref(false)
const error = ref('')
const message = ref('')
const messageIsErr = ref(false)
const toggling = ref('')

const activeCount = computed(
  () => fetchers.value.filter((f) => f.enabled && !f.excluded).length
)
const autoDisabledCount = computed(
  () => fetchers.value.filter((f) => f.auto_disabled).length
)
const manualDisabledCount = computed(
  () => fetchers.value.filter((f) => f.runtime_excluded && !f.auto_disabled).length
)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const r = await api.fetchers()
    fetchers.value = (r.data && r.data.fetchers) || []
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function flash(text, isErr = false) {
  message.value = text
  messageIsErr.value = isErr
}

function autoDisableTip(f) {
  const s = f.stats || {}
  const reason = s.disable_reason || `连续失败 ${s.fails || '?'} 次`
  const parts = [reason]
  if (s.last_error) parts.push(`最近错误: ${s.last_error}`)
  if (s.disabled_at) parts.push(`禁用于: ${s.disabled_at}`)
  return parts.join('\n')
}

async function toggle(f) {
  toggling.value = f.class_name
  try {
    await api.fetcherToggle(f.class_name, !f.excluded)
    if (f.excluded) {
      flash(
        f.auto_disabled
          ? `已重新启用 ${f.name}（统计已重置，下轮抓取生效）`
          : `已启用 ${f.name}，下轮抓取生效`
      )
    } else {
      flash(`已禁用 ${f.name}，下轮抓取生效（仪表盘可手动触发立即生效）`)
    }
    await load()
  } catch (e) {
    flash('操作失败: ' + e.message, true)
  } finally {
    toggling.value = ''
  }
}

load()
</script>

<template>
  <div>
    <div class="card">
      <div class="toolbar">
        <h3 class="card-title" style="margin: 0">代理源</h3>
        <span style="color: var(--text-secondary); font-size: 12px">
          生效 {{ activeCount }} / 手动禁用 {{ manualDisabledCount }} /
          自动禁用 {{ autoDisabledCount }} / 共 {{ fetchers.length }}，
          开关热生效
        </span>
        <div class="spacer"></div>
        <button class="btn" :disabled="loading" @click="load">
          {{ loading ? '加载中…' : '⟳ 刷新' }}
        </button>
      </div>
      <div v-if="error" class="flash err">{{ error }}</div>
      <div v-if="message" class="flash" :class="{ err: messageIsErr }" style="margin-top: 8px">
        {{ message }}
      </div>
      <div class="table-wrap">
        <table v-if="fetchers.length">
          <thead>
            <tr>
              <th>名称</th>
              <th>站点</th>
              <th>状态</th>
              <th>最近成功</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="f in fetchers" :key="f.class_name">
              <td class="mono">{{ f.name }}</td>
              <td>
                <a v-if="f.url" :href="f.url" target="_blank" rel="noopener" style="color: var(--primary)">
                  {{ f.url }}
                </a>
                <span v-else>-</span>
              </td>
              <td>
                <span v-if="!f.enabled" class="badge badge-off">源文件禁用</span>
                <span v-else-if="f.setting_excluded" class="badge badge-warn" title="在 setting.py 黑名单中，需修改配置文件移除">
                  setting.py 排除
                </span>
                <span v-else-if="f.auto_disabled" class="badge badge-err" :title="autoDisableTip(f)">
                  自动禁用 · 连续失败 {{ f.stats.fails }} 次
                </span>
                <span v-else-if="f.runtime_excluded" class="badge badge-off">手动禁用</span>
                <span v-else-if="f.stats && f.stats.fails > 0" class="badge badge-warn"
                      :title="'最近错误: ' + (f.stats.last_error || '无代理产出')">
                  抓取中 · 连续失败 {{ f.stats.fails }} 次
                </span>
                <span v-else class="badge badge-ok">抓取中</span>
              </td>
              <td style="color: var(--text-secondary)">{{ (f.stats && f.stats.last_success) || '-' }}</td>
              <td>
                <button
                  v-if="f.enabled && !f.setting_excluded"
                  class="btn"
                  :class="{ 'btn-danger': !f.excluded }"
                  :disabled="toggling === f.class_name"
                  @click="toggle(f)"
                >
                  {{ toggling === f.class_name ? '…' : f.excluded ? '启用' : '禁用' }}
                </button>
                <span v-else style="color: var(--text-secondary); font-size: 12px">-</span>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty">暂无代理源</div>
      </div>
    </div>
  </div>
</template>
