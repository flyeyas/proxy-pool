简体中文 | [English](README.md)

# ProxyPool 爬虫代理IP池

```
    ______                        ______             _
    | ___ \_                      | ___ \           | |
    | |_/ / \__ __   __  _ __   _ | |_/ /___   ___  | |
    |  __/|  _// _ \ \ \/ /| | | ||  __// _ \ / _ \ | |
    | |   | | | (_) | >  < \ |_| || |  | (_) | (_) || |___
    \_|   |_|  \___/ /_/\_\ \__  |\_|   \___/ \___/ \_____/
                           __ / /
                          /___ /
```

> **声明**：本项目基于开源项目 [jhao104/proxy_pool](https://github.com/jhao104/proxy_pool) 二次开发，感谢原作者 [@jhao104](https://github.com/jhao104) 的开源贡献。

## 项目简介

爬虫代理IP池：定时采集网上发布的免费代理、验证后入库，定时复检保证代理可用性，通过 **RESTful API**、**CLI** 和 **WebUI 管理后台** 三种方式使用。支持扩展代理源以提升代理池的质量和数量。

相对原项目的主要增强：

- **WebUI 管理后台**（`/webui/`）：仪表盘（统计卡片 / 协议与来源分布 / 代理列表筛选搜索删除）、**代理源后台启停开关**（热生效）、运行日志查看、手动触发"抓取 + 校验"
- **账号密码登录**：保护管理接口（`.env` 一行配置即可启用），对外的代理 API 不受影响
- **`.env` 环境配置**：`系统环境变量 > .env.local(本地) > .env(线上) > setting.py` 四级优先，密码等敏感信息不入库
- **Docker 多阶段构建**：镜像内自动构建 WebUI 前端

## 环境要求

- Python 3.10 – 3.13
- Redis 或 SSDB（存储）
- Node.js 18+（仅构建 WebUI 前端时需要，Docker 部署无需本地安装）

## 快速开始

```bash
# 1. 获取代码
git clone https://github.com/flyeyas/proxy-pool.git
cd proxy-pool

# 2. 安装依赖（国内可加 -i https://mirrors.aliyun.com/pypi/simple/）
pip install -r requirements.txt

# 3. 配置：复制模板并填写数据库连接
cp .env.example .env
#   编辑 .env，至少配置 DB_CONN，如：
#   DB_CONN=redis://:password@127.0.0.1:6379/0

# 4. 启动（两个进程）
python proxyPool.py schedule   # 调度程序：定时抓取 + 复检
python proxyPool.py server     # API + WebUI 服务（默认 0.0.0.0:5010）

# 5.（可选）构建 WebUI 前端
cd webui && npm install && npm run build
#   然后访问 http://127.0.0.1:5010/webui/
```

其他命令：`python proxyPool.py fetcher` 查看当前启用的代理源列表。

### 环境配置说明

配置优先级：**系统环境变量 > `.env.local`（本地开发覆盖，不入库）> `.env`（线上默认）> `setting.py`**

常用配置项（完整列表见 [.env.example](.env.example) 与 [setting.py](setting.py)）：

| 变量 | 说明 | 默认值 |
| ---- | ---- | ---- |
| `DB_CONN` | 数据库连接，`redis://:pwd@ip:port/db` 或 `ssdb://:pwd@ip:port` | 本地 Redis |
| `HOST` / `PORT` | API 服务地址 / 端口 | `0.0.0.0` / `5010` |
| `TABLE_NAME` | 代理存储表名 | `use_proxy` |
| `VERIFY_TIMEOUT` | 代理验证超时（秒） | `10` |
| `MAX_FAIL_COUNT` | 复检失败几次后剔除 | `0`（失败一次即剔除） |
| `POOL_SIZE_MIN` | 池内代理低于该数量触发补抓 | `20` |
| `FETCHER_AUTO_DISABLE_FAILS` | 代理源连续抓取失败(异常或0产出)达到该次数自动禁用，0 关闭 | `5` |
| `WEBUI_USERNAME` / `WEBUI_PASSWORD` | WebUI 登录账号（密码留空则不启用登录） | `admin` / 空 |

## WebUI 管理后台

访问 `http://127.0.0.1:5010/webui/`（需先构建前端，Docker 部署已内置）：

- **仪表盘**：代理总数 / HTTP / HTTPS / 来源数；协议与来源分布条形图；代理列表（协议筛选、关键字搜索、单条删除）；5 秒自动刷新；「立即抓取并校验」按钮
- **代理源**：全部代理源状态，支持**后台启停开关**（写入运行时黑名单 `runtime_config.json`，与 `setting.py` 黑名单并集生效，热生效）
- **Token**：生成/删除 **API Token**（供爬虫等外部程序直连接口），可设置有效期（7 天 / 30 天 / 90 天 / 1 年 / 永久），支持一键复制
- **日志**：查看 `fetch` / `checker` / `scheduler` 等日志尾部内容，行数可选、自动刷新

在 `.env` 中设置 `WEBUI_PASSWORD` 即启用登录验证：所有数据与管理接口（含 `/get` `/all` `/count` 等）均需授权——WebUI 走登录会话，外部程序走 API Token。

前端开发模式（需同时运行 server）：

```bash
cd webui && npm run dev   # http://localhost:5173，API 自动代理到 127.0.0.1:5010
```

## API 接口

启动 `server` 后提供如下接口。**启用登录后**（`.env` 设置 `WEBUI_PASSWORD`），下表接口均需授权——WebUI 内自动使用登录会话；**外部程序直连需带 API Token**（请求加 `?token=xxx` 或 `Authorization: Bearer xxx` 头，Token 在 WebUI「Token」页生成，可设置有效期）。

接口采用统一的响应包裹格式（HTTP 状态码与 `code` 一致，保持真实语义）：

```json
// 成功: 2xx
{"code": 200, "message": "ok", "data": { ...资源数据... }}
// 失败: 4xx/5xx, data 恒为 null
{"code": 404, "message": "no proxy available", "data": null}
```

- `code`：与 HTTP 状态码相同的整数（200/201 创建 / 202 已受理 / 400 参数错误 / 401 未授权 / 404 不存在 / 409 冲突）
- `data`：资源数据，无数据时为 `null`；集合形如 `{"count": N, "proxies": [...]}`
- 客户端判定：`resp.ok` 或 `code < 400` 即成功

| 接口 | 方法 | 说明 | 参数 |
| ----| ---- | ---- | ----|
| `/` | GET | 302 跳转到 `/webui/`（接口说明在 WebUI「API 文档」页） | 无 |
| `/get` | GET | 随机获取一个代理（池空 404） | 可选 `?type=https` |
| `/pop` | GET | 获取并删除一个代理（池空 404） | 可选 `?type=https` |
| `/all` | GET | 获取所有代理，返回 `{"count": N, "proxies": [...]}` | 可选 `?type=https` |
| `/count` | GET | 代理数量统计（含分布） | 无 |
| `/delete` | DELETE | 删除代理（兼容 GET），返回 `{"deleted": true}` 或 404 | `?proxy=host:port` |
| `/fetchers` | GET | 代理源列表及状态 | 无 |
| `/fetchers/<类名>` | PATCH | 启停代理源，body `{"enabled": bool}` | 无 |
| `/refresh` | POST | 手动触发一次抓取 + 校验（202，后台执行） | 无 |
| `/logs` | GET | 日志文件列表 / 尾部内容 | 可选 `?name=fetch&lines=200` |
| `/webui/` | GET | WebUI 管理后台 | 无 |

### 在爬虫中使用

```python
import requests

TOKEN = "pp_xxxxxxxxxx"   # 在 WebUI「Token」页生成
BASE = "http://127.0.0.1:5010"


def get_proxy():
    resp = requests.get(BASE + "/get/", params={"token": TOKEN})
    return resp.json() if resp.status_code == 200 else None


def delete_proxy(proxy):
    requests.delete(BASE + "/delete/", params={"proxy": proxy, "token": TOKEN})


def get_html():
    retry_count = 5
    proxy = get_proxy().get("proxy")
    while retry_count > 0:
        try:
            return requests.get(
                "http://www.example.com",
                proxies={"http": "http://{}".format(proxy), "https": "https://{}".format(proxy)},
            )
        except Exception:
            retry_count -= 1
            delete_proxy(proxy)
    return None
```

## Docker 部署

镜像采用多阶段构建（Node 构建前端 → Python 运行），WebUI 已内置：

```bash
# 构建镜像
docker build -t flyeyas/proxy-pool .

# 推荐: docker compose 自包含部署(内置Redis+持久化, 配置读 .env, 日志挂到宿主机 ./log/)
cp .env.example .env && vim .env   # REDIS_PASSWORD 与 DB_CONN 密码一致, WEBUI_PASSWORD
docker compose up -d --build

# 或手动运行(日志挂载到宿主机目录，可直接查看)
docker run -d --env DB_CONN=redis://:password@ip:port/0 \
  --env WEBUI_PASSWORD=change-me \
  -p 5010:5010 -v /宿主机日志目录:/app/log \
  flyeyas/proxy-pool
```

容器把 `/app/log` 声明为卷，挂载后在宿主机直接 `tail -f` 日志文件（按小时切割，保留 7 天），详见 [docs/docker.md](docs/docker.md)。

## 扩展代理源

在 `fetcher/sources/` 目录下新建 `.py` 文件，继承 `BaseFetcher`，声明 `name` / `url` / `enabled` 属性，实现 `fetch()` 以生成器形式 yield `host:port`：

```python
from fetcher.baseFetcher import BaseFetcher
from util.webRequest import WebRequest


class MyProxyFetcher(BaseFetcher):
    """我的代理源"""

    name = "myproxy"
    url = "https://www.example.com/"
    enabled = True

    def fetch(self):
        r = WebRequest().get("https://www.example.com/api/proxies")
        for item in r.json:
            yield item["ip"] + ":" + item["port"]
```

添加后调度器下轮抓取自动发现并启用，无需修改配置。禁用某个源的三种方式：

1. **WebUI 后台开关**（推荐，热生效）——代理源页点击「禁用」
2. `setting.py` 的 `PROXY_FETCHER_EXCLUDE` 黑名单中添加类名
3. 源文件中设置 `enabled = False`（永久禁用）

## 运行测试

```bash
pip install pytest pytest-cov fakeredis

pytest tests/unit/                        # 单元测试（零外部依赖）
pytest tests/api/                         # API 测试（mock 数据库）
pytest tests/integration/ -m integration  # 集成测试（需真实 Redis）
pytest                                    # 全部
```

## 致谢

- 本项目基于 [jhao104/proxy_pool](https://github.com/jhao104/proxy_pool) 二次开发，感谢原作者 [@jhao104](https://github.com/jhao104) 及所有贡献者
- 免费代理来自各公开代理站点，质量有限，仅供学习研究使用
