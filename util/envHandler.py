# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     envHandler.py
   Description :   .env 环境配置加载, 支持线上/本地环境区分
   Author :        flyeyas
   date：          2026/10/25
-------------------------------------------------
   Change Activity:
                   2026/10/25: 支持 .env 配置文件
                   2026/10/25: 新增 .env.local 本地覆盖(线上/线下环境区分)
-------------------------------------------------
"""
__author__ = 'flyeyas'

import os

ROOT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir)

# 环境配置文件(按优先级从高到低):
#   .env.local  本地开发覆盖配置, 不入库(.gitignore), 线上服务器不应存在
#   .env        线上默认配置
ENV_FILES = ('.env.local', '.env')


def loadEnvVars(env_path=None):
    """
    读取单个 env 文件写入 os.environ, 已存在的环境变量优先, 不被覆盖
    格式: KEY=VALUE, 支持 # 注释行/行内注释、引号包裹的值、空行
    :param env_path: env 文件路径, 默认项目根目录 .env
    :return: dict 本次实际加载的键值对
    """
    path = env_path or os.path.join(ROOT_PATH, '.env')
    if not os.path.isfile(path):
        return {}
    loaded = {}
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            key = key.strip()
            value = value.strip()
            # 去掉成对的单/双引号
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if not key:
                continue
            if key not in os.environ:
                os.environ[key] = value
                loaded[key] = value
    return loaded


def loadAllEnvVars(root_path=None):
    """
    按优先级加载全部环境配置文件(先 .env.local 后 .env, 先加载的键不会被覆盖),
    配合"已存在的系统环境变量不被覆盖", 最终优先级:
        系统环境变量 > .env.local(本地) > .env(线上) > setting.py
    :param root_path: 配置文件所在目录, 默认项目根目录
    :return: list 实际加载了的文件名(按加载顺序)
    """
    root = root_path or ROOT_PATH
    loaded_files = []
    for name in ENV_FILES:
        path = os.path.join(root, name)
        if os.path.isfile(path):
            loadEnvVars(path)
            loaded_files.append(name)
    return loaded_files
