# -*- coding: utf-8 -*-
# !/usr/bin/env python
"""
-------------------------------------------------
   File Name：    DbClient.py
   Description :  DB工厂类
   Author :       JHao
   date：          2016/12/2
-------------------------------------------------
   Change Activity:
                   2016/12/02:   DB工厂类
                   2020/07/03:   取消raw_proxy储存
                   2026/10/29:   移除 six 兼容层, 改用标准库
                   2026/11/11:   get/pop/getAll 支持 region 地区筛选(工厂层实现, Redis/SSDB通用)
-------------------------------------------------
"""
__author__ = 'JHao'

import os
import sys
import json
from random import choice

from urllib.parse import urlparse
from util.singleton import Singleton

sys.path.append(os.path.dirname(os.path.abspath(__file__)))


class DbClient(metaclass=Singleton):
    """
    DbClient DB工厂类 提供get/put/update/pop/delete/exists/getAll/clean/getCount/changeTable方法


    抽象方法定义：
        get(): 随机返回一个proxy;
        put(proxy): 存入一个proxy;
        pop(): 顺序返回并删除一个proxy;
        update(proxy): 更新指定proxy信息;
        delete(proxy): 删除指定proxy;
        exists(proxy): 判断指定proxy是否存在;
        getAll(): 返回所有代理;
        clean(): 清除所有proxy信息;
        getCount(): 返回proxy统计信息;
        changeTable(name): 切换操作对象


        所有方法需要相应类去具体实现：
            ssdb: ssdbClient.py
            redis: redisClient.py
            mongodb: mongodbClient.py

    """

    def __init__(self, db_conn):
        """
        init
        :return:
        """
        self.parseDbConn(db_conn)
        self.__initDbClient()

    @classmethod
    def parseDbConn(cls, db_conn):
        db_conf = urlparse(db_conn)
        cls.db_type = db_conf.scheme.upper().strip()
        cls.db_host = db_conf.hostname
        cls.db_port = db_conf.port
        cls.db_user = db_conf.username
        cls.db_pwd = db_conf.password
        cls.db_name = db_conf.path[1:]
        return cls

    def __initDbClient(self):
        """
        init DB Client
        :return:
        """
        __type = None
        if "SSDB" == self.db_type:
            __type = "ssdbClient"
        elif "REDIS" == self.db_type:
            __type = "redisClient"
        else:
            pass
        assert __type, 'type error, Not support DB type: {}'.format(self.db_type)
        self.client = getattr(__import__(__type), "%sClient" % self.db_type.title())(host=self.db_host,
                                                                                     port=self.db_port,
                                                                                     username=self.db_user,
                                                                                     password=self.db_pwd,
                                                                                     db=self.db_name)

    @staticmethod
    def _matchRegion(proxy_json, regions):
        """
        判断代理的 region 是否命中筛选集合(大小写不敏感)
        :param proxy_json: 代理属性 json 字符串
        :param regions: 大写地区码集合, 如 {"US", "HK"}
        """
        region = (json.loads(proxy_json).get("region") or "").upper()
        return region in regions

    @classmethod
    def _parseRegions(cls, region):
        """
        解析 region 筛选参数: 逗号分隔多地区, 大小写不敏感
        :param region: "US" / "us,hk"
        :return: 大写地区码集合; 无有效值返回 None
        """
        if not region:
            return None
        regions = {_.strip().upper() for _ in region.split(",") if _.strip()}
        return regions or None

    def get(self, https, region=None):
        """
        返回一个代理; 指定 region 时仅在其命中的代理中随机
        :param https: 是否仅 HTTPS 代理
        :param region: 地区筛选, 逗号分隔(如 "US" / "us,hk"), 大小写不敏感
        """
        regions = self._parseRegions(region)
        if not regions:
            return self.client.get(https)
        proxies = [item for item in self.client.getAll(https)
                   if self._matchRegion(item, regions)]
        return choice(proxies) if proxies else None

    def put(self, key, **kwargs):
        return self.client.put(key, **kwargs)

    def update(self, key, value, **kwargs):
        return self.client.update(key, value, **kwargs)

    def delete(self, key, **kwargs):
        return self.client.delete(key, **kwargs)

    def exists(self, key, **kwargs):
        return self.client.exists(key, **kwargs)

    def pop(self, https, region=None):
        """
        返回并删除一个代理; 指定 region 时仅在其命中的代理中随机
        :param region: 地区筛选, 逗号分隔, 大小写不敏感
        """
        regions = self._parseRegions(region)
        if not regions:
            return self.client.pop(https)
        proxy = self.get(https, region)
        if proxy:
            self.client.delete(json.loads(proxy).get("proxy", ""))
        return proxy

    def getAll(self, https, region=None):
        """
        返回所有代理; 指定 region 时仅返回命中的代理
        :param region: 地区筛选, 逗号分隔, 大小写不敏感
        """
        items = self.client.getAll(https)
        regions = self._parseRegions(region)
        if regions:
            items = [item for item in items if self._matchRegion(item, regions)]
        return items

    def clear(self):
        return self.client.clear()

    def changeTable(self, name):
        self.client.changeTable(name)

    def getCount(self):
        return self.client.getCount()

    def test(self):
        return self.client.test()
