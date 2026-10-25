# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     fetcherConfig.py
   Description :   代理源运行时配置: 手动/自动禁用黑名单 + 抓取结果统计, 存于 runtime_config.json
   Author :        flyeyas
   date：          2026/10/25
-------------------------------------------------
   Change Activity:
                   2026/10/25: 运行时黑名单, 支持在 WebUI 后台启停代理源
                   2026/10/25: 读写迁移至 helper/runtimeConfig.py
                   2026/10/25: 抓取结果统计与连续失败自动禁用
-------------------------------------------------
"""
__author__ = 'flyeyas'

import threading
from datetime import datetime

from helper.runtimeConfig import readConfig, writeConfig

# 同进程内统计读写的互斥(抓取线程并发上报)
_LOCK = threading.Lock()

_AUTO_DISABLE_KEY = 'fetcher_auto_disable'
_STATS_KEY = 'fetcher_stats'
_TIME_FORMAT = '%Y-%m-%d %H:%M:%S'


def getRuntimeExclude():
    """手动禁用的 fetcher 类名列表(WebUI 后台开关写入)"""
    return list(readConfig().get('fetcher_exclude', []))


def setRuntimeExclude(class_names):
    config = readConfig()
    config['fetcher_exclude'] = sorted(set(class_names))
    writeConfig(config)


def toggleRuntimeExclude(class_name, enable):
    """
    手动开关指定代理源
    :param class_name: fetcher 类名(与 setting.PROXY_FETCHER_EXCLUDE 的匹配口径一致)
    :param enable: True 启用(移出黑名单) / False 禁用(加入黑名单)
    :return: list 最新手动黑名单
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


def getAutoDisable():
    """连续失败自动禁用的 fetcher 类名列表"""
    return list(readConfig().get(_AUTO_DISABLE_KEY, []))


def getEffectiveExclude():
    """运行时生效黑名单 = 手动禁用 ∪ 自动禁用(setting.py 静态黑名单由调用方合并)"""
    config = readConfig()
    return list(set(config.get('fetcher_exclude', [])) | set(config.get(_AUTO_DISABLE_KEY, [])))


def getFetcherStats():
    """全部代理源抓取统计 {class_name: {...}}"""
    return readConfig().get(_STATS_KEY, {})


def recordFetchResult(class_name, ok, error='', auto_disable_fails=5):
    """
    记录一次抓取结果; 连续失败达到阈值自动加入禁用名单(需人工启用恢复)
    :param class_name: fetcher 类名
    :param ok: 本轮是否成功(未抛异常且有代理产出)
    :param error: 失败时的错误摘要
    :param auto_disable_fails: 连续失败自动禁用阈值, <=0 表示关闭
    :return: dict 该源最新统计
    """
    now = datetime.now().strftime(_TIME_FORMAT)
    with _LOCK:
        config = readConfig()
        stats_all = config.get(_STATS_KEY, {})
        stat = stats_all.get(class_name, {})
        stat['total_ok'] = stat.get('total_ok', 0) + (1 if ok else 0)
        stat['total_fails'] = stat.get('total_fails', 0) + (0 if ok else 1)
        if ok:
            stat['fails'] = 0
            stat['last_success'] = now
        else:
            stat['fails'] = stat.get('fails', 0) + 1
            stat['last_fail'] = now
            if error:
                stat['last_error'] = str(error)[:200]
        if auto_disable_fails and stat.get('fails', 0) >= auto_disable_fails:
            auto = set(config.get(_AUTO_DISABLE_KEY, []))
            if class_name not in auto:
                auto.add(class_name)
                stat['disabled_at'] = now
                stat['disable_reason'] = '连续失败 %d 次: %s' % (
                    stat['fails'], stat.get('last_error', '无代理产出'))
                config[_AUTO_DISABLE_KEY] = sorted(auto)
        stats_all[class_name] = stat
        config[_STATS_KEY] = stats_all
        writeConfig(config)
    return stat


def enableFetcher(class_name):
    """
    启用指定代理源: 移出手动与自动禁用名单, 并重置统计重新观察
    :return: (手动黑名单, 自动禁用名单)
    """
    with _LOCK:
        config = readConfig()
        exclude = set(config.get('fetcher_exclude', []))
        auto = set(config.get(_AUTO_DISABLE_KEY, []))
        exclude.discard(class_name)
        auto.discard(class_name)
        config['fetcher_exclude'] = sorted(exclude)
        config[_AUTO_DISABLE_KEY] = sorted(auto)
        stats_all = config.get(_STATS_KEY, {})
        stats_all.pop(class_name, None)
        config[_STATS_KEY] = stats_all
        writeConfig(config)
    return sorted(exclude), sorted(auto)
