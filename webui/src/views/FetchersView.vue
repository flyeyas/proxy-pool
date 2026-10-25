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

async function toggle(f) {
  toggling.value = f.class_name
  try {
    await api.fetcherToggle(f.class_name, !f.runtime_excluded)
    flash(
      f.runtime_excluded
        ? `已启用 ${f.name}，下轮抓取生效`
        : `已禁用 ${f.name}，下轮抓取生效（仪表盘可手动触发立即生效）`
    )
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
          生效 {{ activeCount }} / 共 {{ fetchers.length }}，开关写入运行时黑名单，热生效
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
                <span v-else-if="f.runtime_excluded" class="badge badge-err">已禁用</span>
                <span v-else class="badge badge-ok">抓取中</span>
              </td>
              <td>
                <button
                  v-if="f.enabled && !f.setting_excluded"
                  class="btn"
                  :class="{ 'btn-danger': !f.runtime_excluded }"
                  :disabled="toggling === f.class_name"
                  @click="toggle(f)"
                >
                  {{ toggling === f.class_name ? '…' : f.runtime_excluded ? '启用' : '禁用' }}
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
