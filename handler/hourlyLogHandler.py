# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     hourlyLogHandler.py
   Description :   多进程安全的整点日志切割与七天归档清理
   Author :        JHao
   date：          2026/11/11
-------------------------------------------------
   Change Activity:
                   2026/11/11: 按整点切割, 按时间清理, 支持进程间互斥
-------------------------------------------------
"""
__author__ = 'JHao'

import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from logging.handlers import WatchedFileHandler

try:
    import fcntl
except ImportError:  # Windows 的 LogHandler 不启用文件日志
    fcntl = None

HOUR_SECONDS = 3600
RETENTION_SECONDS = 7 * 24 * HOUR_SECONDS
STAMP_FORMAT = "%Y%m%d%H"
_ARCHIVE_PATTERN = re.compile(r"^(?P<base>.+\.log)\.(?P<stamp>\d{10})(?:\.\d+)?$")
_cleanup_lock = threading.Lock()
_last_cleanup = {}


def _reset_after_fork():
    global _cleanup_lock
    _cleanup_lock = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_after_fork)


def _now():
    return time.time()


def _stamp(timestamp):
    return time.strftime(STAMP_FORMAT, time.localtime(timestamp))


def _stamp_time(stamp):
    if not re.fullmatch(r"\d{10}", stamp):
        raise ValueError("invalid hourly log timestamp")
    return datetime.strptime(stamp, STAMP_FORMAT).timestamp()


@contextmanager
def _file_lock(filename, stream=None):
    if fcntl is None:
        raise OSError("Hourly file logging requires POSIX file locks")
    owned = stream is None
    if owned:
        stream = open(filename + ".lock", "a+", encoding="ascii")
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield stream
    finally:
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        if owned:
            stream.close()


def _rollover(filename, marker, now):
    current = _stamp(now)
    marker.seek(0)
    stored = marker.read().strip()
    previous = stored
    try:
        _stamp_time(previous)
    except ValueError:
        try:
            previous = _stamp(os.stat(filename).st_mtime)
        except FileNotFoundError:
            previous = current

    if previous != current and os.path.isfile(filename):
        if os.path.getsize(filename):
            archive = filename + "." + previous
            # 旧版归档或时钟回拨造成重名时, 保留两份文件, 不覆盖历史。
            destination = archive
            index = 1
            while os.path.exists(destination):
                destination = archive + "." + str(index)
                index += 1
            os.rename(filename, destination)
        else:
            os.remove(filename)
        # 保留稳定的当前日志路径, 空闲日志也能被 WebUI 列出。
        with open(filename, "a", encoding="utf-8"):
            pass

    if stored != current:
        marker.seek(0)
        marker.truncate()
        marker.write(current)
        marker.flush()


def cleanupExpiredLogs(log_path, now=None):
    """切割空闲的当前日志, 删除早于最近七天小时边界的归档。"""
    if not os.path.isdir(log_path) or fcntl is None:
        return []
    now = _now() if now is None else now
    cutoff = _stamp_time(_stamp(now)) - RETENTION_SECONDS
    families = set()
    with os.scandir(log_path) as entries:
        for entry in entries:
            if not entry.is_file(follow_symlinks=False):
                continue
            match = _ARCHIVE_PATTERN.fullmatch(entry.name)
            if match:
                families.add(match["base"])
            elif entry.name.endswith(".log"):
                families.add(entry.name)

    removed = []
    for base in sorted(families):
        filename = os.path.join(log_path, base)
        with _file_lock(filename) as marker:
            if os.path.isfile(filename) and not os.path.islink(filename):
                _rollover(filename, marker, now)
            # 包含刚从空闲日志切出的归档。
            prefix = base + "."
            for name in os.listdir(log_path):
                if not name.startswith(prefix):
                    continue
                match = _ARCHIVE_PATTERN.fullmatch(name)
                if not match:
                    continue
                try:
                    expired = _stamp_time(match["stamp"]) < cutoff
                except ValueError:
                    continue
                path = os.path.join(log_path, name)
                if expired and os.path.isfile(path) and not os.path.islink(path):
                    try:
                        os.remove(path)
                        removed.append(path)
                    except FileNotFoundError:
                        pass
    return removed


def _cleanup_if_due(log_path, now):
    hour = _stamp(now)
    with _cleanup_lock:
        if _last_cleanup.get(log_path) == hour:
            return
        _last_cleanup[log_path] = hour
    try:
        cleanupExpiredLogs(log_path, now)
    except OSError:
        with _cleanup_lock:
            _last_cleanup.pop(log_path, None)
        raise


class HourlyRotatingFileHandler(WatchedFileHandler):
    """用文件锁共享小时标记; 切割后自动重开被其他进程更换的文件。"""

    def __init__(self, filename):
        filename = os.path.abspath(filename)
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        self._lock_stream = None
        self._lock_pid = None
        now = _now()
        with _file_lock(filename) as marker:
            _rollover(filename, marker, now)
            super().__init__(filename, mode="a", encoding="utf-8")
        _cleanup_if_due(os.path.dirname(filename), now)

    def _get_lock_stream(self):
        pid = os.getpid()
        # gunicorn fork 后不能复用父进程的 flock 文件描述符。
        if self._lock_pid != pid or self._lock_stream is None or self._lock_stream.closed:
            if self._lock_stream is not None:
                self._lock_stream.close()
            self._lock_stream = open(self.baseFilename + ".lock", "a+", encoding="ascii")
            self._lock_pid = pid
        return self._lock_stream

    def emit(self, record):
        try:
            with _file_lock(self.baseFilename, self._get_lock_stream()) as marker:
                now = _now()
                _rollover(self.baseFilename, marker, now)
                super().emit(record)
            _cleanup_if_due(os.path.dirname(self.baseFilename), now)
        except Exception:
            self.handleError(record)

    def close(self):
        self.acquire()
        try:
            super().close()
            if self._lock_stream is not None:
                self._lock_stream.close()
                self._lock_stream = None
        finally:
            self.release()
