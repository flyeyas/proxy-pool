<script setup>
// 接口说明文档(静态内容, 与后端路由保持同步)
const endpoints = [
  { method: 'GET', path: '/get/', desc: '随机获取一个代理（池空返回 404）', params: '?type=https 筛选 HTTPS' },
  { method: 'GET', path: '/pop/', desc: '获取并删除一个代理（池空返回 404）', params: '?type=https 筛选 HTTPS' },
  { method: 'GET', path: '/all/', desc: '获取所有代理，data 为 {"count": N, "proxies": [...]}', params: '?type=https 筛选 HTTPS' },
  { method: 'GET', path: '/count/', desc: '代理数量统计（含协议/来源分布）', params: '' },
  { method: 'DELETE', path: '/delete/', desc: '删除代理，成功 {"deleted": true}，不存在 404', params: '?proxy=host:port' },
  { method: 'GET', path: '/fetchers/', desc: '代理源列表及状态、抓取统计', params: '' },
  { method: 'PATCH', path: '/fetchers/<类名>/', desc: '启停代理源', params: 'body {"enabled": bool}' },
  { method: 'POST', path: '/refresh/', desc: '手动触发一次抓取+校验（202 后台执行，重复触发 409）', params: '' },
  { method: 'GET', path: '/logs/', desc: '日志文件列表（含大小）/ 指定日志尾部内容', params: '?name=fetch&lines=200' },
  { method: 'GET', path: '/auth/status/', desc: '登录状态查询（公开）', params: '' },
  { method: 'POST', path: '/auth/login/', desc: '登录，body {"username","password"}（公开）', params: '' },
  { method: 'POST', path: '/auth/logout/', desc: '登出（需会话）', params: '' },
  { method: 'GET', path: '/auth/tokens/', desc: 'Token 列表（需会话）', params: '' },
  { method: 'POST', path: '/auth/tokens/', desc: '生成 Token（需会话）', params: 'body {"name","expire_days"}' },
  { method: 'PATCH', path: '/auth/tokens/<token>/', desc: '修改 Token 备注（需会话）', params: 'body {"name"}' },
  { method: 'DELETE', path: '/auth/tokens/<token>/', desc: '删除 Token（需会话）', params: '' },
  { method: 'GET', path: '/webui/', desc: 'WebUI 管理后台', params: '' }
]

const methodClass = {
  GET: 'badge-ok',
  POST: 'badge-warn',
  PATCH: 'badge-warn',
  DELETE: 'badge-err'
}

const curlExample = `# 带 Token 获取一个代理
curl "http://127.0.0.1:5010/get/?token=pp_xxxxxxxx"

# Bearer 请求头方式
curl -H "Authorization: Bearer pp_xxxxxxxx" http://127.0.0.1:5010/count/`

const pythonExample = `import requests

TOKEN = "pp_xxxxxxxxxx"   # 在「Token」页生成
BASE = "http://127.0.0.1:5010"

def get_proxy():
    resp = requests.get(BASE + "/get/", params={"token": TOKEN})
    return resp.json()["data"] if resp.status_code == 200 else None

def delete_proxy(proxy):
    requests.delete(BASE + "/delete/", params={"proxy": proxy, "token": TOKEN})`
</script>

<template>
  <div>
    <div class="card">
      <h3 class="card-title">认证方式</h3>
      <p style="margin: 0 0 8px; line-height: 1.8">
        启用登录后（<code>.env</code> 设置 <code>WEBUI_PASSWORD</code>），除公开接口外均需授权，两种方式任选：
      </p>
      <ul style="margin: 0; line-height: 2">
        <li><b>登录会话</b>：WebUI 页面自动携带（浏览器登录后的 Cookie）</li>
        <li><b>API Token</b>：外部程序直连接口用，请求加 <code>?token=xxx</code> 参数或
          <code>Authorization: Bearer xxx</code> 请求头；
          Token 在 <router-link to="/tokens" style="color: var(--primary)">「Token」页</router-link> 生成，可设有效期</li>
      </ul>
    </div>

    <div class="card">
      <h3 class="card-title">统一响应格式</h3>
      <p style="margin: 0 0 8px; line-height: 1.8">
        所有接口返回统一包裹，<code>code</code> 与 HTTP 状态码一致（保持真实语义）：
      </p>
      <pre class="doc-code">// 成功: 2xx
{"code": 200, "message": "ok", "data": { ...资源数据... }}
// 失败: 4xx/5xx, data 恒为 null
{"code": 404, "message": "no proxy available", "data": null}</pre>
      <p style="margin: 8px 0 0; color: var(--text-secondary); font-size: 13px; line-height: 1.8">
        常见 code：200 成功 / 201 已创建 / 202 已受理 / 400 参数错误 / 401 未授权 / 404 不存在 / 409 冲突。
        客户端判定：<code>resp.ok</code> 或 <code>code &lt; 400</code> 即成功。
      </p>
    </div>

    <div class="card">
      <h3 class="card-title">接口列表</h3>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>方法</th>
              <th>接口</th>
              <th>说明</th>
              <th>参数</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="ep in endpoints" :key="ep.method + ep.path">
              <td><span class="badge" :class="methodClass[ep.method]">{{ ep.method }}</span></td>
              <td class="mono">{{ ep.path }}</td>
              <td>{{ ep.desc }}</td>
              <td class="mono" style="color: var(--text-secondary)">{{ ep.params || '-' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <h3 class="card-title">调用示例</h3>
      <p style="margin: 0 0 6px; font-weight: 500">curl</p>
      <pre class="doc-code">{{ curlExample }}</pre>
      <p style="margin: 12px 0 6px; font-weight: 500">Python（爬虫）</p>
      <pre class="doc-code">{{ pythonExample }}</pre>
    </div>
  </div>
</template>

<style scoped>
.doc-code {
  margin: 0;
  padding: 12px 14px;
  border-radius: 6px;
  background: var(--bg);
  border: 1px solid var(--border);
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.7;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
