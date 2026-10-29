English | [简体中文](README.zh-CN.md)

# ProxyPool — Crawler Proxy IP Pool

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

> **Acknowledgement**: this project is a secondary development on top of the open-source project [jhao104/proxy_pool](https://github.com/jhao104/proxy_pool). Many thanks to the original author [@jhao104](https://github.com/jhao104).

## Introduction

A crawler proxy IP pool: it periodically crawls free proxies published on the web, validates them before storing, and re-checks them on a schedule to keep the pool usable. Use it through a **RESTful API**, the **CLI**, or the **WebUI admin console**. Proxy sources are pluggable so you can extend the pool's quality and size.

Main enhancements over the original project:

- **WebUI admin console** (`/webui/`): dashboard (stat cards / protocol & source distribution / proxy list with filter, search and delete), **runtime fetcher enable/disable toggles** (hot reload), log viewer, and a manual "fetch + validate now" trigger
- **Username / password login**: protects management endpoints (one line in `.env` to enable); the data APIs accept **API tokens** for programmatic access
- **Fetcher monitoring**: sources failing consecutively (exception or zero output) are **auto-disabled with an annotated reason** and can be re-enabled from the WebUI
- **`.env` based configuration**: precedence `system env > .env.local (local dev) > .env (production) > setting.py`; secrets never enter git or the image
- **Docker multi-stage build**: the WebUI frontend is built inside the image

## Requirements

- Python 3.10 – 3.13
- Redis or SSDB (storage)
- Node.js 18+ (only for building the WebUI frontend; not needed for Docker deployment)

## Quick Start

```bash
# 1. Get the code
git clone https://github.com/flyeyas/proxy-pool.git
cd proxy-pool

# 2. Install dependencies (in China you may add -i https://mirrors.aliyun.com/pypi/simple/)
pip install -r requirements.txt

# 3. Configure: copy the template and fill in your database connection
cp .env.example .env
#   Edit .env — at minimum set DB_CONN, e.g.:
#   DB_CONN=redis://:password@127.0.0.1:6379/0

# 4. Start (two processes)
python proxyPool.py schedule   # scheduler: periodic crawl + re-check
python proxyPool.py server     # API + WebUI (defaults to 0.0.0.0:5010)

# 5. (Optional) build the WebUI frontend
cd webui && npm install && npm run build
#   then open http://127.0.0.1:5010/webui/
```

Also: `python proxyPool.py fetcher` lists the currently enabled proxy sources.

### Configuration

Precedence: **system environment variables > `.env.local` (local dev overrides, not committed) > `.env` (production defaults) > `setting.py`**

Common variables (see [.env.example](.env.example) and [setting.py](setting.py) for the full list):

| Variable | Description | Default |
| ---- | ---- | ---- |
| `DB_CONN` | Database URI, `redis://:pwd@ip:port/db` or `ssdb://:pwd@ip:port` | local Redis |
| `HOST` / `PORT` | API server address / port | `0.0.0.0` / `5010` |
| `TABLE_NAME` | Storage table name | `use_proxy` |
| `VERIFY_TIMEOUT` | Proxy validation timeout (seconds) | `10` |
| `MAX_FAIL_COUNT` | Removal threshold of failed re-checks | `0` (removed on first failure) |
| `POOL_SIZE_MIN` | Refetch trigger when pool drops below | `20` |
| `FETCHER_AUTO_DISABLE_FAILS` | Auto-disable a source after N consecutive failures (exception or zero output); 0 disables the feature | `5` |
| `WEBUI_USERNAME` / `WEBUI_PASSWORD` | WebUI login (empty password = login disabled) | `admin` / empty |

## WebUI Admin Console

Open `http://127.0.0.1:5010/webui/` (build the frontend first; Docker images ship with it pre-built):

- **Dashboard**: total / HTTP / HTTPS / source counts; protocol & source distribution bars; proxy table (protocol filter, keyword search, single delete); 5s auto refresh; "fetch & validate now" button
- **Fetchers**: all sources with status; **runtime enable/disable toggles** (persisted to `runtime_config.json`, merged with the `setting.py` blacklist, hot reload); consecutive-failure auto-disable with annotated reason
- **Tokens**: create / rename / delete **API tokens** for external programs, with a validity period (7 / 30 / 90 / 365 days or permanent) and one-click copy
- **Logs**: tail of `fetch` / `checker` / `scheduler` etc., selectable line count, auto refresh

Set `WEBUI_PASSWORD` in `.env` to enable authentication: all data and management APIs (including `/get` `/all` `/count`) then require authorization — the WebUI uses the login session, external programs use an API token.

Frontend dev mode (requires the server running):

```bash
cd webui && npm run dev   # http://localhost:5173, API proxied to 127.0.0.1:5010
```

## API

With `server` running. **When login is enabled** (`WEBUI_PASSWORD` in `.env`), all endpoints below require authorization — the WebUI uses its session automatically; external programs must send an **API token** (either `?token=xxx` or an `Authorization: Bearer xxx` header; tokens are created on the WebUI "Token" page).

Responses use a unified envelope (`code` always equals the HTTP status):

```json
// success: 2xx
{"code": 200, "message": "ok", "data": { ...resource... }}
// failure: 4xx/5xx, data is always null
{"code": 404, "message": "no proxy available", "data": null}
```

- `code`: integer equal to the HTTP status (200 / 201 created / 202 accepted / 400 bad request / 401 unauthorized / 404 not found / 409 conflict)
- `data`: the resource payload, `null` when none; collections look like `{"count": N, "proxies": [...]}`
- Client check: `resp.ok` or `code < 400` means success

| Endpoint | Method | Description | Params |
| ----| ---- | ---- | ----|
| `/` | GET | Redirects to `/webui/` (API docs live in the WebUI "API 文档" page) | none |
| `/get` | GET | Random proxy (404 when pool empty) | optional `?type=https` |
| `/pop` | GET | Get and remove a proxy (404 when empty) | optional `?type=https` |
| `/all` | GET | All proxies as `{"count": N, "proxies": [...]}` | optional `?type=https` |
| `/count` | GET | Pool statistics with distribution | none |
| `/delete` | DELETE | Delete a proxy (GET also accepted); `{"deleted": true}` or 404 | `?proxy=host:port` |
| `/fetchers` | GET | Fetcher list with status and stats | none |
| `/fetchers/<class>` | PATCH | Enable/disable a fetcher, body `{"enabled": bool}` | none |
| `/refresh` | POST | Trigger one fetch + validate run (202, runs in background) | none |
| `/logs` | GET | Log file list / tail content | optional `?name=fetch&lines=200` |
| `/webui/` | GET | WebUI admin console | none |

### Using in a crawler

```python
import requests

TOKEN = "pp_xxxxxxxxxx"   # generated on the WebUI "Token" page
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

## Docker

The image is a multi-stage build (Node builds the frontend → Python runtime), with the WebUI bundled:

```bash
# Build the image
docker build -t flyeyas/proxy-pool .

# Recommended: self-contained docker compose deployment
# (bundled Redis with persistence, config from .env, logs mounted to ./log/)
cp .env.example .env && vim .env   # REDIS_PASSWORD must match the password in DB_CONN; set WEBUI_PASSWORD
docker compose up -d --build

# Or run manually (logs mounted to a host directory)
docker run -d --env DB_CONN=redis://:password@ip:port/0 \
  --env WEBUI_PASSWORD=change-me \
  -p 5010:5010 -v /host/log/dir:/app/log \
  flyeyas/proxy-pool
```

`/app/log` is declared as a volume — mount it and `tail -f` the log files directly on the host (rotated hourly, 7 days retained). See [docs/docker.md](docs/docker.md) (Chinese) for details.

## Extending Proxy Sources

Create a `.py` file under `fetcher/sources/`, subclass `BaseFetcher`, declare `name` / `url` / `enabled`, and implement `fetch()` yielding `host:port` strings:

```python
from fetcher.baseFetcher import BaseFetcher
from util.webRequest import WebRequest


class MyProxyFetcher(BaseFetcher):
    """My proxy source"""

    name = "myproxy"
    url = "https://www.example.com/"
    enabled = True

    def fetch(self):
        r = WebRequest().get("https://www.example.com/api/proxies")
        for item in r.json:
            yield item["ip"] + ":" + item["port"]
```

The scheduler picks up new sources automatically on the next fetch round — no configuration change needed. Three ways to disable a source:

1. **WebUI toggle** (recommended, hot reload) — click "disable" on the Fetchers page
2. Add its class name to the `PROXY_FETCHER_EXCLUDE` blacklist in `setting.py`
3. Set `enabled = False` in the source file (permanent)

## Running Tests

```bash
pip install pytest pytest-cov fakeredis

pytest tests/unit/                        # unit tests (no external deps)
pytest tests/api/                         # API tests (mocked database)
pytest tests/integration/ -m integration  # integration tests (needs a real Redis)
pytest                                    # everything
```

## Acknowledgements

- Based on [jhao104/proxy_pool](https://github.com/jhao104/proxy_pool) — thanks to [@jhao104](https://github.com/jhao104) and all contributors
- Free proxies come from public proxy sites; quality varies. For learning and research use only
