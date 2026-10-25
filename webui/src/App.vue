<script setup>
import { watch } from 'vue'
import { RouterLink, RouterView, useRouter } from 'vue-router'
import { api } from './api'
import { auth } from './auth'

const router = useRouter()

// 登录态变化时跳转: 登录过期(401/登出)或状态查询返回需登录而未登录
watch(
  [() => auth.required, () => auth.loggedIn],
  ([required, loggedIn]) => {
    if (required && !loggedIn && router.currentRoute.value.name !== 'login') {
      router.push({ name: 'login' })
    }
  }
)

async function logout() {
  try {
    await api.logout()
  } catch (e) {
    /* ignore */
  }
  auth.loggedIn = false
}
</script>

<template>
  <div class="layout">
    <header class="topbar" v-if="!auth.required || auth.loggedIn">
      <div class="brand">
        ProxyPool <span class="brand-sub">控制台</span>
      </div>
      <nav class="nav">
        <RouterLink to="/">仪表盘</RouterLink>
        <RouterLink to="/fetchers">代理源</RouterLink>
        <RouterLink to="/logs">日志</RouterLink>
      </nav>
      <div style="flex: 1"></div>
      <button class="btn" @click="logout">退出登录</button>
    </header>
    <main class="main">
      <RouterView />
    </main>
  </div>
</template>
