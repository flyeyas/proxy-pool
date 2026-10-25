// 后端 API 封装: 全部使用相对路径, 构建(同源)与 dev(vite proxy)均可直连
import { auth } from './auth'

async function request(url, options = {}) {
  const resp = await fetch(url, options)
  // 管理接口未登录/登录过期: 通知全局状态跳转登录页(登录接口自身的401除外)
  if (resp.status === 401 && !url.startsWith('/auth/')) {
    auth.loggedIn = false
  }
  if (!resp.ok) {
    let detail = ''
    try {
      detail = (await resp.text()).slice(0, 200)
    } catch (e) {
      /* ignore */
    }
    throw new Error(`HTTP ${resp.status} ${detail}`.trim())
  }
  const text = await resp.text()
  try {
    return JSON.parse(text)
  } catch (e) {
    return text
  }
}

export const api = {
  authStatus: () => request('/auth/status/'),
  login: (username, password) =>
    request('/auth/login/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    }),
  logout: () => request('/auth/logout/'),
  tokenList: () => request('/auth/tokens/'),
  tokenCreate: (name, expireDays) =>
    request('/auth/tokens/create/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, expire_days: expireDays })
    }),
  tokenDelete: (token) =>
    request('/auth/tokens/delete/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token })
    }),
  getCount: () => request('/count/'),
  getAll: () => request('/all/'),
  deleteProxy: (proxy) => request(`/delete/?proxy=${encodeURIComponent(proxy)}`),
  refresh: () => request('/refresh/', { method: 'POST' }),
  fetchers: () => request('/fetchers/'),
  fetcherToggle: (className, enable) =>
    request('/fetchers/toggle/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ class_name: className, enable })
    }),
  logList: () => request('/logs/'),
  logContent: (name, lines) =>
    request(`/logs/?name=${encodeURIComponent(name)}&lines=${lines}`)
}
