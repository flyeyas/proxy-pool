# -*- coding: utf-8 -*-
# !/usr/bin/env python
"""
-------------------------------------------------
   File Name：     ProxyApi.py
   Description :   WebApi
   Author :       JHao
   date：          2016/12/4
-------------------------------------------------
   Change Activity:
                   2016/12/04: WebApi
                   2019/08/14: 集成Gunicorn启动方式
                   2020/06/23: 新增pop接口
                   2022/07/21: 更新count接口
                   2026/10/24: 注册WebUI Blueprint
-------------------------------------------------
"""
__author__ = 'JHao'

import os
import hashlib
import platform
from werkzeug.wrappers import Response
from flask import Flask, jsonify, request

from util.six import iteritems
from helper.proxy import Proxy
from handler.proxyHandler import ProxyHandler
from handler.configHandler import ConfigHandler
from api.response import apiOk, apiError
from api.webuiApi import webui_bp

app = Flask(__name__)
conf = ConfigHandler()
proxy_handler = ProxyHandler()


class JsonResponse(Response):
    @classmethod
    def force_type(cls, response, environ=None):
        if isinstance(response, (dict, list)):
            response = jsonify(response)

        return super(JsonResponse, cls).force_type(response, environ)


app.response_class = JsonResponse  # type: ignore # Flask 官方支持的响应类替换方式, 类型桩未覆盖
app.register_blueprint(webui_bp)

# WebUI 登录会话签名密钥: 优先环境变量; 未设置时由账号密码派生,
# 保证 gunicorn 多 worker 下各进程密钥一致(登录态不丢失)
app.secret_key = os.getenv('WEBUI_SECRET_KEY') or hashlib.sha256(
    ('proxy_pool_webui:%s:%s' % (conf.webuiUsername, conf.webuiPassword)).encode()).hexdigest()

api_list = [
    {"url": "/", "method": "GET", "desc": "api list"},
    {"url": "/get", "method": "GET", "params": "type: 'https'|''", "desc": "get a random proxy"},
    {"url": "/pop", "method": "GET", "params": "type: 'https'|''", "desc": "get and remove a proxy"},
    {"url": "/all", "method": "GET", "params": "type: 'https'|''", "desc": "get all proxies"},
    {"url": "/count", "method": "GET", "desc": "proxy count stats"},
    {"url": "/delete", "method": "DELETE", "params": "proxy: 'e.g. 127.0.0.1:8080'", "desc": "delete a proxy"},
    {"url": "/fetchers", "method": "GET", "desc": "list all proxy fetchers (for WebUI)"},
    {"url": "/fetchers/<class_name>", "method": "PATCH", "desc": "enable/disable a fetcher (for WebUI)"},
    {"url": "/refresh", "method": "POST", "desc": "trigger a fetch and check run (for WebUI)"},
    {"url": "/logs", "method": "GET", "desc": "list log files / read recent log lines (for WebUI)"},
    {"url": "/webui", "method": "GET", "desc": "WebUI dashboard"},
]


@app.route('/')
def index():
    return apiOk({"apis": api_list})


@app.route('/get/')
def get():
    https = request.args.get("type", "").lower() == 'https'
    proxy = proxy_handler.get(https)
    return apiOk(proxy.to_dict) if proxy else apiError(404, "no proxy available")


@app.route('/pop/')
def pop():
    https = request.args.get("type", "").lower() == 'https'
    proxy = proxy_handler.pop(https)
    return apiOk(proxy.to_dict) if proxy else apiError(404, "no proxy available")


@app.route('/all/')
def getAll():
    https = request.args.get("type", "").lower() == 'https'
    proxies = proxy_handler.getAll(https)
    return apiOk({"count": len(proxies), "proxies": [_.to_dict for _ in proxies]})


@app.route('/delete/', methods=['DELETE', 'GET'])
def delete():
    proxy = request.args.get('proxy')
    if not proxy:
        return apiError(400, "proxy parameter is required")
    deleted = proxy_handler.delete(Proxy(proxy))
    if deleted:
        return apiOk({"proxy": proxy, "deleted": True})
    return apiError(404, "proxy not found in pool")


@app.route('/count/')
def getCount():
    proxies = proxy_handler.getAll()
    http_type_dict = {}
    source_dict = {}
    for proxy in proxies:
        http_type = 'https' if proxy.https else 'http'
        http_type_dict[http_type] = http_type_dict.get(http_type, 0) + 1
        for source in proxy.source.split('/'):
            source_dict[source] = source_dict.get(source, 0) + 1
    return apiOk({"http_type": http_type_dict, "source": source_dict, "count": len(proxies)})


def runFlask():
    if platform.system() == "Windows":
        app.run(host=conf.serverHost, port=conf.serverPort)
    else:
        import gunicorn.app.base

        class StandaloneApplication(gunicorn.app.base.BaseApplication):

            def __init__(self, app, options=None):
                self.options = options or {}
                self.application = app
                super(StandaloneApplication, self).__init__()

            def load_config(self):
                _config = dict([(key, value) for key, value in iteritems(self.options)
                                if key in self.cfg.settings and value is not None])
                for key, value in iteritems(_config):
                    self.cfg.set(key.lower(), value)

            def load(self):
                return self.application

        _options = {
            'bind': '%s:%s' % (conf.serverHost, conf.serverPort),
            'workers': 4,
            'accesslog': '-',  # log to stdout
            'access_log_format': '%(h)s %(l)s %(t)s "%(r)s" %(s)s "%(a)s"'
        }
        StandaloneApplication(app, _options).run()


if __name__ == '__main__':
    runFlask()
