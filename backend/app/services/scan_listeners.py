# -*- coding: utf-8 -*-
"""Scan Listeners — v7.0.10 临时占位 (v2 投产 5 步工程).

目的: 让 main.py 启动, 任何 scan 事件回调不报错.
v7.0.32 修复: 改用 register_handlers (与 main.py 一致).
"""
import logging

logger = logging.getLogger("scan_listeners_placeholder")


def register_handlers():
    """v7.0.32: 与 main.py 调用的函数名保持一致.

    占位: 无 listener 注册, 避免启动失败.
    """
    logger.info("scan_listeners: no-op (v2 投产占位)")
    return


# 向后兼容: 旧名 register_all_listeners 也保留
register_all_listeners = register_handlers
