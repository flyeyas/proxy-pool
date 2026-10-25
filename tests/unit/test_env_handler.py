# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     test_env_handler.py
   Description :   .env 加载器单元测试
   Author :        flyeyas
   date：          2026/10/25
-------------------------------------------------
   Change Activity:
                   2026/10/25:
-------------------------------------------------
"""
__author__ = 'flyeyas'

import os
import pytest

from util.envHandler import loadEnvVars, loadAllEnvVars


@pytest.fixture(autouse=True)
def clean_loaded_env():
    """清理测试中注入的环境变量"""
    keys = ["ENV_TEST_A", "ENV_TEST_B", "ENV_TEST_C", "ENV_TEST_D"]
    for key in keys:
        os.environ.pop(key, None)
    yield
    for key in keys:
        os.environ.pop(key, None)


def write_env(tmp_path, content):
    env_file = tmp_path / ".env"
    env_file.write_text(content, encoding="utf-8")
    return str(env_file)


class TestLoadEnvVars:

    def test_loads_key_values(self, tmp_path):
        path = write_env(tmp_path, "ENV_TEST_A=value_a\nENV_TEST_B=value_b\n")
        loaded = loadEnvVars(path)
        assert loaded == {"ENV_TEST_A": "value_a", "ENV_TEST_B": "value_b"}
        assert os.environ["ENV_TEST_A"] == "value_a"

    def test_skips_comment_and_blank_lines(self, tmp_path):
        path = write_env(tmp_path, "# 注释\n\n   \nENV_TEST_A=value_a\n")
        loaded = loadEnvVars(path)
        assert loaded == {"ENV_TEST_A": "value_a"}

    def test_strips_quotes(self, tmp_path):
        path = write_env(tmp_path, 'ENV_TEST_A="value_a"\nENV_TEST_B=\'value_b\'\n')
        loaded = loadEnvVars(path)
        assert loaded == {"ENV_TEST_A": "value_a", "ENV_TEST_B": "value_b"}

    def test_ignores_line_without_equals(self, tmp_path):
        path = write_env(tmp_path, "INVALID_LINE\nENV_TEST_A=value_a\n")
        loaded = loadEnvVars(path)
        assert loaded == {"ENV_TEST_A": "value_a"}

    def test_allows_space_around_key_and_value(self, tmp_path):
        path = write_env(tmp_path, "  ENV_TEST_A  =  value_a  \n")
        loaded = loadEnvVars(path)
        assert loaded == {"ENV_TEST_A": "value_a"}

    def test_existing_env_not_overridden(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENV_TEST_A", "from_system")
        path = write_env(tmp_path, "ENV_TEST_A=from_file\nENV_TEST_B=value_b\n")
        loaded = loadEnvVars(path)
        assert loaded == {"ENV_TEST_B": "value_b"}
        assert os.environ["ENV_TEST_A"] == "from_system"

    def test_missing_file_returns_empty(self, tmp_path):
        assert loadEnvVars(str(tmp_path / "not_exist.env")) == {}


class TestLoadAllEnvVars:
    """线上/本地环境区分: .env.local 覆盖 .env, 系统环境变量优先"""

    def test_local_overrides_base(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ENV_TEST_A", raising=False)
        monkeypatch.delenv("ENV_TEST_B", raising=False)
        (tmp_path / ".env").write_text("ENV_TEST_A=from_env\nENV_TEST_B=base_only\n")
        (tmp_path / ".env.local").write_text("ENV_TEST_A=from_local\n")
        loaded_files = loadAllEnvVars(str(tmp_path))
        assert loaded_files == [".env.local", ".env"]
        assert os.environ["ENV_TEST_A"] == "from_local"
        assert os.environ["ENV_TEST_B"] == "base_only"

    def test_system_env_wins_over_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENV_TEST_A", "from_system")
        (tmp_path / ".env").write_text("ENV_TEST_A=from_env\n")
        (tmp_path / ".env.local").write_text("ENV_TEST_A=from_local\n")
        loadAllEnvVars(str(tmp_path))
        assert os.environ["ENV_TEST_A"] == "from_system"

    def test_no_files_returns_empty(self, tmp_path):
        assert loadAllEnvVars(str(tmp_path)) == []

    def test_base_env_only(self, tmp_path, monkeypatch):
        """线上服务器场景: 只有 .env, 无 .env.local"""
        monkeypatch.delenv("ENV_TEST_C", raising=False)
        (tmp_path / ".env").write_text("ENV_TEST_C=prod\n")
        loaded_files = loadAllEnvVars(str(tmp_path))
        assert loaded_files == [".env"]
        assert os.environ["ENV_TEST_C"] == "prod"


class TestConfigHandlerWithEnv:

    def test_config_handler_loads_env_files(self, monkeypatch, tmp_path):
        """configHandler 模块通过 loadAllEnvVars 加载根目录环境配置文件"""
        import handler.configHandler as config_handler
        monkeypatch.setattr("util.envHandler.ROOT_PATH", str(tmp_path))
        (tmp_path / ".env").write_text("ENV_TEST_D=loaded\n", encoding="utf-8")
        assert config_handler.loadAllEnvVars() == [".env"]
        assert os.environ["ENV_TEST_D"] == "loaded"
