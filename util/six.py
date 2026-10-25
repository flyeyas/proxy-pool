# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     six
   Description :   py2/py3 兼容层(现仅保留 py3 实现)
   Author :        JHao
   date：          2020/6/22
-------------------------------------------------
   Change Activity:
                   2020/6/22: py2/py3 兼容层
                   2026/10/25: 移除 Python2 分支(项目仅支持 py3.8+), 补充类型标注
-------------------------------------------------
"""
__author__ = 'JHao'

from typing import Any

from urllib.parse import urlparse
from importlib import reload as reload_six
from queue import Empty, Queue


def iteritems(d, **kw):
    return iter(d.items(**kw))


def withMetaclass(meta, *bases) -> Any:
    """Create a base class with a metaclass."""

    # This requires a bit of explanation: the basic idea is to make a dummy
    # metaclass for one level of class instantiation that replaces itself with
    # the actual metaclass.
    class MetaClass(meta):

        def __new__(cls, name, this_bases, d):
            return meta(name, bases, d)

    return type.__new__(MetaClass, 'temporary_class', (), {})
