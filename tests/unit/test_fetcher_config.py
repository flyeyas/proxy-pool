# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     test_fetcher_config.py
   Description :   代理源运行时配置单元测试
   Author :        flyeyas
   date：          2026/10/25
-------------------------------------------------
   Change Activity:
                   2026/10/25:
-------------------------------------------------
"""
__author__ = 'flyeyas'

import json
import pytest

import helper.runtimeConfig as runtime_config_mod
from helper.fetcherConfig import (
    getRuntimeExclude, setRuntimeExclude, toggleRuntimeExclude,
    getAutoDisable, getFetcherStats, getEffectiveExclude,
    recordFetchResult, enableFetcher
)


@pytest.fixture(autouse=True)
def tmp_config_file(tmp_path, monkeypatch):
    """每个测试使用独立的临时配置文件"""
    path = tmp_path / "runtime_config.json"
    monkeypatch.setattr(runtime_config_mod, "CONFIG_FILE", str(path))
    return path


class TestRuntimeExclude:

    def test_missing_file_returns_empty(self):
        assert getRuntimeExclude() == []

    def test_corrupt_file_returns_empty(self, tmp_config_file):
        tmp_config_file.write_text("{broken json", encoding="utf-8")
        assert getRuntimeExclude() == []

    def test_toggle_disable_then_enable(self):
        exclude = toggleRuntimeExclude("KuaidailiFetcher", enable=False)
        assert exclude == ["KuaidailiFetcher"]
        assert getRuntimeExclude() == ["KuaidailiFetcher"]

        exclude = toggleRuntimeExclude("KuaidailiFetcher", enable=True)
        assert exclude == []
        assert getRuntimeExclude() == []

    def test_toggle_unknown_enable_is_noop(self):
        toggleRuntimeExclude("AFetcher", enable=False)
        exclude = toggleRuntimeExclude("BFetcher", enable=True)
        assert exclude == ["AFetcher"]

    def test_set_deduplicates_and_sorts(self, tmp_config_file):
        setRuntimeExclude(["BFetcher", "AFetcher", "BFetcher"])
        assert getRuntimeExclude() == ["AFetcher", "BFetcher"]
        data = json.loads(tmp_config_file.read_text(encoding="utf-8"))
        assert data == {"fetcher_exclude": ["AFetcher", "BFetcher"]}


class TestFetchStats:
    """抓取统计与连续失败自动禁用"""

    def test_record_success_and_fail(self):
        stat = recordFetchResult("AFetcher", ok=True)
        assert stat["total_ok"] == 1
        assert stat["fails"] == 0
        assert stat["last_success"]
        stat = recordFetchResult("AFetcher", ok=False, error="boom")
        assert stat["fails"] == 1
        assert stat["total_fails"] == 1
        assert stat["last_error"] == "boom"

    def test_consecutive_fail_auto_disable(self):
        for _ in range(3):
            recordFetchResult("BFetcher", ok=False, error="timeout", auto_disable_fails=3)
        assert getAutoDisable() == ["BFetcher"]
        stat = getFetcherStats()["BFetcher"]
        assert stat["fails"] == 3
        assert "连续失败 3 次" in stat["disable_reason"]
        assert stat["disabled_at"]

    def test_success_resets_consecutive(self):
        for _ in range(2):
            recordFetchResult("CFetcher", ok=False, auto_disable_fails=3)
        recordFetchResult("CFetcher", ok=True, auto_disable_fails=3)
        recordFetchResult("CFetcher", ok=False, auto_disable_fails=3)
        assert "CFetcher" not in getAutoDisable()
        assert getFetcherStats()["CFetcher"]["fails"] == 1

    def test_zero_threshold_disables_feature(self):
        for _ in range(5):
            recordFetchResult("DFetcher", ok=False, auto_disable_fails=0)
        assert "DFetcher" not in getAutoDisable()

    def test_enable_fetcher_clears_all_and_resets_stats(self):
        for _ in range(2):
            recordFetchResult("EFetcher", ok=False, auto_disable_fails=2)
        toggleRuntimeExclude("EFetcher", False)
        manual, auto = enableFetcher("EFetcher")
        assert manual == []
        assert auto == []
        assert "EFetcher" not in getFetcherStats()

    def test_effective_exclude_merges_manual_and_auto(self):
        toggleRuntimeExclude("FFetcher", False)
        recordFetchResult("GFetcher", ok=False, auto_disable_fails=1)
        effective = set(getEffectiveExclude())
        assert {"FFetcher", "GFetcher"} <= effective
