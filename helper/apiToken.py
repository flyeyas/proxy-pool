# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     apiToken.py
   Description :   API Token 管理: 供外部程序直接调用代理接口的授权凭证,
                   在 WebUI 后台生成, 可设置有效期, 存于 runtime_config.json
   Author :        flyeyas
   date：          2026/10/25
-------------------------------------------------
   Change Activity:
                   2026/10/25: API Token 生成/验证/删除
-------------------------------------------------
"""
__author__ = 'flyeyas'

import secrets
from datetime import datetime, timedelta

from helper.runtimeConfig import readConfig, writeConfig

TOKENS_KEY = 'api_tokens'

_TIME_FORMAT = '%Y-%m-%d %H:%M:%S'


def listTokens():
    """全部 token 列表(含已过期, 由前端标注状态)"""
    return readConfig().get(TOKENS_KEY, [])


def generateToken(name='', expire_days=0):
    """
    生成 token
    :param name: 备注名
    :param expire_days: 有效期(天), 0 表示永久
    :return: dict token 信息
    """
    token = 'pp_' + secrets.token_urlsafe(24)
    now = datetime.now()
    expires_at = ''
    if expire_days:
        expires_at = (now + timedelta(days=expire_days)).strftime(_TIME_FORMAT)
    item = {
        'token': token,
        'name': name or '未命名',
        'created_at': now.strftime(_TIME_FORMAT),
        'expires_at': expires_at,
    }
    config = readConfig()
    tokens = config.get(TOKENS_KEY, [])
    tokens.append(item)
    config[TOKENS_KEY] = tokens
    writeConfig(config)
    return item


def deleteToken(token):
    """
    删除指定 token
    :return: bool 是否删除了记录
    """
    config = readConfig()
    tokens = config.get(TOKENS_KEY, [])
    remain = [item for item in tokens if item.get('token') != token]
    config[TOKENS_KEY] = remain
    writeConfig(config)
    return len(remain) != len(tokens)


def verifyApiToken(token):
    """校验 token: 存在且未过期"""
    if not token:
        return False
    for item in readConfig().get(TOKENS_KEY, []):
        if secrets.compare_digest(item.get('token', ''), token):
            expires_at = item.get('expires_at', '')
            if not expires_at:
                return True
            try:
                return datetime.strptime(expires_at, _TIME_FORMAT) > datetime.now()
            except ValueError:
                return False
    return False
