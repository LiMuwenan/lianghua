# -*- coding: utf-8 -*-
"""baostock 数据源。两个核心接口均为“按单日横扫全市场”。

适配说明（相对计划的关键事实）：
- baostock 0.9.4 的 ResultData.get_data() 内部使用已在新版 pandas 移除的 `df.append`，
  与 pandas 3.x 不兼容会导致崩溃；故改走 `next()/get_row_data()` 手工翻页取行，不依赖 get_data()。
- query_daily_adjust_factor(date) 返回的字段为 foreAdjustFactor/backAdjustFactor/adjustFactor，
  语义为“前复权因子/后复权因子/复权因子”；这里映射为计划 SCHEMA 内的 qfq_factor/hfq_factor。
  该接口仅返回当日发生除权除息(分红送转)的股票，股票当日无动作则不会出现在结果，由摄取侧默认计 1.0。
- query_daily_history_k_AStock(date) 返回 18 字段（含 isST/pctChg），均为当日全市场不复权日K。
"""
import datetime
import logging
from typing import Any, List

import baostock as bs
import pandas as pd

from .base import BaseStockDataSource, DataSourceError

logger = logging.getLogger("app.datasources.baostock")


class BaostockDataSource(BaseStockDataSource):
    name = "baostock"

    def connect(self) -> None:
        rs = bs.login()
        if rs.error_code != "0":
            raise DataSourceError(f"baostock 登录失败: {rs.error_msg}")

    def disconnect(self) -> None:
        try:
            bs.logout()
        except Exception:  # noqa: BLE001
            pass

    def _query(self, fn):
        """执行查询；遇到登录态失效(10001001/未登录)自动重登一次再试。

        baostock 会话有效期短，服务长跑后易失效；此处自愈，避免整次摄取因 0 条流水而空跑。
        """
        rs = fn()
        code = getattr(rs, "error_code", "0")
        msg = getattr(rs, "error_msg", "") or ""
        if code == "10001001" or "未登录" in msg:
            logger.warning("baostock 登录态失效(%s)，重新登录后重试", msg or code)
            r = bs.login()
            if r.error_code != "0":
                logger.warning("baostock 重新登录失败: %s", r.error_msg)
                return rs
            rs = fn()
            if getattr(rs, "error_code", "0") != "0":
                logger.warning("重登后查询仍失败(%s): %s", rs.error_code, rs.error_msg)
        return rs

    @staticmethod
    def _rows(result: Any, date: str) -> List[dict]:
        """按 next()/get_row_data() 手工翻页取全量行，返回 dict 列表。"""
        if result.error_code != "0":
            logger.warning("query 失败(%s,%s): %s", date, result.error_code, result.error_msg)
            return []
        fields = list(result.fields)
        if not fields:
            return []
        out = []
        while result.next():
            row = result.get_row_data()
            if row:
                out.append(dict(zip(fields, row)))
        return out

    def trade_dates(self, start_date: str, end_date: str) -> List[str]:
        # baostock 返回 calendar_date/is_trading_day 字段
        rs = self._query(lambda: bs.query_trade_dates(start_date=start_date, end_date=end_date))
        if getattr(rs, "error_code", "0") != "0":
            logger.warning("query trade_dates 失败(%s): %s", rs.error_code, rs.error_msg)
            return []
        fields = list(rs.fields)
        days = []
        while rs.next():
            d = dict(zip(fields, rs.get_row_data()))
            if str(d.get("is_trading_day")) == "1":
                days.append(str(d.get("calendar_date")))
        return [d for d in sorted(set(days)) if start_date <= d <= end_date]

    def daily_bars(self, date: str) -> List[dict]:
        rs = self._query(lambda: bs.query_daily_history_k_AStock(date=date))
        return self._rows(rs, date)

    def adjust_factors(self, date: str) -> List[dict]:
        rs = self._query(lambda: bs.query_daily_adjust_factor(date=date))
        rows = self._rows(rs, date)
        out = []
        for r in rows:
            try:
                out.append({
                    "date": date,
                    "code": str(r.get("code") or "").replace(".", ""),   # sh.600000 → sh600000（文件名不含点）
                    "qfq_factor": float(r.get("foreAdjustFactor") or r.get("qfq_factor") or 1.0),
                    "hfq_factor": float(r.get("backAdjustFactor") or r.get("hfq_factor") or 1.0),
                })
            except (TypeError, ValueError):
                continue
        return out

    def universe(self, date: str) -> List[str]:
        rs = self._query(lambda: bs.query_all_stock(day=date))
        rows = self._rows(rs, date)
        return [str(r["code"]).replace(".", "") for r in rows]   # sh.600000 → sh600000

    def stock_basics(self) -> List[dict]:
        """拉取 A 股基础信息 [{code, name, industry}]。

        用 query_all_stock 一次取得当日证券列表（含名称），再按沪深 A 股代码规则过滤
        掉指数等非个股；industry baostock 提供不了，置空（预留字段）。
        """
        from ..services.stock_service import is_a_stock, suggest_trade_day

        day = suggest_trade_day(self)
        rs = self._query(lambda: bs.query_all_stock(day=day))
        rows = self._rows(rs, day)
        out = []
        for r in rows:
            code = str(r.get("code") or "")
            if not is_a_stock(code):
                continue
            out.append({
                "code": code.replace(".", ""),               # sh.600000 → sh600000（文件名不含点）
                "name": str(r.get("code_name") or "").strip(),
                "industry": "",
            })
        return out