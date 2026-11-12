# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     ihuan.py
   Description :   小幻代理代理源
   Author :        JHao
   date：          2026/5/31
-------------------------------------------------
   Change Activity:
                   2026/05/31:
                   2026/11/12: Cookie 会话通过统一采集代理请求
-------------------------------------------------
"""
# pyright: reportAttributeAccessIssue=false
__author__ = 'JHao'

import requests

from fetcher.baseFetcher import BaseFetcher
from util.webRequest import WebRequest


class IhuanFetcher(BaseFetcher):
    """小幻代理 https://ip.ihuan.me/"""

    name = "ihuan"
    url = "https://ip.ihuan.me/"
    enabled = True

    def fetch(self):
        session = requests.session()
        session.trust_env = False
        try:
            wb = WebRequest(session=session)
            headers = wb.header
            wb.get(self.url, header=headers, timeout=10, verify=False)
            tree = wb.get(self.url, header=headers, timeout=10, verify=False).tree
            if tree is None:
                return
            for item in tree.xpath("//table[@class='table table-hover table-bordered']//tr"):
                ip = "".join(item.xpath("./td[1]//text()")).strip()
                port = "".join(item.xpath("./td[2]//text()")).strip()
                if ip and port:
                    yield "%s:%s" % (ip, port)
        finally:
            session.close()


if __name__ == '__main__':
    for proxy in IhuanFetcher().fetch():
        print(proxy)
