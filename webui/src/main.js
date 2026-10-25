import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import { api } from './api'
import { auth } from './auth'
import './assets/main.css'

// 必须先拿到登录状态再挂载应用:
// 路由守卫在 app.use(router) 时就会执行首次导航, 若状态未就绪会误放行进入仪表盘
api
  .authStatus()
  .then((s) => {
    auth.required = !!s.required
    auth.loggedIn = !!s.logged_in
  })
  .catch(() => {})
  .finally(() => {
    auth.checked = true
    createApp(App).use(router).mount('#app')
  })
