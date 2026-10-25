# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     fetcherConfig.py
   Description :   代理源运行时配置(WebUI后台开关), 持久化到项目根目录 runtime_config.json
   Author :        flyeyas
   date：          2026/10/25
-------------------------------------------------
   Change Activity:
                   2026/10/25: 运行时黑名单, 支持在 WebUI 后台启停代理源
-------------------------------------------------
"""
__author__ = 'flyeyas'

import os
import json

ROOT_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 运行时配置文件(schedule 与 server 进程通过同一文件共享, 同机部署天然生效)
CONFIG_FILE = os.path.join(ROOT_PATH, 'runtime_config.json')


def _readConfig():
    """读取运行时配置, 文件不存在或损坏时返回空配置"""
    if not os.path.isfile(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
            return config if isinstance(config, dict) else {}
    except (ValueError, OSError):
        return {}


def _writeConfig(config):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def getRuntimeExclude():
    """运行时禁用的 fetcher 类名列表"""
    return list(_readConfig().get('fetcher_exclude', []))


def setRuntimeExclude(class_names):
    _writeConfig({'fetcher_exclude': sorted(set(class_names))})


def toggleRuntimeExclude(class_name, enable):
    """
    开关指定代理源
    :param class_name: fetcher 类名(与 setting.PROXY_FETCHER_EXCLUDE 的匹配口径一致)
    :param enable: True 启用(移出黑名单) / False 禁用(加入黑名单)
    :return: list 最新黑名单
    """
    exclude = set(getRuntimeExclude())
    if enable:
        exclude.discard(class_name)
    else:
        exclude.add(class_name)
    setRuntimeExclude(exclude)
    return sorted(exclude)
