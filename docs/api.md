# API 使用

## 响应格式约定

所有接口返回统一的 JSON 包裹格式，`code` 与 HTTP 状态码一致（保持真实语义，不做永远 200）：

```json
// 成功: 2xx, data 为资源数据(无数据时为 null)
{"code": 200, "message": "ok", "data": {"proxy": "1.2.3.4:8080", "...": "..."}}

// 失败: 4xx/5xx, data 恒为 null
{"code": 404, "message": "no proxy available", "data": null}
```

常见 `code`：`200` 成功、`201` 已创建（生成 token）、`202` 已受理（手动触发刷新）、`400` 参数错误、`401` 未授权（需登录会话或 API Token）、`404` 资源不存在（如池空时 `/get`）、`409` 冲突（重复触发刷新）。

## 接口列表

启动 ProxyPool 的 `server` 后会提供如下 HTTP 接口：

| 接口 | 方法 | 说明 | 参数 |
|------|------|------|------|
| `/` | GET | 302 跳转到 `/webui/`（接口说明见 WebUI「API 文档」页） | 无 |
| `/get` | GET | 随机返回一个代理（池空 404） | 可选：`?type=https` 过滤 HTTPS 代理 |
| `/pop` | GET | 返回并删除一个代理（池空 404） | 可选：`?type=https` 过滤 HTTPS 代理 |
| `/all` | GET | 返回所有代理：`{"count": N, "proxies": [...]}` | 可选：`?type=https` 过滤 HTTPS 代理 |
| `/count` | GET | 返回代理数量统计 | 无 |
| `/delete` | DELETE | 删除指定代理（兼容 GET）；`200 {"deleted": true}` / 404 | `?proxy=host:port` |
| `/fetchers` | GET | 返回全部代理源及启用状态 | 无 |
| `/fetchers/<类名>` | PATCH | 启停指定代理源，body `{"enabled": bool}` | 无 |
| `/refresh` | POST | 手动触发一次抓取+校验（202 后台执行；重复触发 409） | 无 |
| `/logs` | GET | 日志文件列表 / 指定日志尾部内容 | 可选：`?name=fetch&lines=200`（lines 上限 1000） |
| `/webui/` | GET | WebUI 管理后台页面 | 无，详见 [WebUI 后台](webui.md) |

## 调用示例

### 在爬虫中使用

通过调用 API 接口来使用代理池：

```python
import requests


def get_proxy():
    return requests.get("http://127.0.0.1:5010/get/").json()


def delete_proxy(proxy):
    requests.get("http://127.0.0.1:5010/delete/?proxy={}".format(proxy))


def get_html():
    retry_count = 5
    proxy = get_proxy().get("proxy")
    while retry_count > 0:
        try:
            # 使用代理访问
            html = requests.get(
                "http://www.example.com",
                proxies={
                    "http": "http://{}".format(proxy),
                    "https": "https://{}".format(proxy),
                },
            )
            return html
        except Exception:
            retry_count -= 1
            # 删除代理池中代理
            delete_proxy(proxy)
    return None
```

本例中在本地 `127.0.0.1` 启动端口为 `5010` 的 `server`，使用 `/get` 接口获取代理，`/delete` 删除代理。

### 获取 HTTPS 代理

```python
# 只获取支持 HTTPS 的代理
proxy = requests.get("http://127.0.0.1:5010/get/?type=https").json()
```

### 获取代理统计

```python
# 返回代理数量、类型分布、来源分布
stats = requests.get("http://127.0.0.1:5010/count/").json()
# 示例返回: {"http_type": {"http": 10, "https": 5}, "source": {"freeProxy01": 8, "freeProxy02": 7}, "count": 15}
```

## 直接读取数据库

除了通过 API 接口，也可以直接读取数据库获取代理。目前支持两种数据库：Redis 和 SSDB。

- **Redis**：存储结构为 hash，hash name 为配置项中的 `TABLE_NAME`（默认 `use_proxy`）
- **SSDB**：存储结构为 hash，hash name 为配置项中的 `TABLE_NAME`

可以在代码中自行读取数据库获取代理列表。