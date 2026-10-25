# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     test_webui_api.py
   Description :   WebUI后台接口测试
   Author :        flyeyas
   date：          2026/10/24
-------------------------------------------------
   Change Activity:
                   2026/10/24:
-------------------------------------------------
"""
__author__ = 'flyeyas'

import pytest
from unittest.mock import patch

import api.webuiApi as webuiApi


@pytest.fixture(autouse=True)
def reset_refresh_state():
    """每个测试前后重置手动刷新的运行状态"""
    webuiApi._refresh_running = False
    yield
    webuiApi._refresh_running = False


class TestFetchers:

    def test_fetchers_returns_list(self, client):
        """返回非空代理源列表, 字段齐全"""
        resp = client.get("/fetchers/")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "fetchers" in data
        assert len(data["fetchers"]) > 0
        for item in data["fetchers"]:
            assert set(["name", "url", "enabled", "excluded"]) <= set(item.keys())

    def test_fetchers_sorted_by_name(self, client):
        """按 name 排序"""
        data = client.get("/fetchers/").get_json()
        names = [item["name"] for item in data["fetchers"]]
        assert names == sorted(names)


class TestRefresh:

    @patch("api.webuiApi.Thread")
    def test_refresh_starts_background_task(self, mock_thread, client):
        """触发成功: 后台线程启动, target 为抓取校验任务"""
        resp = client.post("/refresh/")
        assert resp.status_code == 200
        assert resp.get_json() == {"code": 0, "src": "started"}
        mock_thread.assert_called_once()
        assert mock_thread.call_args.kwargs.get("target") is webuiApi._runFetchAndCheck
        assert mock_thread.call_args.kwargs.get("daemon") is True
        mock_thread.return_value.start.assert_called_once()

    @patch("api.webuiApi.Thread")
    def test_refresh_rejects_when_running(self, mock_thread, client):
        """运行中重复触发被拒"""
        webuiApi._refresh_running = True
        resp = client.post("/refresh/")
        assert resp.status_code == 200
        assert resp.get_json() == {"code": 1, "src": "already running"}
        mock_thread.assert_not_called()


class TestLogs:

    def test_logs_list(self, client, tmp_path, monkeypatch):
        """无参返回日志文件列表"""
        monkeypatch.setattr("handler.logHandler.LOG_PATH", str(tmp_path))
        (tmp_path / "fetch.log").write_text("l1\n", encoding="utf-8")
        (tmp_path / "checker.log").write_text("l1\n", encoding="utf-8")
        (tmp_path / "not_log.txt").write_text("x", encoding="utf-8")
        resp = client.get("/logs/")
        assert resp.status_code == 200
        assert resp.get_json() == {"logs": ["checker", "fetch"]}

    def test_logs_content_returns_tail(self, client, tmp_path, monkeypatch):
        """返回指定日志的尾部行"""
        monkeypatch.setattr("handler.logHandler.LOG_PATH", str(tmp_path))
        (tmp_path / "fetch.log").write_text("l1\nl2\nl3\n", encoding="utf-8")
        resp = client.get("/logs/?name=fetch&lines=2")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "fetch"
        assert data["lines"] == ["l2", "l3"]

    def test_logs_file_not_found(self, client, tmp_path, monkeypatch):
        """日志文件不存在返回 404"""
        monkeypatch.setattr("handler.logHandler.LOG_PATH", str(tmp_path))
        resp = client.get("/logs/?name=nope")
        assert resp.status_code == 404

    def test_logs_rejects_path_traversal(self, client):
        """非法日志名(路径穿越)返回 400"""
        resp = client.get("/logs/?name=../setting")
        assert resp.status_code == 400


class TestWebuiStatic:

    @pytest.fixture
    def built_dist(self, tmp_path, monkeypatch):
        """临时 dist 目录, 含 index.html 与一个静态资源"""
        (tmp_path / "index.html").write_text("<html>webui-app</html>", encoding="utf-8")
        (tmp_path / "test.js").write_text("console.log(1)", encoding="utf-8")
        monkeypatch.setattr(webuiApi, "WEBUI_DIST_PATH", str(tmp_path))
        return tmp_path

    def test_webui_serves_index(self, client, built_dist):
        resp = client.get("/webui/")
        assert resp.status_code == 200
        assert b"webui-app" in resp.data

    def test_webui_serves_asset(self, client, built_dist):
        resp = client.get("/webui/test.js")
        assert resp.status_code == 200
        assert b"console.log" in resp.data

    def test_webui_spa_fallback(self, client, built_dist):
        """不存在的路径回退到 index.html (SPA history 路由)"""
        resp = client.get("/webui/fetchers")
        assert resp.status_code == 200
        assert b"webui-app" in resp.data

    def test_webui_not_built_returns_503(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(webuiApi, "WEBUI_DIST_PATH", str(tmp_path / "not_exist"))
        resp = client.get("/webui/")
        assert resp.status_code == 503
        assert b"npm run build" in resp.data


class TestFetcherToggle:
    """代理源后台开关测试"""

    @pytest.fixture
    def runtime_config(self, tmp_path, monkeypatch):
        """隔离运行时配置文件, 避免写入项目根目录"""
        path = tmp_path / "runtime_config.json"
        monkeypatch.setattr("helper.runtimeConfig.CONFIG_FILE", str(path))
        return path

    def test_fetchers_returns_runtime_fields(self, client, runtime_config):
        """/fetchers/ 包含类名与运行时/静态黑名单状态字段"""
        data = client.get("/fetchers/").get_json()
        assert len(data["fetchers"]) > 0
        for item in data["fetchers"]:
            assert {"class_name", "excluded", "setting_excluded", "runtime_excluded"} <= set(item.keys())

    def test_toggle_disable_and_enable(self, client, runtime_config):
        """禁用 -> 状态为已排除; 启用 -> 恢复"""
        resp = client.post("/fetchers/toggle/",
                           json={"class_name": "KuaidailiFetcher", "enable": False})
        assert resp.status_code == 200
        assert resp.get_json()["fetcher_exclude"] == ["KuaidailiFetcher"]

        data = client.get("/fetchers/").get_json()
        item = next(f for f in data["fetchers"] if f["class_name"] == "KuaidailiFetcher")
        assert item["runtime_excluded"] is True
        assert item["excluded"] is True

        resp = client.post("/fetchers/toggle/",
                           json={"class_name": "KuaidailiFetcher", "enable": True})
        assert resp.get_json()["fetcher_exclude"] == []
        data = client.get("/fetchers/").get_json()
        item = next(f for f in data["fetchers"] if f["class_name"] == "KuaidailiFetcher")
        assert item["runtime_excluded"] is False
        assert item["excluded"] is False

    def test_toggle_rejects_invalid_class_name(self, client, runtime_config):
        resp = client.post("/fetchers/toggle/",
                           json={"class_name": "../setting", "enable": False})
        assert resp.status_code == 400

    def test_toggle_requires_login_when_auth_on(self, client, monkeypatch, tmp_path):
        """开启登录后 toggle 接口需要登录"""
        monkeypatch.setenv("WEBUI_PASSWORD", "secret123")
        monkeypatch.setattr("helper.runtimeConfig.CONFIG_FILE",
                            str(tmp_path / "runtime_config.json"))
        resp = client.post("/fetchers/toggle/",
                           json={"class_name": "KuaidailiFetcher", "enable": False})
        assert resp.status_code == 401


class TestWebuiAuth:
    """WebUI 登录鉴权测试(conftest 已将 WEBUI_PASSWORD 置空=默认关闭)"""

    def test_status_when_disabled(self, client):
        """未配置密码时登录关闭, 接口无需登录"""
        resp = client.get("/auth/status/")
        assert resp.status_code == 200
        assert resp.get_json() == {"required": False, "logged_in": False}
        assert client.get("/fetchers/").status_code == 200

    def test_login_flow(self, client, monkeypatch):
        """开启登录后: 未登录拦截 -> 登录 -> 可访问 -> 登出 -> 再拦截"""
        monkeypatch.setenv("WEBUI_USERNAME", "boss")
        monkeypatch.setenv("WEBUI_PASSWORD", "secret123")

        assert client.get("/fetchers/").status_code == 401

        resp = client.post("/auth/login/", json={"username": "boss", "password": "wrong"})
        assert resp.status_code == 401

        resp = client.post("/auth/login/", json={"username": "boss", "password": "secret123"})
        assert resp.status_code == 200
        assert resp.get_json() == {"code": 0, "src": "ok"}

        assert client.get("/fetchers/").status_code == 200
        assert client.get("/auth/status/").get_json() == {"required": True, "logged_in": True}

        client.get("/auth/logout/")
        assert client.get("/fetchers/").status_code == 401

    def test_protected_paths_when_auth_on(self, client, monkeypatch):
        """开启登录后管理与数据接口均被拦"""
        monkeypatch.setenv("WEBUI_PASSWORD", "secret123")
        assert client.get("/delete/?proxy=1.2.3.4:8080").status_code == 401
        assert client.post("/refresh/").status_code == 401
        # 数据接口(代理API)同样需要授权
        assert client.get("/count/").status_code == 401
        assert client.get("/all/").status_code == 401
        # GET /refresh/ 为原有stub, 保持公开
        assert client.get("/refresh/").status_code == 200


class TestApiToken:
    """数据接口授权 + API Token 流程"""

    @pytest.fixture(autouse=True)
    def runtime_config(self, tmp_path, monkeypatch):
        path = tmp_path / "runtime_config.json"
        monkeypatch.setattr("helper.runtimeConfig.CONFIG_FILE", str(path))
        return path

    @pytest.fixture(autouse=True)
    def auth_enabled(self, monkeypatch):
        """统一开启登录, 账号 admin/secret123"""
        monkeypatch.setenv("WEBUI_USERNAME", "admin")
        monkeypatch.setenv("WEBUI_PASSWORD", "secret123")

    def _login(self, client):
        client.post("/auth/login/", json={"username": "admin", "password": "secret123"})

    def test_data_api_requires_auth(self, client):
        """开启登录后数据接口无凭证 401, 登录会话可用"""
        assert client.get("/count/").status_code == 401
        assert client.get("/count/?token=bad").status_code == 401
        self._login(client)
        assert client.get("/count/").status_code == 200

    def test_token_grants_api_access(self, client):
        """token 通过参数/请求头授权数据接口; 删除后失效"""
        self._login(client)
        resp = client.post("/auth/tokens/create/",
                           json={"name": "spider", "expire_days": 7})
        token = resp.get_json()["token"]["token"]
        client.get("/auth/logout/")

        assert client.get("/all/").status_code == 401
        assert client.get("/all/?token=" + token).status_code == 200
        assert client.get("/count/",
                          headers={"Authorization": "Bearer " + token}).status_code == 200

        # token 不能管理 token(仅登录会话可管理)
        assert client.post("/auth/tokens/delete/",
                           json={"token": token}).status_code == 401

        self._login(client)
        client.post("/auth/tokens/delete/", json={"token": token})
        client.get("/auth/logout/")
        assert client.get("/all/?token=" + token).status_code == 401

    def test_expired_token_rejected(self, client, runtime_config):
        """过期 token 不再授权"""
        import json as jsonlib
        self._login(client)
        resp = client.post("/auth/tokens/create/",
                           json={"name": "old", "expire_days": 1})
        token = resp.get_json()["token"]["token"]
        config = jsonlib.loads(runtime_config.read_text(encoding="utf-8"))
        config["api_tokens"][0]["expires_at"] = "2000-01-01 00:00:00"
        runtime_config.write_text(jsonlib.dumps(config), encoding="utf-8")
        client.get("/auth/logout/")
        assert client.get("/count/?token=" + token).status_code == 401
