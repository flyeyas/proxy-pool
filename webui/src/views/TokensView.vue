<script setup>
import { ref } from 'vue'
import { api } from '../api'
import AppSelect from '../components/AppSelect.vue'

const tokens = ref([])
const loading = ref(false)
const name = ref('')
const expireDays = ref(30)
const message = ref('')
const messageIsErr = ref(false)
const copiedToken = ref('')

const expireOptions = [
  { value: 7, label: '7 天' },
  { value: 30, label: '30 天' },
  { value: 90, label: '90 天' },
  { value: 365, label: '1 年' },
  { value: 0, label: '永久' }
]

function flash(text, isErr = false) {
  message.value = text
  messageIsErr.value = isErr
}

async function load() {
  loading.value = true
  try {
    const r = await api.tokenList()
    tokens.value = r.tokens || []
  } catch (e) {
    flash('加载失败: ' + e.message, true)
  } finally {
    loading.value = false
  }
}

async function create() {
  try {
    const r = await api.tokenCreate(name.value, expireDays.value)
    flash(`已生成 token「${r.token.name}」，可在下方复制使用`)
    name.value = ''
    await load()
  } catch (e) {
    flash('生成失败: ' + e.message, true)
  }
}

async function remove(item) {
  if (!window.confirm(`确认删除 token「${item.name}」? 删除后使用它的程序将立即失去访问权限。`)) return
  try {
    await api.tokenDelete(item.token)
    flash(`已删除 token「${item.name}」`)
    await load()
  } catch (e) {
    flash('删除失败: ' + e.message, true)
  }
}

async function copy(item) {
  try {
    await navigator.clipboard.writeText(item.token)
    copiedToken.value = item.token
    setTimeout(() => (copiedToken.value = ''), 1500)
  } catch (e) {
    flash('复制失败，请手动选择复制', true)
  }
}

function isExpired(item) {
  if (!item.expires_at) return false
  return new Date(item.expires_at.replace(' ', 'T')) < new Date()
}

load()
</script>

<template>
  <div>
    <div class="card">
      <div class="toolbar">
        <h3 class="card-title" style="margin: 0">API Token</h3>
        <span style="color: var(--text-secondary); font-size: 12px">
          外部程序调用代理接口的授权凭证，请求时加 ?token=xxx 或 Authorization: Bearer 头
        </span>
        <div class="spacer"></div>
        <input type="text" v-model="name" placeholder="备注名，如：我的爬虫" style="width: 180px" />
        <AppSelect
          v-model="expireDays"
          :min-width="'130px'"
          :options="expireOptions.map((o) => ({ value: o.value, label: `有效期 ${o.label}` }))"
        />
        <button class="btn btn-primary" @click="create">+ 生成 Token</button>
      </div>
      <div v-if="message" class="flash" :class="{ err: messageIsErr }" style="margin-top: 8px">
        {{ message }}
      </div>
      <div class="table-wrap">
        <table v-if="tokens.length">
          <thead>
            <tr>
              <th>备注</th>
              <th>Token</th>
              <th>创建时间</th>
              <th>过期时间</th>
              <th>状态</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in tokens" :key="item.token">
              <td>{{ item.name }}</td>
              <td class="mono" style="max-width: 360px; overflow: hidden; text-overflow: ellipsis">
                {{ item.token }}
                <button class="btn" style="margin-left: 8px; padding: 2px 8px" @click="copy(item)">
                  {{ copiedToken === item.token ? '已复制' : '复制' }}
                </button>
              </td>
              <td style="color: var(--text-secondary)">{{ item.created_at }}</td>
              <td style="color: var(--text-secondary)">{{ item.expires_at || '永久' }}</td>
              <td>
                <span class="badge" :class="isExpired(item) ? 'badge-err' : 'badge-ok'">
                  {{ isExpired(item) ? '已过期' : '有效' }}
                </span>
              </td>
              <td>
                <button class="btn btn-danger" @click="remove(item)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty">
          暂无 token，点击上方「生成 Token」创建
        </div>
      </div>
    </div>
  </div>
</template>
