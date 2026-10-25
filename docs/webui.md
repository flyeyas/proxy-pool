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
- **代理源**：列出 `fetcher/sources/` 下全部代理源，支持**后台启停开关**（写入运行时黑名单 `runtime_config.json`，与 `setting.py` 的 `PROXY_FETCHER_EXCLUDE` 取并集生效；每轮抓取前读取，热生效；被 `setting.py` 黑名单或源文件 `enabled=False` 禁用的源不可在后台开启）
- **日志**：查看 `log/` 目录下各日志文件的尾部内容（fetch、checker、scheduler 等），支持行数选择与自动刷新

    !!! warning "Windows 限制"
        日志在 Windows 下不落盘（见 `handler/logHandler.py`），日志页将为空。

## 登录验证

WebUI 支持简单的账号密码登录（会话 Cookie 方案，无独立用户系统）。在 `.env`（或环境变量）中设置：

```bash
WEBUI_USERNAME=admin          # 默认 admin
WEBUI_PASSWORD=your-password  # 设置后启用登录验证, 留空则不启用
# WEBUI_SECRET_KEY=xxx        # 可选: 会话签名密钥, 不设则由账号密码自动派生
```

启用后：

- 浏览器访问 `/webui/` 会先跳转登录页
- 所有数据与管理接口（`/get`、`/pop`、`/all`、`/count`、`/delete`、`/fetchers`、`/logs`、`POST /refresh`、`/auth/tokens`）未授权返回 401
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

## 相关接口

WebUI 使用的后端接口见 [API 使用](api.md)：`/fetchers`、`/refresh`（POST）、`/logs`。
