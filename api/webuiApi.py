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
import tempfile
import threading
from threading import Thread

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows 无 fcntl
    fcntl = None

from flask import Blueprint, send_from_directory, request, session

from handler import logHandler as _logHandler
from handler.logHandler import listLogFiles, readRecentLines
from handler.configHandler import ConfigHandler
from fetcher.baseFetcher import BaseFetcher
from helper.fetch import _get_sources_dir, _load_module
from helper.fetcherConfig import (getRuntimeExclude, toggleRuntimeExclude,
                                  getAutoDisable, getFetcherStats, enableFetcher)
from helper.apiToken import (listTokens, generateToken, deleteToken,
                             verifyApiToken, renameToken)
from api.response import apiOk, apiError

webui_bp = Blueprint('webui', __name__)

# webui 前端构建产物目录 (webui/dist)
WEBUI_DIST_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'webui', 'dist')

# 手动刷新防重入锁:
# - POSIX: flock 文件锁, gunicorn 多 worker 间互斥, 进程崩溃自动释放
# - Windows(无 fcntl): 退化为进程内标志
_REFRESH_LOCK_FILE = os.path.join(tempfile.gettempdir(), 'proxypool_webui_refresh.lock')
_refresh_fd = None
_refresh_lock = threading.Lock()
_refresh_running = False

_LOG_NAME_PATTERN = re.compile(r'^[A-Za-z0-9_]+$')


def _acquireRefreshLock():
    """获取刷新任务锁, 成功返回 fd(POSIX) 或 True(Windows), 已被占用返回 None"""
    global _refresh_fd, _refresh_running
    if fcntl is not None:
        fd = None
        try:
            fd = os.open(_REFRESH_LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if fd is not None:
                os.close(fd)
            return None
        _refresh_fd = fd
        return fd
    with _refresh_lock:
        if _refresh_running:
            return None
        _refresh_running = True
        return True


def _releaseRefreshLock(handle):
    """释放刷新任务锁(容忍 fd 已失效, 供异常路径兜底)"""
    global _refresh_fd, _refresh_running
    if handle is None:
        return
    if fcntl is not None and isinstance(handle, int):
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(handle)
        except OSError:
            pass
        if _refresh_fd == handle:
            _refresh_fd = None
    else:
        with _refresh_lock:
            _refresh_running = False

# 需要授权才能访问的接口(前缀匹配): 代理数据接口 + 管理接口 + token 管理
_PROTECTED_PREFIXES = ('/get', '/pop', '/all', '/count',
                       '/fetchers', '/logs', '/delete', '/auth/tokens')
_SESSION_KEY = 'webui_login'


def _authRequired():
    """WEBUI_PASSWORD 为空时登录验证关闭"""
    return bool(ConfigHandler().webuiPassword)


def _requestToken():
    """从 Authorization: Bearer 头或 ?token= 参数中取 API Token"""
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        return auth_header[len('Bearer '):].strip()
    return (request.args.get('token') or '').strip()


@webui_bp.before_app_request
def _authGuard():
    """
    全局鉴权拦截(需登录或有效 token):
    - WebUI 走登录会话; 外部程序直连代理接口用 ?token= 或 Bearer 头
    - /auth/tokens 为 token 管理接口, 仅允许会话登录, 不允许 token 自授权
    """
    if not _authRequired():
        return None
    path = request.path
    needs_auth = path.startswith(_PROTECTED_PREFIXES) \
        or (path == '/refresh/' and request.method == 'POST')
    if not needs_auth:
        return None
    if not path.startswith('/auth/tokens'):
        token = _requestToken()
        if token and verifyApiToken(token):
            return None
    if not session.get(_SESSION_KEY):
        return apiError(401, "authentication required: login session or API token")
    return None


@webui_bp.route('/auth/status/')
def authStatus():
    """前端启动时查询: 是否启用登录/是否已登录/程序版本"""
    import setting
    return apiOk({"required": _authRequired(),
                  "logged_in": bool(session.get(_SESSION_KEY)),
                  "version": setting.VERSION})


@webui_bp.route('/auth/login/', methods=['POST'])
def authLogin():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    conf = ConfigHandler()
    ok_user = hmac.compare_digest(username, str(conf.webuiUsername))
    ok_pwd = hmac.compare_digest(password, str(conf.webuiPassword))
    if ok_user and ok_pwd:
        session[_SESSION_KEY] = True
        return apiOk(message="login successful")
    return apiError(401, "invalid username or password")


@webui_bp.route('/auth/logout/', methods=['POST'])
def authLogout():
    session.clear()
    return apiOk(message="logged out")


@webui_bp.route('/auth/tokens/')
def tokenList():
    """token 列表"""
    return apiOk({"tokens": listTokens()})


@webui_bp.route('/auth/tokens/', methods=['POST'])
def tokenCreate():
    """生成 token: name 备注, expire_days 有效期天数(0=永久, 上限10年)"""
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    try:
        expire_days = int(data.get('expire_days', 0))
    except (TypeError, ValueError):
        expire_days = 0
    expire_days = max(0, min(expire_days, 3650))
    item = generateToken(name, expire_days)
    return apiOk({"token": item}, status=201)


@webui_bp.route('/auth/tokens/<token>/', methods=['DELETE'])
def tokenDelete(token):
    if not deleteToken(token):
        return apiError(404, "token not found")
    return apiOk(message="deleted")


@webui_bp.route('/auth/tokens/<token>/', methods=['PATCH'])
def tokenRename(token):
    """修改 token 备注名: body {"name": ...}"""
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return apiError(400, "name is required")
    item = renameToken(token, name)
    if item is None:
        return apiError(404, "token not found")
    return apiOk({"token": item})


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
    """列出全部代理源(含 disabled), 及其启用/黑名单状态与抓取统计"""
    # 黑名单匹配口径与 helper/fetch.py 一致: fetcher 类名(__name__)
    setting_exclude = ConfigHandler().fetcherExclude
    runtime_exclude = set(getRuntimeExclude())
    auto_exclude = set(getAutoDisable())
    stats = getFetcherStats()
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
                    "excluded": class_name in setting_exclude
                                or class_name in runtime_exclude
                                or class_name in auto_exclude,
                    "setting_excluded": class_name in setting_exclude,
                    "runtime_excluded": class_name in runtime_exclude,
                    "auto_disabled": class_name in auto_exclude,
                    "stats": stats.get(class_name, {}),
                })
    result.sort(key=lambda item: item["name"])
    return apiOk({"fetchers": result})


