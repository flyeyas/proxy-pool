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

import os
import pytest
from unittest.mock import patch

import api.webuiApi as webuiApi


@pytest.fixture(autouse=True)
def reset_refresh_state():
    """每个测试前后清理手动刷新的运行状态与残留锁"""
    webuiApi._refresh_running = False
    yield
    webuiApi._releaseRefreshLock(webuiApi._refresh_fd)
    if webuiApi._refresh_fd is not None:
        webuiApi._refresh_fd = None
    webuiApi._refresh_running = False


class TestFetchers:

    def test_fetchers_returns_list(self, client):
        """返回非空代理源列表, 字段齐全"""
        resp = client.get("/fetchers/")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["data"]["fetchers"]) > 0
        for item in data["data"]["fetchers"]:
            assert set(["name", "url", "enabled", "excluded"]) <= set(item.keys())

    def test_fetchers_sorted_by_name(self, client):
        """按 name 排序"""
        data = client.get("/fetchers/").get_json()
        names = [item["name"] for item in data["data"]["fetchers"]]
        assert names == sorted(names)


class TestRefresh:

    @patch("api.webuiApi.Thread")
    def test_refresh_starts_background_task(self, mock_thread, client):
        """触发成功: 202 + 后台线程启动(携带锁句柄), target 为抓取校验任务"""
        resp = client.post("/refresh/")
        assert resp.status_code == 202
        data = resp.get_json()
        assert data["code"] == 202
        assert data["message"] == "fetch and check started"
        mock_thread.assert_called_once()
        assert mock_thread.call_args.kwargs.get("target") is webuiApi._runFetchAndCheck
        assert mock_thread.call_args.kwargs.get("daemon") is True
        assert len(mock_thread.call_args.kwargs.get("args", ())) == 1  # 锁句柄随线程传递
        mock_thread.return_value.start.assert_called_once()

    @patch("api.webuiApi.Thread")
    def test_refresh_rejects_when_running(self, mock_thread, client, monkeypatch, tmp_path):
        """锁被持有时(跨进程 flock)触发返回 409"""
        if webuiApi.fcntl is None:  # pragma: no cover - Windows 降级路径
            webuiApi._refresh_running = True
        else:
            import fcntl
            lock = str(tmp_path / "refresh.lock")
            monkeypatch.setattr(webuiApi, "_REFRESH_LOCK_FILE", lock)
            fd = os.open(lock, os.O_CREAT | os.O_RDWR)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            webuiApi._refresh_fd = fd
        try:
            resp = client.post("/refresh/")
            assert resp.status_code == 409
            assert resp.get_json()["code"] == 409
            mock_thread.assert_not_called()
        finally:
            webuiApi._releaseRefreshLock(webuiApi._refresh_fd)


