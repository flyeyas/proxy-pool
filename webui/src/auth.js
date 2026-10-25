import { reactive } from 'vue'

// WebUI 登录状态(由 /auth/status/ 初始化)
export const auth = reactive({
  checked: false, // 是否已完成启动时查询
  required: false, // 后端是否启用了登录
  loggedIn: false
})
