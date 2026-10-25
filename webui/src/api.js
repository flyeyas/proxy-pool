// 后端 API 封装: 全部使用相对路径, 构建(同源)与 dev(vite proxy)均可直连
import { auth } from './auth'

async function request(url, options = {}) {
  const resp = await fetch(url, options)
  // 管理接口未登录/登录过期: 通知全局状态跳转登录页(登录接口自身的401除外)
  if (resp.status === 401 && !url.startsWith('/auth/')) {
    auth.loggedIn = false
  }
  if (!resp.ok) {
    // 优先取后端统一错误体 {error, message}
    let body = null
    let detail = ''
    try {
      const text = await resp.text()
      try {
        body = JSON.parse(text)
      } catch (e) {
        detail = text.slice(0, 200)
      }
    } catch (e) {
      /* ignore */
    }
    const err = new Error((body && body.message) || detail || `HTTP ${resp.status}`)
    err.status = resp.status
    err.body = body
    throw err
  }
  if (resp.status === 204) {
    return null
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
  logout: () =>
    request('/auth/logout/', { method: 'POST', headers: { 'Content-Type': 'application/json' } }),
  tokenList: () => request('/auth/tokens/'),
  tokenCreate: (name, expireDays) =>
    request('/auth/tokens/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, expire_days: expireDays })
    }),
  tokenDelete: (token) =>
    request(`/auth/tokens/${encodeURIComponent(token)}/`, { method: 'DELETE' }),
  tokenRename: (token, name) =>
    request(`/auth/tokens/${encodeURIComponent(token)}/`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    }),
  getCount: () => request('/count/'),
  getAll: () => request('/all/'),
  deleteProxy: (proxy) =>
    request(`/delete/?proxy=${encodeURIComponent(proxy)}`, { method: 'DELETE' }),
  refresh: () => request('/refresh/', { method: 'POST' }),
  fetchers: () => request('/fetchers/'),
  fetcherToggle: (className, enabled) =>
    request(`/fetchers/${encodeURIComponent(className)}/`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled })
    }),
  logList: () => request('/logs/'),
  logContent: (name, lines) =>
    request(`/logs/?name=${encodeURIComponent(name)}&lines=${lines}`)
}
