import { createRouter, createWebHistory } from 'vue-router'
import DashboardView from './views/DashboardView.vue'
import { auth } from './auth'

export const router = createRouter({
  history: createWebHistory('/webui/'),
  routes: [
    { path: '/', name: 'dashboard', component: DashboardView },
    { path: '/login', name: 'login', component: () => import('./views/LoginView.vue') },
    { path: '/fetchers', name: 'fetchers', component: () => import('./views/FetchersView.vue') },
    { path: '/logs', name: 'logs', component: () => import('./views/LogsView.vue') }
  ]
})

// 登录守卫: 后端启用登录且未登录时, 一律跳转登录页
router.beforeEach((to) => {
  if (to.name === 'login') return true
  if (auth.required && !auth.loggedIn) return { name: 'login' }
  return true
})
