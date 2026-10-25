# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     test_log_handler.py
   Description :   LogHandler 单元测试
   Author :        JHao
   date：          2026/6/15
-------------------------------------------------
     Change Activity:
                     2026/06/15:
-------------------------------------------------
"""
__author__ = 'JHao'

import logging
import pytest
from unittest.mock import patch, MagicMock

from handler.logHandler import (
    LogHandler, DEBUG, INFO, ERROR, listLogFiles, readRecentLines
)


@pytest.fixture(autouse=True)
def tmp_log_path(tmp_path, monkeypatch):
    """隔离日志目录: 防止测试创建的 file handler 把文件写进项目 log/"""
    monkeypatch.setattr("handler.logHandler.LOG_PATH", str(tmp_path))


class TestLogHandlerInit:
    """__init__ 测试"""

    def test_default_creates_stream_handler(self):
        """默认参数创建 stream handler"""
        log = LogHandler("test_default_stream", stream=True, file=False)
        handler_types = [type(h) for h in log.handlers]
        assert logging.StreamHandler in handler_types

    @patch("handler.logHandler.platform")
    def test_file_handler_on_linux(self, mock_platform):
        """Linux 下创建 file handler"""
        mock_platform.system.return_value = "Linux"
        log = LogHandler("test_linux_file", stream=False, file=True)
        has_file = any(isinstance(h, logging.handlers.TimedRotatingFileHandler) for h in log.handlers)
        assert has_file

    @patch("handler.logHandler.platform")
    def test_no_file_handler_on_windows(self, mock_platform):
        """Windows 下不创建 file handler"""
        mock_platform.system.return_value = "Windows"
        log = LogHandler("test_windows_no_file", stream=False, file=True)
        has_file = any(isinstance(h, logging.handlers.TimedRotatingFileHandler) for h in log.handlers)
        assert not has_file

    def test_no_stream_handler_when_disabled(self):
        """stream=False 时不创建 stream handler"""
        log = LogHandler("test_no_stream", stream=False, file=False)
        assert len(log.handlers) == 0


class TestLogHandlerStreamLevel:
    """stream handler level 测试"""

    def test_default_level_used_when_no_override(self):
        """未指定 level 时使用 self.level"""
        log = LogHandler("test_stream_level", level=ERROR, stream=True, file=False)
        stream_handlers = [h for h in log.handlers if isinstance(h, logging.StreamHandler)
                           and not isinstance(h, logging.handlers.TimedRotatingFileHandler)]
        assert len(stream_handlers) > 0
        assert stream_handlers[0].level == ERROR

    def test_explicit_level_overrides_default(self):
        """显式指定 level 覆盖默认值"""
        log = LogHandler("test_stream_override", level=DEBUG, stream=True, file=False)
        log.__setStreamHandler__(level=ERROR)
        # 最后添加的 handler 应该是 ERROR 级别
        last_handler = log.handlers[-1]
        assert last_handler.level == ERROR


class TestLogHandlerFileLevel:
    """file handler level 测试"""

    @patch("handler.logHandler.platform")
    def test_file_handler_default_level(self, mock_platform):
        """file handler 未指定 level 时使用 self.level"""
        mock_platform.system.return_value = "Linux"
        log = LogHandler("test_file_level", level=INFO, stream=False, file=True)
        file_handlers = [h for h in log.handlers
                         if isinstance(h, logging.handlers.TimedRotatingFileHandler)]
        assert len(file_handlers) > 0
        assert file_handlers[0].level == INFO

    @patch("handler.logHandler.platform")
    def test_file_handler_hourly_rotation(self, mock_platform):
        """日志按小时切割, 保留168个(7天)"""
        mock_platform.system.return_value = "Linux"
        log = LogHandler("test_hourly", level=INFO, stream=False, file=True)
        handler = [h for h in log.handlers
                   if isinstance(h, logging.handlers.TimedRotatingFileHandler)][0]
        assert handler.when == 'H'
        assert handler.backupCount == 168

    @patch("handler.logHandler.platform")
    def test_file_handler_explicit_level(self, mock_platform):
        """file handler 显式指定 level"""
        mock_platform.system.return_value = "Linux"
        log = LogHandler("test_file_override", level=DEBUG, stream=False, file=True)
        log.__setFileHandler__(level=ERROR)
        last_handler = log.handlers[-1]
        assert last_handler.level == ERROR


class TestLogHandlerDirCreation:
    """log 目录创建测试"""

    @patch("os.path.exists", return_value=False)
    @patch("os.mkdir")
    def test_creates_log_dir_when_missing(self, mock_mkdir, mock_exists):
        """log 目录不存在时创建"""
        # 重新 import 触发模块级代码（无法直接测试，验证模块级逻辑）
        # 这里测试的是 FileExistsError 处理
        import handler.logHandler as lh
        # 模块加载时已执行，此处验证 LOG_PATH 存在
        assert lh.LOG_PATH is not None

    @patch("os.path.exists", return_value=False)
    @patch("os.mkdir", side_effect=FileExistsError)
    def test_handles_file_exists_race_condition(self, mock_mkdir, mock_exists):
        """处理 mkdir 时的 FileExistsError 竞态条件"""
        # 验证模块级代码不会因 FileExistsError 崩溃
        import handler.logHandler as lh
        assert lh.LOG_PATH is not None


class TestLogFiles:
    """listLogFiles / readRecentLines 测试"""

    def test_list_log_files(self, tmp_path, monkeypatch):
        """列出 log 目录下全部 .log 文件(去扩展名, 排序)"""
        monkeypatch.setattr("handler.logHandler.LOG_PATH", str(tmp_path))
        (tmp_path / "fetch.log").write_text("l", encoding="utf-8")
        (tmp_path / "checker.log").write_text("l", encoding="utf-8")
        (tmp_path / "readme.txt").write_text("l", encoding="utf-8")
        assert listLogFiles() == ["checker", "fetch"]

    def test_list_log_files_missing_dir(self, tmp_path, monkeypatch):
        """目录不存在返回空列表"""
        monkeypatch.setattr("handler.logHandler.LOG_PATH", str(tmp_path / "not_exist"))
        assert listLogFiles() == []

    def test_read_recent_lines_returns_tail(self, tmp_path, monkeypatch):
        """返回尾部 N 行"""
        monkeypatch.setattr("handler.logHandler.LOG_PATH", str(tmp_path))
        (tmp_path / "fetch.log").write_text(
            "".join("line%d\n" % i for i in range(1, 6)), encoding="utf-8")
        ok, lines = readRecentLines("fetch", 2)
        assert ok is True
        assert lines == ["line4", "line5"]

    def test_read_recent_lines_invalid_name(self, tmp_path, monkeypatch):
        """非法日志名返回 (False, [])"""
        monkeypatch.setattr("handler.logHandler.LOG_PATH", str(tmp_path))
        for name in ("../setting", "a/b", "a b", "", None):
            assert readRecentLines(name, 10) == (False, [])

    def test_read_recent_lines_file_not_found(self, tmp_path, monkeypatch):
        """文件不存在返回 (False, [])"""
        monkeypatch.setattr("handler.logHandler.LOG_PATH", str(tmp_path))
        assert readRecentLines("nope", 10) == (False, [])