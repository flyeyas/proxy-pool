# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     configHandler
   Description :
   Author :        JHao
   date：          2020/6/22
-------------------------------------------------
   Change Activity:
                   2020/6/22:
                   2026/10/25: 接入 .env 配置文件加载
-------------------------------------------------
"""
__author__ = 'JHao'

import os
import setting
from util.singleton import Singleton
from util.lazyProperty import LazyProperty
from util.six import reload_six, withMetaclass
from util.envHandler import loadAllEnvVars

# 启动时加载环境配置文件, 优先级: 系统环境变量 > .env.local(本地) > .env(线上) > setting.py
# 见 util/envHandler.py
LOADED_ENV_FILES = loadAllEnvVars()


class ConfigHandler(withMetaclass(Singleton)):

    def __init__(self):
        pass

    @LazyProperty
    def serverHost(self):
        return os.environ.get("HOST", setting.HOST)

    @LazyProperty
    def serverPort(self):
        return os.environ.get("PORT", setting.PORT)

    @LazyProperty
    def dbConn(self):
        return os.getenv("DB_CONN", setting.DB_CONN)

    @LazyProperty
    def tableName(self):
        return os.getenv("TABLE_NAME", setting.TABLE_NAME)

    @property
    def fetcherExclude(self):
        reload_six(setting)
        return getattr(setting, 'PROXY_FETCHER_EXCLUDE', [])

    @LazyProperty
    def fetcherAutoDisableFails(self):
        return int(os.getenv("FETCHER_AUTO_DISABLE_FAILS", setting.FETCHER_AUTO_DISABLE_FAILS))

    @LazyProperty
    def httpUrl(self):
        return os.getenv("HTTP_URL", setting.HTTP_URL)

    @LazyProperty
    def httpsUrl(self):
        return os.getenv("HTTPS_URL", setting.HTTPS_URL)

    @LazyProperty
    def verifyTimeout(self):
        return int(os.getenv("VERIFY_TIMEOUT", setting.VERIFY_TIMEOUT))

    # @LazyProperty
    # def proxyCheckCount(self):
    #     return int(os.getenv("PROXY_CHECK_COUNT", setting.PROXY_CHECK_COUNT))

    @LazyProperty
    def maxFailCount(self):
        return int(os.getenv("MAX_FAIL_COUNT", setting.MAX_FAIL_COUNT))

    # @LazyProperty
    # def maxFailRate(self):
    #     return int(os.getenv("MAX_FAIL_RATE", setting.MAX_FAIL_RATE))

    @LazyProperty
    def poolSizeMin(self):
        return int(os.getenv("POOL_SIZE_MIN", setting.POOL_SIZE_MIN))

    @LazyProperty
    def proxyRegion(self):
        return bool(os.getenv("PROXY_REGION", setting.PROXY_REGION))

    @LazyProperty
    def timezone(self):
        return os.getenv("TIMEZONE", setting.TIMEZONE)

    @LazyProperty
    def webuiUsername(self):
        return os.environ.get("WEBUI_USERNAME", "admin")

    @LazyProperty
    def webuiPassword(self):
        # 为空时 WebUI 登录验证关闭
        return os.environ.get("WEBUI_PASSWORD", "")

