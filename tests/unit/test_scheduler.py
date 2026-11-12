# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     test_scheduler.py
   Description :   helper/scheduler.py 单元测试
   Author :        JHao
   date：          2026/6/15
-------------------------------------------------
     Change Activity:
                     2026/06/15:
                     2026/11/11: 验证启动清理与每小时整点日志清理任务
-------------------------------------------------
"""
__author__ = 'JHao'

import logging
import pytest
from unittest.mock import patch, MagicMock
from apscheduler.schedulers.blocking import BlockingScheduler

import helper.scheduler as scheduler_mod
from helper.schedulerStatus import SchedulerMonitor


@pytest.fixture(autouse=True)
def mock_log_cleanup(monkeypatch):
    cleanup = MagicMock()
    monkeypatch.setattr(scheduler_mod, "cleanupExpiredLogs", cleanup)
    return cleanup


@pytest.fixture(autouse=True)
def mock_status_monitor(monkeypatch):
    monitor = MagicMock()
    monitor.events = SchedulerMonitor.events
    monitor.wrap.side_effect = lambda job_id, function: function
    monitor.runJob.side_effect = lambda job_id, function, **kwargs: function()
    monkeypatch.setattr(scheduler_mod, 'SchedulerMonitor', lambda *args: monitor)
    return monitor


def _get_attr(name):
    """获取模块中双下划线开头的属性（绕过类内 name mangling）"""
    return getattr(scheduler_mod, name)


class TestRunProxyFetch:

    @patch("helper.scheduler.Checker")
    @patch("helper.scheduler.Fetcher")
    def test_fetcher_yields_go_to_queue(self, mock_fetcher_cls, mock_checker):
        """Fetcher yield 的代理放入 queue，传给 Checker"""
        mock_proxy = MagicMock()
        mock_fetcher = MagicMock()
        mock_fetcher.run.return_value = iter([mock_proxy])
        mock_fetcher_cls.return_value = mock_fetcher

        _get_attr("__runProxyFetch")()

        mock_fetcher_cls.assert_called_once()
        mock_checker.assert_called_once()
        call_args = mock_checker.call_args
        assert call_args[0][0] == "raw"


class TestRunProxyCheck:

    @patch("helper.scheduler.__runProxyFetch")
    @patch("helper.scheduler.Checker")
    @patch("helper.scheduler.ProxyHandler")
    def test_triggers_fetch_when_pool_low(self, mock_ph_cls, mock_checker, mock_fetch):
        """count < poolSizeMin -> 触发 __runProxyFetch"""
        mock_ph = MagicMock()
        mock_ph.db.getCount.return_value = {"total": 5}
        mock_ph.conf.poolSizeMin = 20
        mock_ph.getAll.return_value = []
        mock_ph_cls.return_value = mock_ph

        _get_attr("__runProxyCheck")()

        mock_fetch.assert_called_once()

    @patch("helper.scheduler.__runProxyFetch")
    @patch("helper.scheduler.Checker")
    @patch("helper.scheduler.ProxyHandler")
    def test_skips_fetch_when_pool_sufficient(self, mock_ph_cls, mock_checker, mock_fetch):
        """count >= poolSizeMin -> 不触发 __runProxyFetch"""
        mock_ph = MagicMock()
        mock_ph.db.getCount.return_value = {"total": 50}
        mock_ph.conf.poolSizeMin = 20
        mock_ph.getAll.return_value = []
        mock_ph_cls.return_value = mock_ph

        _get_attr("__runProxyCheck")()

        mock_fetch.assert_not_called()


class TestRunScheduler:

    def test_keeps_logger_when_configuring_scheduler(self, monkeypatch):
        observed = []
        logger = logging.Logger("scheduler_test")

        class RecordingScheduler(BlockingScheduler):
            def start(self):
                observed.append(self)

        monkeypatch.setattr(scheduler_mod, "BlockingScheduler", RecordingScheduler)
        monkeypatch.setattr(scheduler_mod, "LogHandler", lambda name: logger)
        monkeypatch.setattr(scheduler_mod, "ConfigHandler",
                            lambda: MagicMock(timezone="Asia/Shanghai"))
        monkeypatch.setattr(scheduler_mod, "__runProxyFetch", lambda: None)
        scheduler_mod.runScheduler()
        scheduler = observed[0]
        assert scheduler._logger is logger
        assert {job.id for job in scheduler.get_jobs()} == {
            "proxy_fetch", "proxy_check", "log_cleanup"
        }

    @patch("helper.scheduler.BlockingScheduler")
    @patch("helper.scheduler.__runProxyFetch")
    @patch("helper.scheduler.ConfigHandler")
    @patch("helper.scheduler.LogHandler")
    def test_adds_three_jobs(self, mock_log, mock_conf_cls, mock_fetch, mock_sched_cls):
        """runScheduler 添加采集, 检查与日志清理任务"""
        mock_conf = MagicMock()
        mock_conf.timezone = "Asia/Shanghai"
        mock_conf_cls.return_value = mock_conf
        mock_sched = MagicMock()
        mock_sched_cls.return_value = mock_sched

        scheduler_mod.runScheduler()

        assert mock_sched.add_job.call_count == 3

    @patch("helper.scheduler.BlockingScheduler")
    @patch("helper.scheduler.__runProxyFetch")
    @patch("helper.scheduler.ConfigHandler")
    @patch("helper.scheduler.LogHandler")
    def test_log_cleanup_at_startup_and_top_of_hour(self, mock_log, mock_conf_cls,
                                                   mock_fetch, mock_sched_cls,
                                                   mock_log_cleanup):
        mock_conf_cls.return_value.timezone = "Asia/Shanghai"
        scheduler_mod.runScheduler()
        mock_log_cleanup.assert_called_once_with()
        job = mock_sched_cls.return_value.add_job.call_args_list[2]
        assert job.args == (mock_log_cleanup, "cron")
        assert job.kwargs["minute"] == 0
        assert job.kwargs["max_instances"] == 1
        assert job.kwargs["coalesce"] is True

    @patch("helper.scheduler.BlockingScheduler")
    @patch("helper.scheduler.__runProxyFetch")
    @patch("helper.scheduler.ConfigHandler")
    @patch("helper.scheduler.LogHandler")
    def test_fetch_job_interval_5min(self, mock_log, mock_conf_cls, mock_fetch, mock_sched_cls):
        """采集任务间隔 5 分钟"""
        mock_conf = MagicMock()
        mock_conf.timezone = "Asia/Shanghai"
        mock_conf_cls.return_value = mock_conf
        mock_sched = MagicMock()
        mock_sched_cls.return_value = mock_sched

        scheduler_mod.runScheduler()

        calls = mock_sched.add_job.call_args_list
        first_call = calls[0]
        assert first_call[0][1] == "interval"
        assert first_call[1]["minutes"] == 5

    @patch("helper.scheduler.BlockingScheduler")
    @patch("helper.scheduler.__runProxyFetch")
    @patch("helper.scheduler.ConfigHandler")
    @patch("helper.scheduler.LogHandler")
    def test_check_job_interval_2min(self, mock_log, mock_conf_cls, mock_fetch, mock_sched_cls):
        """检查任务间隔 2 分钟"""
        mock_conf = MagicMock()
        mock_conf.timezone = "Asia/Shanghai"
        mock_conf_cls.return_value = mock_conf
        mock_sched = MagicMock()
        mock_sched_cls.return_value = mock_sched

        scheduler_mod.runScheduler()

        calls = mock_sched.add_job.call_args_list
        second_call = calls[1]
        assert second_call[0][1] == "interval"
        assert second_call[1]["minutes"] == 2
