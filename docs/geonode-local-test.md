# Geonode 本地 Docker 验证（2026-11-18）

以下验证在 1.5.0 发布前完成，仅从工作区源码构建，未使用已发布的项目镜像。测试过程中未更新服务器部署。

## 镜像与依赖

- 应用版本：`1.5.0`。
- 本地镜像：`proxy-pool:local-geonode-1.5.0`，架构 `arm64`。
- 前端基础镜像：`mirror.gcr.io/library/node:26-alpine`，实际 Node `26.13.1`。
- 后端基础镜像：`mirror.gcr.io/library/python:3.13-alpine`，实际 Python `3.13.18`。
- 数据库镜像：`mirror.gcr.io/library/redis:8-alpine`，实际 Redis `8.10.3`。

构建命令：

```bash
docker build --pull \
  --build-arg NODE_BASE_IMAGE=mirror.gcr.io/library/node:26-alpine \
  --build-arg PYTHON_BASE_IMAGE=mirror.gcr.io/library/python:3.13-alpine \
  -t proxy-pool:local-geonode-1.5.0 .
```

## 验证结果

- 本地 `xiangmu` 环境：446 项测试通过。
- 最终镜像内 Python 3.13：446 项测试通过，测试依赖仅装在自动删除的临时测试容器中。
- Node 26 下的 Vite 前端生产构建通过。
- HTTP 200 正常响应、200 空列表、429、503、缺少 `data` 字段、非法 JSON、纯文本与两次请求超时的 8 类场景均有诊断日志。
- 混合协议正常响应中只返回 HTTP/HTTPS，SOCKS4/5 被过滤，重复地址被去重。
- 错误摘要写入运行时统计，原有其他配置字段保持完整。
- 调度页返回 4 个任务：常规源 5 分钟、Geonode 15 分钟、代理复检 2 分钟、整点日志清理。Geonode 同时最多执行一次，错过的周期合并。
- 登录鉴权、WebUI 静态文件、Redis 数据 API 和调度器连续心跳通过。
- 用本地受控源与 HTTP 代理验证了真实抓取→格式/协议筛选→HEAD 校验→Redis 8 入库，随后删除测试代理。

## 真实 Geonode 接口发现

使用原采集参数 `filterLastChecked=10` 时，一次真实请求返回 HTTP 200，响应仅 42 字节，`total=0`、`data=[]`。新日志能区分这种空数据与网络超时或 HTTP 错误。

对照去掉 `filterLastChecked` 的请求返回 HTTP 200，`total=4696`、100 条记录，最新检查记录已是约 130 分钟前；新解析器从这批记录保留 58 条 HTTP/HTTPS 候选，过滤 42 条 SOCKS4/5。此对照仅用于诊断，1.5.0 发布时仍保留原筛选参数。后续变更见下节。

这说明当时没有满足“最近 10 分钟检查”的记录；当前证据不能将历史失败归因为请求限频。15 分钟周期降低自动采集次数，但不会让上游缺失的新记录自动出现。候选抓取数量也不等于校验通过的可用代理数量。

## 2026-11-19：更换为 500 条响应时间排序接口

根据新的采集要求，工作区抓取地址改为：

```text
https://proxylist.geonode.com/api/proxy-list?page=1&limit=500&sort_by=responseTime&sort_type=asc
```

移除最近检查时间筛选，每页获取 500 条，按 `responseTime` 升序返回。新增回归断言检查请求地址和参数；全套 446 项测试通过。

从本地源码构建独立镜像 `proxy-pool:local-geonode-500`，仍使用 Node 26、Python 3.13 基础镜像。真实抓取通过临时容器运行，未更新已有本地应用容器、云服务器或 GitHub。

- 本机容器直连：两次请求均在 5 秒后超时，正确记录 `ReadTimeout`。
- 同一镜像通过本机 `7897` 代理：首次请求超时，第二次成功；含重试总耗时约 11.09 秒。
- 最终 HTTP 200，JSON 响应 231137 字节，上游总量 4660，实际返回 500 条。
- 协议分布：HTTP 192、HTTPS 104、SOCKS5 134、SOCKS4 70。
- 抓取器产出 296 条去重后的 HTTP/HTTPS 候选，过滤 204 条 SOCKS4/5；返回记录的数值响应时间升序检查通过。
- 真实调用经过 `Fetcher.run()` 来源选择、`GeonodeFetcher` 解析和结果统计，记录 `result=success`、`yielded=296`、连续失败 0。

这次验证覆盖实际抓取与解析，未逐个验证这 296 条候选的代理可用性。临时容器及其测试配置在完成后自动删除，构建镜像保留用于后续复测。

## 保留的本地测试服务

- WebUI：`http://127.0.0.1:15010/webui/`。
- 测试环境启用独立的本地登录配置，仅本机端口可访问。
- 应用容器：`proxy-pool-local-geonode-app`。
- 数据库容器：`proxy-pool-local-geonode-redis`。
- 网络：`proxy-pool-local-geonode-net`。
- 数据与日志：`/private/tmp/proxy-pool-local-1.5.0.RljAJr/data/` 和 `log/`。

测试初始配置禁用全部外部代理源，避免测试容器持续自动请求公开站点。受控验证已经完成；需要继续真实定时观察时，可在测试 WebUI 中启用 Geonode，并保持“直连采集”选项。
