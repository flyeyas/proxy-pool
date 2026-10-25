<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { auth } from '../auth'

const router = useRouter()
const username = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)

async function submit() {
  if (!username.value || !password.value) {
    error.value = '请输入账号和密码'
    return
  }
  busy.value = true
  error.value = ''
  try {
    await api.login(username.value, password.value)
    auth.loggedIn = true
    router.replace({ name: 'dashboard' })
  } catch (e) {
    error.value = '账号或密码错误'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <form class="card login-card" @submit.prevent="submit">
      <h2 class="login-title">ProxyPool 控制台</h2>
      <p class="login-sub">请登录后继续</p>
      <label class="login-field">
        <span>账号</span>
        <input type="text" v-model="username" autocomplete="username" autofocus placeholder="请输入账号" />
      </label>
      <label class="login-field">
        <span>密码</span>
        <input type="password" v-model="password" autocomplete="current-password" placeholder="请输入密码" />
      </label>
      <div v-if="error" class="flash err" style="margin-bottom: 12px">{{ error }}</div>
      <button class="btn btn-primary login-btn" type="submit" :disabled="busy">
        {{ busy ? '登录中…' : '登 录' }}
      </button>
    </form>
  </div>
</template>
