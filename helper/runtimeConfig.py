# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     runtimeConfig.py
   Description :   运行时配置通用读写(持久化到项目根目录 runtime_config.json)
   Author :        flyeyas
   date：          2026/10/25
-------------------------------------------------
   Change Activity:
                   2026/10/25: 从 fetcherConfig 抽出通用读写, 供多项运行时配置共用
-------------------------------------------------
"""
__author__ = 'flyeyas'

import os
import json

ROOT_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 运行时配置文件(schedule 与 server 进程通过同一文件共享, 同机部署天然生效)
CONFIG_FILE = os.path.join(ROOT_PATH, 'runtime_config.json')


def readConfig():
    """读取运行时配置, 文件不存在或损坏时返回空配置"""
    if not os.path.isfile(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
            return config if isinstance(config, dict) else {}
    except (ValueError, OSError):
        return {}


def writeConfig(config):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
