# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     webuiApi.py
   Description :   WebUI后台接口: 静态页面托管/代理源列表/手动刷新/日志查看
   Author :        flyeyas
   date：          2026/10/24
-------------------------------------------------
   Change Activity:
                   2026/10/24: WebUI后台接口
                   2026/10/25: 新增账号登录(WEBUI_USERNAME/WEBUI_PASSWORD)
-------------------------------------------------
"""
__author__ = 'flyeyas'

import os
import re
import hmac
import threading
from threading import Thread

from flask import Blueprint, send_from_directory, request, session

from handler.logHandler import listLogFiles, readRecentLines
from handler.configHandler import ConfigHandler
from fetcher.baseFetcher import BaseFetcher
from helper.fetch import _get_sources_dir, _load_module
from helper.fetcherConfig import getRuntimeExclude, toggleRuntimeExclude

webui_bp = Blueprint('webui', __name__)

# webui 前端构建产物目录 (webui/dist)
WEBUI_DIST_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'webui', 'dist')

# 手动刷新锁: 防止重复触发
# 注意: 仅进程内有效, gunicorn 多 worker 部署时各 worker 独立, 极端情况可能并发触发多次
_refresh_lock = threading.Lock()
_refresh_running = False

_LOG_NAME_PATTERN = re.compile(r'^[A-Za-z0-9_]+$')

# 登录后才能访问的管理类接口(对外的 /get /all /count 等代理API不受影响)
# 注意使用前缀匹配, 覆盖 /fetchers/ 与 /fetchers/toggle/ 等子路径
_PROTECTED_PREFIXES = ('/fetchers', '/logs', '/delete/')
_SESSION_KEY = 'webui_login'


def _authRequired():
    """WEBUI_PASSWORD 为空时登录验证关闭"""
    return bool(ConfigHandler().webuiPassword)


@webui_bp.before_app_request
def _authGuard():
    """全局鉴权拦截: 未登录时返回 401, 前端跳转登录页"""
    if not _authRequired():
        return None
    path = request.path
    if path.startswith(_PROTECTED_PREFIXES) or (path == '/refresh/' and request.method == 'POST'):
        if not session.get(_SESSION_KEY):
            return {"code": 401, "src": "unauthorized"}, 401
    return None


@webui_bp.route('/auth/status/')
def authStatus():
    """前端启动时查询: 是否启用登录/是否已登录"""
    return {"required": _authRequired(), "logged_in": bool(session.get(_SESSION_KEY))}


@webui_bp.route('/auth/login/', methods=['POST'])
def authLogin():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    conf = ConfigHandler()
    ok_user = hmac.compare_digest(username, conf.webuiUsername)
    ok_pwd = hmac.compare_digest(password, conf.webuiPassword)
    if ok_user and ok_pwd:
        session[_SESSION_KEY] = True
        return {"code": 0, "src": "ok"}
    return {"code": 1, "src": "invalid username or password"}, 401


@webui_bp.route('/auth/logout/')
def authLogout():
    session.clear()
    return {"code": 0, "src": "ok"}


@webui_bp.route('/webui/')
@webui_bp.route('/webui/<path:filename>')
def webui(filename='index.html'):
    """托管 webui/dist 下的前端构建产物"""
    if not os.path.isfile(os.path.join(WEBUI_DIST_PATH, 'index.html')):
        return ('WebUI is not built yet. Run `npm install && npm run build` '
                'in the webui/ directory first, or use `npm run dev` for development.'), 503
    # SPA history 路由: 不存在的文件路径回退到 index.html
    if filename != 'index.html' and not os.path.isfile(os.path.join(WEBUI_DIST_PATH, filename)):
        filename = 'index.html'
    return send_from_directory(WEBUI_DIST_PATH, filename)


@webui_bp.route('/fetchers/')
def fetchers():
    """列出全部代理源(含 disabled), 及其启用/黑名单状态"""
    # 黑名单匹配口径与 helper/fetch.py 一致: fetcher 类名(__name__)
    setting_exclude = ConfigHandler().fetcherExclude
    runtime_exclude = set(getRuntimeExclude())
    sources_dir = _get_sources_dir()
    result = []
    for filename in os.listdir(sources_dir):
        if not filename.endswith('.py') or filename.startswith('_'):
            continue
        module_name = "fetcher.sources.%s" % filename[:-3]
        module = _load_module(module_name, os.path.join(sources_dir, filename))
        if module is None:
            continue
        for attr_name in dir(module):
            attr = getattr(module, attr_name, None)
            if (attr and isinstance(attr, type)
                    and issubclass(attr, BaseFetcher)
                    and attr is not BaseFetcher
                    and attr.name):
                class_name = attr.__name__
                result.append({
                    "name": attr.name,
                    "class_name": class_name,
                    "url": getattr(attr, 'url', '') or '',
                    "enabled": bool(getattr(attr, 'enabled', True)),
                    "excluded": class_name in setting_exclude or class_name in runtime_exclude,
                    "setting_excluded": class_name in setting_exclude,
                    "runtime_excluded": class_name in runtime_exclude,
                })
    result.sort(key=lambda item: item["name"])
    return {"fetchers": result}


@webui_bp.route('/fetchers/toggle/', methods=['POST'])
def fetcherToggle():
    """后台开关代理源(写入运行时黑名单, 下轮抓取/手动刷新即生效)"""
    data = request.get_json(silent=True) or {}
    class_name = (data.get('class_name') or '').strip()
    enable = bool(data.get('enable'))
    if not class_name or not _LOG_NAME_PATTERN.match(class_name):
        return {"code": 1, "src": "invalid fetcher class name"}, 400
    exclude = toggleRuntimeExclude(class_name, enable)
    return {"code": 0, "src": "ok", "fetcher_exclude": exclude}


def _runFetchAndCheck():
    """后台任务: 执行一次完整抓取入库 + 全池复检(与 scheduler 逻辑一致)"""
    global _refresh_running
    try:
        from util.six import Queue
        from helper.fetch import Fetcher
        from helper.check import Checker
        from handler.proxyHandler import ProxyHandler
        from handler.logHandler import LogHandler

        logger = LogHandler("webui")
        logger.info("WebUI refresh: fetch start")
        raw_queue = Queue()
        for proxy in Fetcher().run():
            raw_queue.put(proxy)
        Checker("raw", raw_queue)

        logger.info("WebUI refresh: check start")
        proxy_handler = ProxyHandler()
        use_queue = Queue()
        for proxy in proxy_handler.getAll():
            use_queue.put(proxy)
        Checker("use", use_queue)
        logger.info("WebUI refresh: complete")
    except Exception as e:
        from handler.logHandler import LogHandler
        LogHandler("webui").error("WebUI refresh error: %s" % e)
    finally:
        with _refresh_lock:
            _refresh_running = False


@webui_bp.route('/refresh/', methods=['POST'])
def refresh():
    """手动触发一次抓取+校验(后台线程执行, 立即返回)"""
    global _refresh_running
    with _refresh_lock:
        if _refresh_running:
            return {"code": 1, "src": "already running"}
        _refresh_running = True
    Thread(target=_runFetchAndCheck, daemon=True).start()
    return {"code": 0, "src": "started"}


@webui_bp.route('/logs/')
def logs():
    """日志文件列表 / 指定日志尾部内容"""
    name = request.args.get("name", "").strip()
    if not name:
        return {"logs": listLogFiles()}
    if not _LOG_NAME_PATTERN.match(name):
        return {"code": 1, "src": "invalid log name"}, 400
    lines = request.args.get("lines", default=200, type=int)
    lines = max(1, min(lines, 1000))
    ok, content = readRecentLines(name, lines)
    if not ok:
        return {"code": 1, "src": "log file not found"}, 404
    return {"name": name, "lines": content}
