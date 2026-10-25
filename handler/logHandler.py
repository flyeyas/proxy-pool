# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     LogHandler.py
   Description :  日志操作模块
   Author :       JHao
   date：          2017/3/6
-------------------------------------------------
   Change Activity:
                   2017/03/06: log handler
                   2017/09/21: 屏幕输出/文件输出 可选(默认屏幕和文件均输出)
                   2020/07/13: Windows下TimedRotatingFileHandler线程不安全, 不再使用
                   2026/10/24: 新增listLogFiles/readRecentLines 供WebUI日志查看
                   2026/10/25: 日志改为按小时切割, 保留168个(7天)
-------------------------------------------------
"""
__author__ = 'JHao'

import os
import re
import logging
import platform
import collections

from logging.handlers import TimedRotatingFileHandler

# 日志级别
CRITICAL = 50
FATAL = CRITICAL
ERROR = 40
WARNING = 30
WARN = WARNING
INFO = 20
DEBUG = 10
NOTSET = 0

CURRENT_PATH = os.path.dirname(os.path.abspath(__file__))
ROOT_PATH = os.path.join(CURRENT_PATH, os.pardir)
LOG_PATH = os.path.join(ROOT_PATH, 'log')

if not os.path.exists(LOG_PATH):
    try:
        os.mkdir(LOG_PATH)
    except FileExistsError:
        pass


class LogHandler(logging.Logger):
    """
    LogHandler
    """

    def __init__(self, name, level=DEBUG, stream=True, file=True):
        self.name = name
        self.level = level
        logging.Logger.__init__(self, self.name, level=level)
        if stream:
            self.__setStreamHandler__()
        if file:
            if platform.system() != "Windows":
                self.__setFileHandler__()

    def __setFileHandler__(self, level=None):
        """
        set file handler
        :param level:
        :return:
        """
        file_name = os.path.join(LOG_PATH, '{name}.log'.format(name=self.name))
        # 按小时切割日志, 一小时一个文件, 保留168个(7天)
        # 切割后的备份文件命名为 name.log.YYYYMMDDHH(不带.log结尾, 避免混入WebUI日志列表)
        file_handler = TimedRotatingFileHandler(filename=file_name, when='H', interval=1, backupCount=168)
        file_handler.suffix = '%Y%m%d%H'
        file_handler.extMatch = re.compile(r'^\d{10}$')
        if not level:
            file_handler.setLevel(self.level)
        else:
            file_handler.setLevel(level)
        formatter = logging.Formatter('%(asctime)s %(filename)s[line:%(lineno)d] %(levelname)s %(message)s')

        file_handler.setFormatter(formatter)
        self.file_handler = file_handler
        self.addHandler(file_handler)

    def __setStreamHandler__(self, level=None):
        """
        set stream handler
        :param level:
        :return:
        """
        stream_handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s %(filename)s[line:%(lineno)d] %(levelname)s %(message)s')
        stream_handler.setFormatter(formatter)
        if not level:
            stream_handler.setLevel(self.level)
        else:
            stream_handler.setLevel(level)
        self.addHandler(stream_handler)


def listLogFiles():
    """
    列出 log 目录下所有日志文件名(不含扩展名), 供 WebUI 展示
    :return: list[str] 排序后的日志名列表
    """
    if not os.path.isdir(LOG_PATH):
        return []
    return sorted(filename[:-len('.log')] for filename in os.listdir(LOG_PATH)
                  if filename.endswith('.log'))


def readRecentLines(name, lines=200):
    """
    读取指定日志文件的尾部内容(供 WebUI 日志查看接口使用)
    :param name: 日志名(不含扩展名), 仅允许字母数字下划线
    :param lines: 返回的行数
    :return: (是否成功, 行列表)
    """
    if not re.match(r'^[A-Za-z0-9_]+$', name or ''):
        return False, []
    file_path = os.path.join(LOG_PATH, name + '.log')
    if not os.path.isfile(file_path):
        return False, []
    result = collections.deque(maxlen=max(1, lines))
    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            result.append(line.rstrip('\n'))
    return True, list(result)


if __name__ == '__main__':
    log = LogHandler('test')
    log.info('this is a test msg')