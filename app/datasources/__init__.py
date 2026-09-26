# -*- coding: utf-8 -*-
"""数据源包：抽象接口 + 各数据源实现。"""
from .base import BaseStockDataSource, DataSourceError

__all__ = ["BaseStockDataSource", "DataSourceError"]