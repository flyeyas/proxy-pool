# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     fetcherConfig.py
   Description :   代理源运行时配置(WebUI后台开关), 存于 runtime_config.json
   Author :        flyeyas
   date：          2026/10/25
-------------------------------------------------
   Change Activity:
                   2026/10/25: 运行时黑名单, 支持在 WebUI 后台启停代理源
                   2026/10/25: 读写迁移至 helper/runtimeConfig.py
-------------------------------------------------
"""
__author__ = 'flyeyas'

from helper.runtimeConfig import readConfig, writeConfig


def getRuntimeExclude():
    """运行时禁用的 fetcher 类名列表"""
    return list(readConfig().get('fetcher_exclude', []))


def setRuntimeExclude(class_names):
    config = readConfig()
    config['fetcher_exclude'] = sorted(set(class_names))
    writeConfig(config)


def toggleRuntimeExclude(class_name, enable):
    """
    开关指定代理源
    :param class_name: fetcher 类名(与 setting.PROXY_FETCHER_EXCLUDE 的匹配口径一致)
    :param enable: True 启用(移出黑名单) / False 禁用(加入黑名单)
    :return: list 最新黑名单
    """
    config = readConfig()
    exclude = set(config.get('fetcher_exclude', []))
    if enable:
        exclude.discard(class_name)
    else:
        exclude.add(class_name)
    config['fetcher_exclude'] = sorted(exclude)
    writeConfig(config)
    return sorted(exclude)
