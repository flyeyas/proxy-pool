<script setup>
import { ref, watch } from 'vue'
import { api } from '../api'
import { usePolling } from '../composables/usePolling'
import AppSelect from '../components/AppSelect.vue'

const files = ref([])
const current = ref('')
const lines = ref(200)
const content = ref([])
const error = ref('')
const lineOptions = [100, 200, 500, 1000]

async function loadList() {
  try {
    const r = await api.logList()
    files.value = r.logs || []
    if (!current.value && files.value.length) {
      current.value = files.value[0]
    }
  } catch (e) {
    error.value = e.message
  }
}

async function loadContent() {
  if (!current.value) return
  error.value = ''
  try {
    const r = await api.logContent(current.value, lines.value)
    content.value = r.lines || []
  } catch (e) {
    content.value = []
    error.value = e.message
  }
}

const polling = usePolling(loadContent, 10000)

loadList().then(() => {
  if (current.value) polling.start()
})

watch([current, lines], () => {
  content.value = []
  loadContent()
})
</script>

<template>
  <div>
    <div class="logs-layout">
      <div class="card">
        <h3 class="card-title">日志文件</h3>
        <div class="log-files" v-if="files.length">
          <button
            v-for="name in files"
            :key="name"
            class="log-file-btn mono"
            :class="{ active: name === current }"
            @click="current = name"
          >
            {{ name }}
          </button>
        </div>
        <div v-else class="empty" style="padding: 16px 0">
          暂无日志文件<br />
          <span style="font-size: 12px">（Windows 下日志不落盘）</span>
        </div>
      </div>
      <div class="card">
        <div class="toolbar">
          <h3 class="card-title" style="margin: 0">
            {{ current ? `${current}.log` : '日志内容' }}
          </h3>
          <div class="spacer"></div>
          <AppSelect
            v-model="lines"
            :min-width="'110px'"
            :options="lineOptions.map((n) => ({ value: n, label: `尾部 ${n} 行` }))"
          />
          <button class="btn" :class="{ active: polling.running.value }" @click="polling.toggle()">
            {{ polling.running.value ? '⏸ 暂停' : '▶ 自动' }}
          </button>
          <button class="btn" :disabled="polling.loading.value" @click="polling.run()">⟳ 刷新</button>
        </div>
        <div v-if="error" class="flash err">{{ error }}</div>
        <pre v-else class="log-content">{{ content.length ? content.join('\n') : '暂无内容' }}</pre>
      </div>
    </div>
  </div>
</template>
