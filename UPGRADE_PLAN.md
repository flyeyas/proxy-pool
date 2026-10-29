# 依赖与工程化升级计划（2026-10-29）

依据 2026-10-29 的全面体检结果制定。体检结论：代码层面无弃用 API（284 个测试在
Python 3.13 + DeprecationWarning-as-error 下全过），问题集中在依赖钉死版本、CI
覆盖缺口与上游遗留物。

## 目标

1. 消除依赖中的已知 CVE 与古董版本
2. 让 CI 覆盖实际生产运行环境（Docker = Python 3.13）
3. 移除 py2 时代兼容层与上游遗留物
4. 统一文档中的 Python 版本支持声明

## 支持版本决策

**统一支持 Python 3.10 – 3.13**（3.8 已于 2024-10 EOL、3.9 已于 2025-10 EOL；
Docker 镜像运行在 3.13）。由此 requirements.txt 的按版本双分支钉死策略可以
简化为单一版本集，且所有包可升到当前主线版本。

## 阶段一：依赖升级（安全优先）

`requirements.txt` 从双分支改为单一版本集：

| 包 | 旧（双分支） | 新 | 原因 |
|---|---|---|---|
| requests | ==2.31.0 | ==2.34.2 | CVE-2024-35195（2.32.0 修复）、CVE-2024-47081（2.32.4 修复） |
| gunicorn | ==19.9.0 (py<3.13) / >=23.0 | >=23.0 | 19.9.0 有 CVE-2024-1135、CVE-2024-6827（请求走私） |
| lxml | ==4.9.2 / ==5.3.0 | ==6.1.3 | 4.9.2 是 2022 年版本，捆绑的 libxml2 过旧 |
| click | ==8.0.1 / ==8.2.1 | >=8.2,<9.0 | 8.2+ 要求 py>=3.10; 用范围钉死以便与共享环境中要求 click<8.3 的工具共存 |
| Flask | ==2.1.1 / ==3.1.3 | ==3.1.3 | 2.1.1 为 py<3.13 遗留 |
| werkzeug | >=2.0,<2.2 / >=3.1,<3.2 | >=3.1,<3.2 | 同上 |
| APScheduler | ==3.2.0 (py<3.10) / ==3.11.3 | ==3.11.3 | 3.2.0 为 py<3.10 遗留 |
| redis | >=4.2.0 | >=4.2,<9.0 | 补上缺失的上界（其余包均钉死） |

`requirements-test.txt`：删除 py<=3.8 的 fakeredis 分支，保留 py<3.11 的
async_timeout/typing_extensions（3.10 仍在支持范围内）。

安装前先验证 lxml 6.1.3 在 alpine/musl 上有 wheel（Docker 构建依赖此路径），
无 wheel 则保留 build 依赖（Dockerfile 已装 libxml2-dev 等，可源码编译）。

## 阶段二：CI / 工程化

- `.github/workflows/test.yml`：matrix 从 ["3.8","3.9","3.10","3.11"] 改为
  ["3.10","3.11","3.12","3.13"]；codecov 上传从 3.11 改到 3.13
- **删除** `docker-image-latest.yml`、`docker-image-tags.yml`：上游遗留，
  推送目标仍是 jhao104/proxy_pool 的 Docker Hub（本 fork 必然失败且无意义），
  且使用 checkout@v2 等废弃版本 action；fork 自己的 `docker.yml`（GHCR）保留
- `tox.ini`：envlist 改为 py310,py311,py312,py313

## 阶段三：代码现代化

### 移除 util/six.py（py2 遗迹）

`withMetaclass(Singleton)` → 直接 `class X(metaclass=Singleton)`；urlparse/
Queue/reload 改从标准库导入。涉及：

- `handler/configHandler.py`（withMetaclass + reload_six）
- `db/dbClient.py`（withMetaclass + urlparse）
- `helper/validator.py`（withMetaclass）
- `helper/scheduler.py`（Queue）
- 删除 `util/six.py`

### util/webRequest.py

- 修复 `get()` 重试耗尽时假 `Response(status_code=200)` 未赋值给
  `self.response` 的隐性 bug（`post()` 有赋值，两路径行为不一致）
- UA 列表从 2013–2014 年（Chrome 30–39、IE6）换为现代浏览器 UA
  （Chrome/Edge/Firefox/Safari，Windows/macOS/Linux 混合）——老 UA 会被大量
  代理源直接拒绝，实打实影响抓取成功率

## 阶段四：杂项清理与文档

- `docker-compose.yml`：`redis:alpine` → `redis:8-alpine`（钉住大版本）
- 删除根目录 `_config.yml`（上游 Jekyll GitHub Pages 遗留，文档已迁 mkdocs）
- 版本声明统一为 3.10–3.13：`README.md`、`README.zh-CN.md`、`CLAUDE.md`
  （及 docs/ 下若有提及）

## 明确不做（本次）

- vite 7/8、vue-router 5、plugin-vue 6 升级：破坏性变更，且 vite 漏洞仅影响
  开发服务器（产物为静态文件，风险低），留待专门的前端升级窗口
- APScheduler 4.x：仍为 alpha
- 删除远端上游遗留 dependabot 分支（gunicorn-22.0.0 / lxml-6.1.0 /
  redis-4.4.4）：外发操作，留给仓库所有者手动处理或另行确认

## 验证

1. `conda activate xiangmu && pip install -r requirements.txt`
2. `pytest tests/unit tests/api -W error::DeprecationWarning`（全绿）
3. `python proxyPool.py fetcher`（新 click 下 CLI 冒烟）
4. pyright 静态检查（本仓库历史上保持 0 error）
5. 检查 lxml musllinux wheel 可用性（保障 Docker 构建）

## 提交切分建议（供评审后执行）

1. `deps: unify pins for py3.10–3.13, requests CVE upgrades`
2. `ci: test matrix 3.10–3.13, drop leftover jhao104 docker hub workflows`
3. `refactor: remove py2 six shim in favor of stdlib`
4. `fix: webRequest get() failure path + modern UA list`
5. `chore: redis image pin, remove jekyll leftover, sync version claims`
