# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     response.py
   Description :   API 统一响应格式: {"code": 同HTTP状态码, "message": ..., "data": ...}
   Author :        flyeyas
   date：          2026/10/25
-------------------------------------------------
   Change Activity:
                   2026/10/25: 从 webuiApi 抽出为独立模块
-------------------------------------------------
"""
__author__ = 'flyeyas'


def apiOk(data=None, message="ok", status=200):
    """统一成功响应: {"code": 同HTTP状态码, "message": ..., "data": ...}"""
    return {"code": status, "message": message, "data": data}, status


def apiError(status, message):
    """统一错误响应: {"code": 同HTTP状态码, "message": ..., "data": null}"""
    return {"code": status, "message": message, "data": None}, status
