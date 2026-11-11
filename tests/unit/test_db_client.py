# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     testDbClient.py
   Description :   DbClient URI解析单元测试
   Author :        JHao
   date：          2026/5/28
-------------------------------------------------
   Change Activity:
                   2026/05/28:
                   2026/11/11: region 地区筛选测试
-------------------------------------------------
"""
__author__ = 'JHao'

import json

import pytest
from unittest.mock import MagicMock, patch

from db.dbClient import DbClient


class TestParseDbConn:

    def test_redis_uri(self):
        DbClient.parseDbConn("redis://:password@127.0.0.1:6379/1")
        assert DbClient.db_type == "REDIS"
        assert DbClient.db_pwd == "password"
        assert DbClient.db_host == "127.0.0.1"
        assert DbClient.db_port == 6379
        assert DbClient.db_name == "1"

    def test_ssdb_uri(self):
        DbClient.parseDbConn("ssdb://:password@127.0.0.1:8888")
        assert DbClient.db_type == "SSDB"
        assert DbClient.db_pwd == "password"
        assert DbClient.db_host == "127.0.0.1"
        assert DbClient.db_port == 8888

    def test_redis_uri_no_password(self):
        DbClient.parseDbConn("redis://127.0.0.1:6379/0")
        assert DbClient.db_type == "REDIS"
        assert DbClient.db_pwd is None
        assert DbClient.db_host == "127.0.0.1"
        assert DbClient.db_port == 6379
        assert DbClient.db_name == "0"

    def test_ssdb_uri_no_password(self):
        DbClient.parseDbConn("ssdb://@127.0.0.1:8888")
        assert DbClient.db_type == "SSDB"
        assert DbClient.db_host == "127.0.0.1"
        assert DbClient.db_port == 8888

    def test_unknown_db_type_raises(self):
        with pytest.raises(AssertionError):
            DbClient("mysql://127.0.0.1:3306")

    @pytest.mark.parametrize("uri,expected_type", [
        ("redis://:pwd@10.0.0.1:6380/2", "REDIS"),
        ("ssdb://:pwd@10.0.0.1:8899", "SSDB"),
    ])
    def test_parse_returns_cls(self, uri, expected_type):
        """parseDbConn 返回 cls 以支持链式调用"""
        result = DbClient.parseDbConn(uri)
        assert result is DbClient
        assert DbClient.db_type == expected_type


class TestDbClientInit:

    @patch("db.dbClient.DbClient.parseDbConn")
    def test_redis_init(self, mock_parse):
        """Redis URI -> RedisClient 实例"""
        with patch.object(DbClient, "_DbClient__initDbClient") as mock_init:
            db = DbClient.__new__(DbClient)
            DbClient.__init__(db, "redis://:pwd@127.0.0.1:6379/0")
            mock_parse.assert_called_once_with("redis://:pwd@127.0.0.1:6379/0")
            mock_init.assert_called_once()

    @patch("db.dbClient.DbClient.parseDbConn")
    def test_ssdb_init(self, mock_parse):
        """SSDB URI -> SsdbClient 实例"""
        with patch.object(DbClient, "_DbClient__initDbClient") as mock_init:
            db = DbClient.__new__(DbClient)
            DbClient.__init__(db, "ssdb://:pwd@127.0.0.1:8888")
            mock_parse.assert_called_once_with("ssdb://:pwd@127.0.0.1:8888")
            mock_init.assert_called_once()


class TestDbClientDelegation:
    """所有委托方法测试"""

    def _make_client(self):
        """构造注入 mock client 的 DbClient"""
        db = DbClient.__new__(DbClient)
        db.client = MagicMock()
        return db

    def test_get(self):
        db = self._make_client()
        db.client.get.return_value = '{"proxy": "1.2.3.4:8080"}'
        result = db.get(True)
        db.client.get.assert_called_once_with(True)
        assert result == '{"proxy": "1.2.3.4:8080"}'

    def test_put(self):
        db = self._make_client()
        db.put("1.2.3.4:8080")
        db.client.put.assert_called_once_with("1.2.3.4:8080")

    def test_update(self):
        db = self._make_client()
        db.update("key", "value")
        db.client.update.assert_called_once_with("key", "value")

    def test_delete(self):
        db = self._make_client()
        db.delete("1.2.3.4:8080")
        db.client.delete.assert_called_once_with("1.2.3.4:8080")

    def test_exists(self):
        db = self._make_client()
        db.client.exists.return_value = True
        result = db.exists("1.2.3.4:8080")
        db.client.exists.assert_called_once_with("1.2.3.4:8080")
        assert result is True

    def test_pop(self):
        db = self._make_client()
        db.client.pop.return_value = '{"proxy": "1.2.3.4:8080"}'
        result = db.pop(True)
        db.client.pop.assert_called_once_with(True)
        assert result == '{"proxy": "1.2.3.4:8080"}'

    def test_getAll(self):
        db = self._make_client()
        db.client.getAll.return_value = []
        result = db.getAll(False)
        db.client.getAll.assert_called_once_with(False)
        assert result == []

    def test_clear(self):
        db = self._make_client()
        db.clear()
        db.client.clear.assert_called_once()

    def test_changeTable(self):
        db = self._make_client()
        db.changeTable("use_proxy")
        db.client.changeTable.assert_called_once_with("use_proxy")

    def test_getCount(self):
        db = self._make_client()
        db.client.getCount.return_value = 42
        result = db.getCount()
        db.client.getCount.assert_called_once()
        assert result == 42

    def test_test(self):
        db = self._make_client()
        db.client.test.return_value = True
        result = db.test()
        db.client.test.assert_called_once()
        assert result is True


class TestDbClientRegionFilter:
    """工厂层 region 地区筛选测试"""

    ITEMS = [
        '{"proxy": "1.1.1.1:80", "region": "US"}',
        '{"proxy": "2.2.2.2:80", "region": "CN"}',
        '{"proxy": "3.3.3.3:80", "region": "HK"}',
        '{"proxy": "4.4.4.4:80", "region": null}',
        '{"proxy": "5.5.5.5:80"}',
    ]

    def _make_client(self):
        db = DbClient.__new__(DbClient)
        db.client = MagicMock()
        db.client.getAll.return_value = list(self.ITEMS)
        return db

    def test_get_all_region_single(self):
        """单地区筛选"""
        db = self._make_client()
        result = db.getAll(False, "US")
        assert result == ['{"proxy": "1.1.1.1:80", "region": "US"}']

    def test_get_all_region_multi_and_case_insensitive(self):
        """逗号分隔多地区 + 大小写不敏感"""
        db = self._make_client()
        result = db.getAll(False, "us,hk")
        assert len(result) == 2
        assert '{"proxy": "1.1.1.1:80", "region": "US"}' in result
        assert '{"proxy": "3.3.3.3:80", "region": "HK"}' in result

    def test_get_all_region_no_match(self):
        db = self._make_client()
        assert db.getAll(False, "JP") == []

    def test_get_all_region_none_keeps_all(self):
        """不传 region 时行为与原来一致, 且直接委托 client.getAll"""
        db = self._make_client()
        result = db.getAll(False)
        assert result == self.ITEMS
        db.client.getAll.assert_called_once_with(False)

    def test_get_all_region_empty_string_keeps_all(self):
        """空字符串 / 纯逗号视为不筛选"""
        db = self._make_client()
        assert db.getAll(False, "") == self.ITEMS
        assert db.getAll(False, " , ") == self.ITEMS

    def test_get_region_picks_from_matches_only(self):
        """get 带地区时仅从命中的代理中随机"""
        db = self._make_client()
        result = json.loads(db.get(False, "CN"))
        assert result["proxy"] == "2.2.2.2:80"
        # 未命中地区返回 None
        assert db.get(False, "JP") is None

    def test_get_region_none_delegates(self):
        """不传 region 时委托 client.get(原快速路径)"""
        db = self._make_client()
        db.client.get.return_value = '{"proxy": "9.9.9.9:80"}'
        assert db.get(False) == '{"proxy": "9.9.9.9:80"}'
        db.client.get.assert_called_once_with(False)
        db.client.getAll.assert_not_called()

    def test_pop_region_deletes_matched(self):
        """pop 带地区时删除命中代理并返回"""
        db = self._make_client()
        result = json.loads(db.pop(False, "HK"))
        assert result["proxy"] == "3.3.3.3:80"
        db.client.delete.assert_called_once_with("3.3.3.3:80")

    def test_pop_region_no_match_returns_none(self):
        db = self._make_client()
        assert db.pop(False, "JP") is None
        db.client.delete.assert_not_called()

    def test_pop_region_none_delegates(self):
        """不传 region 时委托 client.pop(原路径)"""
        db = self._make_client()
        db.client.pop.return_value = '{"proxy": "9.9.9.9:80"}'
        assert db.pop(False) == '{"proxy": "9.9.9.9:80"}'
        db.client.pop.assert_called_once_with(False)