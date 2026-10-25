# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     test_api_token.py
   Description :   API Token 管理单元测试
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
from helper.apiToken import listTokens, generateToken, deleteToken, verifyApiToken


@pytest.fixture(autouse=True)
def tmp_config_file(tmp_path, monkeypatch):
    """每个测试使用独立的临时配置文件"""
    path = tmp_path / "runtime_config.json"
    monkeypatch.setattr(runtime_config_mod, "CONFIG_FILE", str(path))
    return path


class TestApiToken:

    def test_generate_and_verify(self):
        item = generateToken("spider", expire_days=7)
        assert item["token"].startswith("pp_")
        assert item["expires_at"]
        assert verifyApiToken(item["token"]) is True
        assert listTokens() == [item]

    def test_permanent_token_has_no_expiry(self):
        item = generateToken("forever", expire_days=0)
        assert item["expires_at"] == ""
        assert verifyApiToken(item["token"]) is True

    def test_verify_invalid(self):
        assert verifyApiToken("") is False
        assert verifyApiToken("pp_not_exists") is False

    def test_expired_token_rejected(self, tmp_config_file):
        generateToken("old", expire_days=1)
        config = json.loads(tmp_config_file.read_text(encoding="utf-8"))
        config["api_tokens"][0]["expires_at"] = "2000-01-01 00:00:00"
        tmp_config_file.write_text(json.dumps(config), encoding="utf-8")
        assert verifyApiToken(config["api_tokens"][0]["token"]) is False

    def test_delete_token(self):
        item = generateToken("a")
        assert deleteToken(item["token"]) is True
        assert verifyApiToken(item["token"]) is False
        assert deleteToken(item["token"]) is False

    def test_tokens_coexist_with_fetcher_exclude(self, tmp_config_file):
        """token 与代理源黑名单共存于同一配置文件, 互不覆盖"""
        from helper.fetcherConfig import setRuntimeExclude, getRuntimeExclude
        item = generateToken("a")
        setRuntimeExclude(["XFetcher"])
        assert verifyApiToken(item["token"]) is True
        assert getRuntimeExclude() == ["XFetcher"]
        assert listTokens()[0]["name"] == "a"
