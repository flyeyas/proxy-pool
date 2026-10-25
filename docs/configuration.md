# 配置参考

配置文件 `setting.py` 位于项目的主目录下，配置主要分为五类：**服务配置**、**数据库配置**、**采集配置**、**校验配置**、**调度配置**。

## 环境变量与 .env 文件

所有下述配置项（`PROXY_FETCHER_EXCLUDE` 除外）都支持同名环境变量覆盖，优先级为：

```
系统环境变量 > .env.local(本地开发) > .env(线上默认) > setting.py 默认值
```

环境配置文件在启动时自动加载（见 `util/envHandler.py`），区分线上/线下两种环境：

- **`.env`**：线上默认配置，部署时放在服务器项目根目录
- **`.env.local`**：本地开发覆盖配置，优先级更高；已被 `.gitignore` 排除，线上服务器不应存在此文件

使用方法：

```bash
cp .env.example .env        # 线上配置
cp .env.example .env.local  # 本地开发, 只写需要覆盖的项
```

`.env` / `.env.local` 均不入库，适合存放数据库密码等不便提交的配置；支持 `#` 注释与引号包裹的值，删除某行即回退到 `setting.py` 默认值。启动日志中会打印实际加载了哪些配置文件（`ProxyPool env files loaded: ...`）。

## 服务配置

### `HOST`

API 服务监听的 IP。本机访问设置为 `127.0.0.1`，开启远程访问设置为 `0.0.0.0`。

- 默认值：`"0.0.0.0"`

### `PORT`

API 服务监听的端口。

- 默认值：`5010`

## 数据库配置

### `DB_CONN`

存放代理 IP 的数据库 URI，配置格式为：

```
db_type://[[user]:[pwd]]@ip:port/[db]
```

目前支持的 `db_type`：`redis`、`ssdb`。

配置示例：

```python
# Redis
DB_CONN = 'redis://@127.0.0.1:6379'
DB_CONN = 'redis://:123456@127.0.0.1:6379'
DB_CONN = 'redis://:123456@127.0.0.1:6379/15'

# SSDB
DB_CONN = 'ssdb://@127.0.0.1:8888'
DB_CONN = 'ssdb://:123456@127.0.0.1:8888'
```

### `TABLE_NAME`

存放代理的数据载体名称。SSDB 和 Redis 的存放结构为 hash。

- 默认值：`"use_proxy"`

## 采集配置

代理采集采用插件架构，调度器自动扫描 `fetcher/sources/` 目录，加载所有 `enabled=True` 的代理源。新增代理源只需在 `sources/` 下创建文件，无需修改配置。

查看当前启用的代理源：

```bash
python proxyPool.py fetcher
```

### `PROXY_FETCHER_EXCLUDE`

代理源黑名单。列表中的类名对应的代理源不会被加载，即使 `enabled=True`。适用于临时禁用某个代理源而不修改其源文件。

```python
PROXY_FETCHER_EXCLUDE = [
    # "BinglxFetcher",   # 临时禁用冰凌代理
]
```

如需永久禁用，建议直接在源文件中设置 `enabled = False`。

除了修改本文件，也可以直接在 [WebUI 后台](webui.md) 的"代理源"页点击开关启停代理源（运行时黑名单，持久化在项目根目录 `runtime_config.json`，不入库）。两份黑名单取并集生效，每轮抓取前读取，热生效、无需重启进程。

### `FETCHER_AUTO_DISABLE_FAILS`

代理源连续抓取失败（抛异常或本轮产出 0 条代理）达到该次数后**自动禁用**该源，避免持续浪费抓取线程与请求。设为 `0` 关闭自动禁用。

- 自动禁用的源在 WebUI 代理源页有红色标注（失败原因、最近错误、禁用时间），可点击「启用」重新观察（统计会重置）
- 成功一次即重置连续失败计数；一旦达到阈值被禁用，需人工启用恢复（不会自动反复横跳）
- 支持环境变量：`FETCHER_AUTO_DISABLE_FAILS=10`

```python
FETCHER_AUTO_DISABLE_FAILS = 5    # 默认: 连续失败 5 次自动禁用
```

## 校验配置

### `HTTP_URL`

用于检验代理是否可用的地址。

- 默认值：`"http://httpbin.org"`

### `HTTPS_URL`

用于检验代理是否支持 HTTPS 的地址。

- 默认值：`"https://www.qq.com"`

### `VERIFY_TIMEOUT`

检验代理的超时时间，单位秒。使用代理访问 `HTTP_URL` / `HTTPS_URL` 耗时超过 `VERIFY_TIMEOUT` 时，视为代理不可用。

- 默认值：`10`

### `MAX_FAIL_COUNT`

检验代理允许的最大失败次数。超过则剔除代理。

- 默认值：`0`（即失败一次即删除）

### `POOL_SIZE_MIN`

代理检测定时任务运行前，若代理数量小于 `POOL_SIZE_MIN`，则先运行抓取程序。

- 默认值：`20`

## 代理属性

### `PROXY_REGION`

是否启用代理地域属性。开启后会尝试解析代理 IP 的地理位置信息。

- 默认值：`True`

## 调度配置

### `TIMEZONE`

调度器的时区设置。如果在虚拟机上运行时出现 `ValueError: Timezone offset does not match system offset` 错误，请设置该配置项。

- 默认值：`"Asia/Shanghai"`