class TestLogs:

    def test_logs_list(self, client, tmp_path, monkeypatch):
        """无参返回日志文件列表(含大小)"""
        monkeypatch.setattr("handler.logHandler.LOG_PATH", str(tmp_path))
        (tmp_path / "fetch.log").write_text("l1\n", encoding="utf-8")
        (tmp_path / "checker.log").write_text("l1\nl2\n", encoding="utf-8")
        (tmp_path / "not_log.txt").write_text("x", encoding="utf-8")
        resp = client.get("/logs/")
        assert resp.status_code == 200
        logs = resp.get_json()["data"]["logs"]
        assert [item["name"] for item in logs] == ["checker", "fetch"]
        assert all(isinstance(item["size"], int) for item in logs)
        assert next(i for i in logs if i["name"] == "fetch")["size"] == 3

    def test_logs_content_returns_tail(self, client, tmp_path, monkeypatch):
        """返回指定日志的尾部行"""
        monkeypatch.setattr("handler.logHandler.LOG_PATH", str(tmp_path))
        (tmp_path / "fetch.log").write_text("l1\nl2\nl3\n", encoding="utf-8")
        resp = client.get("/logs/?name=fetch&lines=2")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
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
        """/fetchers/ 包含类名/黑名单状态/自动禁用与统计字段"""
        data = client.get("/fetchers/").get_json()
        assert len(data["data"]["fetchers"]) > 0
        for item in data["data"]["fetchers"]:
            assert {"class_name", "excluded", "setting_excluded", "runtime_excluded",
                    "auto_disabled", "stats"} <= set(item.keys())

    def test_auto_disabled_display_and_enable(self, client, runtime_config):
        """自动禁用的源展示状态与统计, PATCH 启用后清除并重置"""
        import json as jsonlib
        runtime_config.write_text(jsonlib.dumps({
            "fetcher_auto_disable": ["KuaidailiFetcher"],
            "fetcher_stats": {
                "KuaidailiFetcher": {
                    "fails": 5, "last_error": "timeout",
                    "disable_reason": "连续失败 5 次: timeout",
                    "disabled_at": "2026-10-25 00:00:00"}
            }
        }), encoding="utf-8")

        data = client.get("/fetchers/").get_json()["data"]
        item = next(f for f in data["fetchers"] if f["class_name"] == "KuaidailiFetcher")
        assert item["auto_disabled"] is True
        assert item["excluded"] is True
        assert item["stats"]["fails"] == 5

        resp = client.patch("/fetchers/KuaidailiFetcher/", json={"enabled": True})
        assert resp.status_code == 200
        assert resp.get_json()["data"]["auto_disable"] == []

        data = client.get("/fetchers/").get_json()["data"]
        item = next(f for f in data["fetchers"] if f["class_name"] == "KuaidailiFetcher")
        assert item["auto_disabled"] is False
        assert item["excluded"] is False
        assert item["stats"] == {}

    def test_toggle_disable_and_enable(self, client, runtime_config):
        """禁用 -> 状态为已排除; 启用 -> 恢复"""
        resp = client.patch("/fetchers/KuaidailiFetcher/", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.get_json()["data"]["fetcher_exclude"] == ["KuaidailiFetcher"]

        data = client.get("/fetchers/").get_json()["data"]
        item = next(f for f in data["fetchers"] if f["class_name"] == "KuaidailiFetcher")
        assert item["runtime_excluded"] is True
        assert item["excluded"] is True

        resp = client.patch("/fetchers/KuaidailiFetcher/", json={"enabled": True})
        assert resp.get_json()["data"]["fetcher_exclude"] == []
        data = client.get("/fetchers/").get_json()["data"]
        item = next(f for f in data["fetchers"] if f["class_name"] == "KuaidailiFetcher")
        assert item["runtime_excluded"] is False
        assert item["excluded"] is False

    def test_toggle_rejects_invalid_class_name(self, client, runtime_config):
        resp = client.patch("/fetchers/invalid!name/", json={"enabled": False})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == 400

    def test_toggle_requires_login_when_auth_on(self, client, monkeypatch, tmp_path):
        """开启登录后 toggle 接口需要登录"""
        monkeypatch.setenv("WEBUI_PASSWORD", "secret123")
        monkeypatch.setattr("helper.runtimeConfig.CONFIG_FILE",
                            str(tmp_path / "runtime_config.json"))
        resp = client.patch("/fetchers/KuaidailiFetcher/", json={"enabled": False})
        assert resp.status_code == 401


class TestWebuiAuth:
    """WebUI 登录鉴权测试(conftest 已将 WEBUI_PASSWORD 置空=默认关闭)"""

    def test_status_when_disabled(self, client):
        """未配置密码时登录关闭, 接口无需登录; 返回程序版本"""
        resp = client.get("/auth/status/")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["required"] is False
        assert data["logged_in"] is False
        assert data["version"]
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
        data = resp.get_json()
        assert data["code"] == 200
        assert data["message"] == "login successful"

        assert client.get("/fetchers/").status_code == 200
        status = client.get("/auth/status/").get_json()["data"]
        assert status["required"] is True
        assert status["logged_in"] is True

        client.post("/auth/logout/")
        assert client.get("/fetchers/").status_code == 401

    def test_protected_paths_when_auth_on(self, client, monkeypatch):
        """开启登录后管理与数据接口均被拦"""
        monkeypatch.setenv("WEBUI_PASSWORD", "secret123")
        assert client.get("/delete/?proxy=1.2.3.4:8080").status_code == 401
        assert client.post("/refresh/").status_code == 401
        # 数据接口(代理API)同样需要授权
        assert client.get("/count/").status_code == 401
        assert client.get("/all/").status_code == 401


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
        resp = client.post("/auth/tokens/", json={"name": "spider", "expire_days": 7})
        assert resp.status_code == 201
        assert resp.get_json()["code"] == 201
        token = resp.get_json()["data"]["token"]["token"]
        client.post("/auth/logout/")

        assert client.get("/all/").status_code == 401
        assert client.get("/all/?token=" + token).status_code == 200
        assert client.get("/count/",
                          headers={"Authorization": "Bearer " + token}).status_code == 200

        # token 不能管理 token(仅登录会话可管理)
        assert client.delete("/auth/tokens/%s/" % token).status_code == 401

        self._login(client)
        resp = client.delete("/auth/tokens/%s/" % token)
        assert resp.status_code == 200
        assert resp.get_json()["code"] == 200
        client.post("/auth/logout/")
        assert client.get("/all/?token=" + token).status_code == 401

    def test_token_delete_not_found(self, client):
        """删除不存在的 token 404"""
        self._login(client)
        resp = client.delete("/auth/tokens/pp_not_exists/")
        assert resp.status_code == 404
        assert resp.get_json()["code"] == 404

    def test_token_rename(self, client):
        """PATCH 修改 token 备注名"""
        self._login(client)
        resp = client.post("/auth/tokens/", json={"name": "old", "expire_days": 1})
        token = resp.get_json()["data"]["token"]["token"]

        resp = client.patch("/auth/tokens/%s/" % token, json={"name": "new-name"})
        assert resp.status_code == 200
        assert resp.get_json()["data"]["token"]["name"] == "new-name"
        names = [t["name"] for t in
                 client.get("/auth/tokens/").get_json()["data"]["tokens"]]
        assert "new-name" in names

        # 空名 400; 不存在 404
        assert client.patch("/auth/tokens/%s/" % token,
                            json={"name": ""}).status_code == 400
        assert client.patch("/auth/tokens/pp_none/",
                            json={"name": "x"}).status_code == 404
        client.delete("/auth/tokens/%s/" % token)

    def test_expired_token_rejected(self, client, runtime_config):
        """过期 token 不再授权"""
        import json as jsonlib
        self._login(client)
        resp = client.post("/auth/tokens/", json={"name": "old", "expire_days": 1})
        token = resp.get_json()["data"]["token"]["token"]
        config = jsonlib.loads(runtime_config.read_text(encoding="utf-8"))
        config["api_tokens"][0]["expires_at"] = "2000-01-01 00:00:00"
        runtime_config.write_text(jsonlib.dumps(config), encoding="utf-8")
        client.post("/auth/logout/")
        assert client.get("/count/?token=" + token).status_code == 401
