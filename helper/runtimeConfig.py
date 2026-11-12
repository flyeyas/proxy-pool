# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     runtimeConfig.py
   Description :   运行时配置通用读写(支持指定持久化文件路径)
   Author :        flyeyas
   date：          2026/10/25
-------------------------------------------------
   Change Activity:
                   2026/10/25: 从 fetcherConfig 抽出通用读写, 供多项运行时配置共用
                   2026/11/12: 文件锁保护跨进程配置事务, 保持绑定挂载文件的 inode
                   2026/11/12: 支持 Docker 数据卷中的运行时配置文件
-------------------------------------------------
"""
__author__ = 'flyeyas'

import os
import json
import threading
from contextlib import contextmanager, nullcontext

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

_WINDOWS_LOCK = threading.RLock()

ROOT_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 本地默认沿用项目根目录; Docker 通过环境变量指向持久化数据卷。
# schedule 与 server 进程必须使用同一个文件路径。
CONFIG_FILE = os.environ.get('RUNTIME_CONFIG_FILE') or os.path.join(ROOT_PATH, 'runtime_config.json')


@contextmanager
def _lockedFile(write=False):
    # POSIX 使用各次独立打开的文件描述符, 同时保护线程、fork 和多个 API worker。
    with (_WINDOWS_LOCK if fcntl is None else nullcontext()):
        if write:
            os.makedirs(os.path.dirname(os.path.abspath(CONFIG_FILE)), exist_ok=True)
            fd = os.open(CONFIG_FILE, os.O_CREAT | os.O_RDWR, 0o600)
            stream = os.fdopen(fd, 'r+', encoding='utf-8')
        else:
            stream = open(CONFIG_FILE, 'r', encoding='utf-8')
        with stream:
            if fcntl is not None:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX if write else fcntl.LOCK_SH)
            try:
                yield stream
            finally:
                if fcntl is not None:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _read(stream):
    try:
        stream.seek(0)
        config = json.load(stream)
        return config if isinstance(config, dict) else {}
    except ValueError:
        return {}


def _write(stream, config):
    text = json.dumps(config, ensure_ascii=False, indent=2)
    stream.seek(0)
    stream.write(text)
    stream.truncate()
    stream.flush()


def readConfig():
    """读取运行时配置, 文件不存在或损坏时返回空配置"""
    if not os.path.isfile(CONFIG_FILE):
        return {}
    try:
        with _lockedFile() as stream:
            return _read(stream)
    except (ValueError, OSError):
        return {}


def writeConfig(config):
    with _lockedFile(write=True) as stream:
        _write(stream, config)


def updateConfig(updater):
    """在同一独占锁内读取、修改和写入配置, 防止不同 worker 覆盖其他设置。"""
    with _lockedFile(write=True) as stream:
        config = _read(stream)
        result = updater(config)
        _write(stream, config)
        return result
