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

import helper.fetcherConfig as fetcher_config
from helper.fetcherConfig import (
    getRuntimeExclude, setRuntimeExclude, toggleRuntimeExclude
)


@pytest.fixture(autouse=True)
def tmp_config_file(tmp_path, monkeypatch):
    """每个测试使用独立的临时配置文件"""
    path = tmp_path / "runtime_config.json"
    monkeypatch.setattr(fetcher_config, "CONFIG_FILE", str(path))
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
