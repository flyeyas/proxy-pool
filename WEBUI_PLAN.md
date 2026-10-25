# ProxyPool WebUI 后台实施方案

> 状态：已批准（2026/10/24）

## 整体架构

前后端分离：前端为独立 Vue 3 + Vite 工程（`webui/` 目录），构建产物由现有 Flask 应用托管在 `/webui/` 路径下。开发时 Vite devserver 代理 API 到 Flask（5010），生产时 `npm run build` 产出 `webui/dist/`，由 Flask 直接服务，**不新增任何 Python 运行时依赖**。现有 `/get` `/all` 等 JSON 接口一律不动，保持完全兼容。

```
浏览器 ──> Flask (0.0.0.0:5010)
             ├── /get /pop /all /count /delete   (现有,不动)
             ├── /webui/ + /webui/<path>          (新: 托管 Vue 构建产物, SPA fallback)
             ├── /fetchers/   GET                 (新: 代理源列表)
             ├── /refresh/    POST                (新: 后台线程触发一次抓取+校验)
             └── /logs/       GET                 (新: 日志文件列表/尾部内容)
```

## 后端改动

### 1. 新增 `api/webuiApi.py`（Flask Blueprint）

在 `proxyApi.py` 中 `register_blueprint`（`proxyApi.py` 仅加 2 行 + api_list 补新接口条目）：

- `GET /webui/`、`GET /webui/<path:path>` — `send_from_directory` 服务 `webui/dist/`；SPA history 路由 fallback 到 index.html；dist 不存在时返回 503 + 提示文字（指导先 `npm run build` 或用 dev 模式）
- `GET /fetchers/` — 模仿 `helper/fetch.py` 的 `_discover_fetchers` 但**不过滤 enabled**，返回 `{"fetchers": [{name, url, enabled, excluded}]}`（excluded = 在 `PROXY_FETCHER_EXCLUDE` 黑名单中）
- `POST /refresh/` — 模块级 `threading.Lock` 防重入，起 daemon 线程执行"一次完整抓取+校验"（`Fetcher().run()` 入队 → `Checker("raw", queue)`，再 `Checker("use", queue)` 全池复检，与 `scheduler.py` 逻辑一致）。返回 `{"code": 0, "src": "started"}`；已在跑则返回 `{"code": 1, "src": "already running"}`。现有 GET `/refresh/` stub 不动。已知局限（代码注释说明）：gunicorn 多 worker 下锁不跨进程，v1 接受
- `GET /logs/` — 无参返回 `log/` 目录下日志文件名列表；带 `?name=fetch&lines=200`（上限 1000）返回该文件尾部行。name 做白名单校验（仅字母数字下划线，防路径穿越），文件不存在返回 404。Windows 下日志不落盘（`logHandler.py` 现状），返回空列表由前端提示

### 2. `handler/logHandler.py` 新增两个函数

- `listLogFiles()` — 列出 log 目录下全部 `*.log` 文件名（去扩展名）
- `readRecentLines(name, lines=200)` — 用 `collections.deque(maxlen=lines)` 高效读尾部行，name 白名单校验，返回 `(是否成功, 行列表)`

## 前端工程 `webui/`

Vite + Vue 3（`<script setup>`，JS）+ vue-router，**不引组件库、不引图表库**，手写 CSS（CSS 变量、系统字体栈、白底卡片式简洁风、跟随系统的暗色模式）。3 个视图：

- **Dashboard**（`/webui/`）：顶部统计卡片（总数 / HTTP / HTTPS / 来源数，数据 `/count/`）；协议与来源分布纯 CSS 水平条形图；代理表格（proxy、协议、来源、region、fail_count、last_status、last_time，来自 `/all/`），支持全部/HTTPS 筛选 + 关键字搜索 + 单条删除（`/delete/`，带确认）；「立即抓取并校验」按钮（POST `/refresh/`，结果提示）；5 秒自动轮询，可暂停
- **Fetchers**（`/webui/fetchers`）：表格列出全部代理源及 enabled/excluded 状态徽标，手动刷新
- **Logs**（`/webui/logs`）：左侧日志文件列表（fetch/checker/scheduler/launcher/fetcher…），右侧等宽字体展示尾部内容，行数选择（100/200/500/1000）、10 秒可选自动刷新、手动刷新

`vite.config.js`：`base: '/webui/'`；dev proxy 把 `/get /pop /all /count /delete /refresh /fetchers /logs` 转发到 `http://127.0.0.1:5010`，前端请求全部用相对路径，构建后同源无跨域问题。

## 打包与文档

- **Dockerfile 改多阶段构建**：stage1 `node:20-alpine` 执行 `npm ci && npm run build`；stage2 现有 `python:3.10-alpine` + `COPY --from=build` 拷入 `webui/dist/`。`docker-compose.yml` 不用改
- 新增 `.dockerignore`（排除 `webui/node_modules`、`webui/dist`、`.git`、`log/` 等）
- `.gitignore` 加 `webui/node_modules/`、`webui/dist/`（`package-lock.json` 提交）
- README.md 使用节、CLAUDE.md 架构/命令节补充 WebUI 说明；docs/（mkdocs）加一页简介

## 测试

新增 `tests/api/test_webui_api.py`，遵循现有 conftest 的 app/client fixture + mock 模式：

- `/fetchers/`：真实扫描本地源目录，断言返回非空、字段齐全
- `POST /refresh/`：mock `Thread`，断言触发成功（target 指向后台任务函数、daemon=True）+ 运行中重复触发被拒
- `/logs/`：tmp_path 造日志文件断言列表与尾部内容；`name=../setting` 断言路径穿越被拒（400）；不存在的 name 返回 404
- `/webui/`：tmp dist fixture 断言 200 与 HTML、SPA fallback、未构建时 503
- `logHandler.listLogFiles/readRecentLines` 在单元测试中覆盖

前端不写单测（体量小），以后端 pytest 为准。

## 实施顺序

1. 后端：`api/webuiApi.py` + `logHandler.py` 读日志函数 + `proxyApi.py` 注册
2. 前端工程脚手架与三个视图页面
3. `npm install && npm run build` 验证构建产物
4. Dockerfile 多阶段 + .dockerignore + .gitignore
5. `tests/api/test_webui_api.py` 并跑通全部现有测试（`pytest`）
6. 文档更新