@webui_bp.route('/fetchers/<class_name>/', methods=['PATCH'])
def fetcherToggle(class_name):
    """
    开关代理源: body {"enabled": bool}
    - 禁用: 写入手动黑名单; 启用: 移出手动+自动禁用名单并重置抓取统计
    """
    data = request.get_json(silent=True) or {}
    enable = bool(data.get('enabled'))
    class_name = (class_name or '').strip()
    if not class_name or not _LOG_NAME_PATTERN.match(class_name):
        return apiError(400, "invalid fetcher class name")
    if enable:
        manual, auto = enableFetcher(class_name)
    else:
        manual = toggleRuntimeExclude(class_name, False)
        auto = getAutoDisable()
    return apiOk({"fetcher": class_name, "excluded": not enable,
                  "fetcher_exclude": manual, "auto_disable": auto})


def _runFetchAndCheck(lock_handle=None):
    """后台任务: 执行一次完整抓取入库 + 全池复检(与 scheduler 逻辑一致)"""
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
        _releaseRefreshLock(lock_handle)


@webui_bp.route('/refresh/', methods=['POST'])
def refresh():
    """手动触发一次抓取+校验(后台线程执行, 立即返回)"""
    lock_handle = _acquireRefreshLock()
    if lock_handle is None:
        return apiError(409, "a refresh task is already running")
    Thread(target=_runFetchAndCheck, args=(lock_handle,), daemon=True).start()
    return apiOk(message="fetch and check started", status=202)


@webui_bp.route('/logs/')
def logs():
    """日志文件列表(含大小, 供前端默认选中非空日志) / 指定日志尾部内容"""
    name = request.args.get("name", "").strip()
    if not name:
        files = []
        for log_name in listLogFiles():
            try:
                size = os.path.getsize(
                    os.path.join(_logHandler.LOG_PATH, log_name + '.log'))
            except OSError:
                size = 0
            files.append({"name": log_name, "size": size})
        return apiOk({"logs": files})
    if not _LOG_NAME_PATTERN.match(name):
        return apiError(400, "invalid log name")
    lines = request.args.get("lines", default=200, type=int)
    lines = max(1, min(lines, 1000))
    ok, content = readRecentLines(name, lines)
    if not ok:
        return apiError(404, "log file not found")
    return apiOk({"name": name, "lines": content})
