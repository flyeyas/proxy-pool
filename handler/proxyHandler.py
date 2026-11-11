# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     ProxyHandler.py
   Description :
   Author :       JHao
   date：          2016/12/3
-------------------------------------------------
   Change Activity:
                   2016/12/03:
                   2020/05/26: 区分http和https
                   2026/11/11: get/pop/getAll 支持 region 地区筛选
-------------------------------------------------
"""
__author__ = 'JHao'

from helper.proxy import Proxy
from db.dbClient import DbClient
from handler.configHandler import ConfigHandler


class ProxyHandler(object):
    """ Proxy CRUD operator"""

    def __init__(self):
        self.conf = ConfigHandler()
        self.db = DbClient(self.conf.dbConn)
        self.db.changeTable(self.conf.tableName)

    def get(self, https=False, region=None):
        """
        return a proxy
        Args:
            https: True/False
            region: 地区筛选(如 "US" / "us,hk"), None 不筛选
        Returns:
        """
        proxy = self.db.get(https, region)
        return Proxy.createFromJson(proxy) if proxy else None

    def pop(self, https, region=None):
        """
        return and delete a useful proxy
        Args:
            region: 地区筛选(如 "US" / "us,hk"), None 不筛选
        :return:
        """
        proxy = self.db.pop(https, region)
        if proxy:
            return Proxy.createFromJson(proxy)
        return None

    def put(self, proxy):
        """
        put proxy into use proxy
        :return:
        """
        self.db.put(proxy)

    def delete(self, proxy):
        """
        delete useful proxy
        :param proxy:
        :return:
        """
        return self.db.delete(proxy.proxy)

    def getAll(self, https=False, region=None):
        """
        get all proxy from pool as Proxy list
        Args:
            region: 地区筛选(如 "US" / "us,hk"), None 不筛选
        :return:
        """
        proxies = self.db.getAll(https, region)
        return [Proxy.createFromJson(_) for _ in proxies]

    def exists(self, proxy):
        """
        check proxy exists
        :param proxy:
        :return:
        """
        return self.db.exists(proxy.proxy)

    def getCount(self):
        """
        return raw_proxy and use_proxy count
        :return:
        """
        total_use_proxy = self.db.getCount()
        return {'count': total_use_proxy}
