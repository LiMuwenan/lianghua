# -*- coding: utf-8 -*-
"""数据源抽象接口：平台主数据统一通过本接口抓取，便于替换数据源。"""
from typing import List, Optional


class DataSourceError(RuntimeError):
    """数据源调用失败。"""


class BaseStockDataSource:
    name = "base"

    def connect(self) -> None:
        raise NotImplementedError

    def disconnect(self) -> None:
        raise NotImplementedError

    def trade_dates(self, start_date: str, end_date: str) -> List[str]:
        """返回 [start,end] 内的交易日字符串列表(YYYY-MM-DD)。"""
        raise NotImplementedError

    def daily_bars(self, date: str) -> List[dict]:
        """返回某日全市场不复权日K的 dict 列表。"""
        raise NotImplementedError

    def adjust_factors(self, date: str) -> List[dict]:
        """返回某日全市场复权因子 dict 列表(含 code/qfq_factor/hfq_factor)。"""
        raise NotImplementedError

    def universe(self, date: str) -> List[str]:
        """返回截至某日的市场股票代码列表(用于期望覆盖数)。"""
        raise NotImplementedError

    def stock_basics(self) -> List[dict]:
        """返回当前 A 股基础信息 [{code, name, industry}]，industry 可空。"""
        raise NotImplementedError