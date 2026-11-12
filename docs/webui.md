# WebUI 后台

ProxyPool 内置一个简洁风格的 WebUI 管理后台，用于查看代理池状态和管理代理。

## 访问方式

启动 `server` 后访问：

```
http://127.0.0.1:5010/webui/
```

!!! note "首次使用需构建前端"

    ```bash
    cd webui
    npm install
    npm run build   # 产物输出到 webui/dist/，由 Flask 直接托管
    ```

    Docker 镜像已内置构建产物，无需手动构建。

    未构建时访问 `/webui/` 会返回 503 与提示信息。

## 功能

- **仪表盘**：代理总数 / HTTP / HTTPS / 来源数统计卡片；协议与来源分布条形图；代理列表（支持协议筛选、关键字搜索、单条删除）；5 秒自动刷新（可暂停）；「立即抓取并校验」按钮（后台线程执行一次完整抓取 + 全池复检）
- **代理源**：列出 `fetcher/sources/` 下全部代理源，支持**后台启停开关**（写入运行时黑名单 `runtime_config.json`，与 `setting.py` 的 `PROXY_FETCHER_EXCLUDE` 取并集生效；每轮抓取前读取，热生效）；**连续失败自动禁用**（默认 5 次，`FETCHER_AUTO_DISABLE_FAILS` 可调）——被禁源有红色标注（失败原因/最近错误/禁用时间），可点击「启用」重置统计重新观察；列表还展示各源最近成功时间
- **定时任务**：查看调度器在线状态、代理采集（每 5 分钟）、代理校验（每 2 分钟）、日志清理（每小时整点）的当前状态、下次执行时间、最近结果、耗时、成功与失败次数，以及最近一小时的执行记录；5 秒自动刷新，可暂停或立即刷新。失败原因、错过执行时间和达到并发上限的记录会显示在页面中。
- **日志**：查看 `log/` 目录下当前日志文件的尾部内容（fetch、checker、scheduler 等），支持行数选择与自动刷新。文件按整点切割，小时归档在宿主机 `log/` 中查看，保留最近七天；读取与切割互斥，避免切割时读取失败。

    !!! warning "Windows 限制"
        日志在 Windows 下不落盘（见 `handler/logHandler.py`），日志页将为空。

## 采集时使用代理

在“代理源”页面，每个采集源都有独立的“采集代理”开关：

- **使用代理**（默认）：从数据库的可用代理池中选择最近一次验证成功的代理。访问 HTTPS 采集地址时，仅选择支持 HTTPS 的代理；没有符合条件的代理时自动直连。
- **直连采集**：该源始终直接访问采集地址，不使用代理池或环境变量中的代理。

设置保存到 `runtime_config.json` 的 `fetcher_use_proxy` 字段，下一轮定时采集或手动采集生效，无需重启。切换采集代理不会启停采集源或重置抓取统计。

同一采集线程优先复用选中的代理，连接失败时在原有重试次数内尝试其他已验证代理。代理池读取失败时也会直连，并记录日志。原始采集结果仍需经过验证才能入库。

## 登录验证

WebUI 支持简单的账号密码登录（会话 Cookie 方案，无独立用户系统）。在 `.env`（或环境变量）中设置：

```bash
WEBUI_USERNAME=admin          # 默认 admin
WEBUI_PASSWORD=your-password  # 设置后启用登录验证, 留空则不启用
# WEBUI_SECRET_KEY=xxx        # 可选: 会话签名密钥, 不设则由账号密码自动派生
```

启用后：

- 浏览器访问 `/webui/` 会先跳转登录页
- 所有数据与管理接口（`/get`、`/pop`、`/all`、`/count`、`/delete`、`/fetchers`、`/scheduler`、`/logs`、`POST /refresh`、`/auth/tokens`）未授权返回 401
- 授权方式两种：**WebUI 登录会话**（浏览器自动携带）或 **API Token**（外部程序请求时加 `?token=xxx` 或 `Authorization: Bearer xxx` 头）

## API Token

供爬虫等外部程序直接调用代理接口的授权凭证，在 WebUI「Token」页管理：

- **生成**：填写备注名 + 选择有效期（7 天 / 30 天 / 90 天 / 1 年 / 永久），点击生成，支持一键复制
- **使用**：请求时加 `?token=xxx` 参数或 `Authorization: Bearer xxx` 请求头
- **删除**：立即失效；过期后自动失效（状态列标注"已过期"）
- Token 与代理源黑名单同存于 `runtime_config.json`（不入库），token 管理接口仅允许登录会话操作（token 自身不能增删 token）

```bash
# 示例
curl "http://127.0.0.1:5010/get/?token=pp_xxxxx"
curl -H "Authorization: Bearer pp_xxxxx" http://127.0.0.1:5010/count/
```

## 前端开发

前端为独立的 Vue 3 + Vite 工程，位于 `webui/` 目录：

```bash
cd webui
npm install
npm run dev    # http://localhost:5173 ，API 请求自动代理到 127.0.0.1:5010
npm run build  # 构建生产产物到 webui/dist/
```

开发时需要同时运行 `python proxyPool.py server`。前端零组件库依赖，样式为手写 CSS（支持跟随系统的暗色模式）。

## 定时任务状态

调度器每 10 秒更新一次心跳，超过 30 秒没有更新时页面显示“心跳中断”，运行中任务显示为“状态未知”。启动阶段的首次日志清理与代理采集也会被跟踪；首次采集完成后开始定时调度。

状态独立保存在 `log/scheduler_status.json`，通过原子替换让 `schedule` 与多个 API worker 共享同一份完整快照。现有 Docker 日志挂载即可共享状态，无需添加配置或数据库表。计数在调度器重启时重置，执行记录会恢复最近一小时的历史。成功表示任务函数正常结束，代理源的具体采集结果仍在“代理源”页面查看。

执行记录按记录时间滚动保留最近一小时，包括成功、失败、错过执行和达到并发上限的记录；这一小时内的全部记录都会保留。任务完成后生成记录，执行跨越一小时的任务也会展示其最新结果。调度器每次更新快照时清理过期记录，接口读取时再次过滤，调度器停止后也不会展示一小时以前的记录。

只启动 `server` 而未启动 `schedule` 时，任务页会显示“尚未启动”。

## 相关接口

WebUI 使用的后端接口见 [API 使用](api.md)：`/fetchers`、`/refresh`（POST）、`/scheduler`、`/logs`。
