# -*- coding: utf-8 -*-
"""数据摄取编排：按交易日历横扫全市场，落 24 列 Parquet，支持协作式取消。

设计（对齐方案文档 5.1/6.1/9）：
- `run_full` / `run_incremental` 都以“交易日历逐日横扫”执行：
    每日先取不复权日K（daily_bars）+ 当日复权因子（adjust_factors），按 code join 后落 24 列。
- 全量：对空文件调用 write_stock（整段重建）；增量：对每股调用 merge_stock（读旧→concat→重写）。
- 协作式取消：`cancel_flag()` 返回 True 时在每日边界尽早退出，返回 status=aborted。
- 数据源通过模块级钩子 `_fetch_daily_bars/_fetch_factors/_trade_dates` 注入：
    默认空实现（便于测试以 fake 替换）；运行时由 API 接线层绑定到真实 BaostockDataSource 实例。
"""
import logging
from pathlib import Path
from typing import Callable, List

import pandas as pd

from . import parquet_store as store

logger = logging.getLogger("app.services.ingest")

# ---- 可被测试 monkeypatch 的内部钩子（运行时由 API 接线层绑定真实数据源） ----
_fetch_daily_bars: Callable = lambda date: []
_fetch_factors: Callable = lambda date: []
_trade_dates: Callable = lambda start, end: []


def compute_adjusted_prices(row: dict) -> dict:
    """由原始价 + 因子计算前/后复权 OHLC。"""
    q = row["qfq_factor"]
    h = row["hfq_factor"]
    return {
        "qfq_open": row["open"] * q, "qfq_high": row["high"] * q,
        "qfq_low": row["low"] * q, "qfq_close": row["close"] * q,
        "hfq_open": row["open"] * h, "hfq_high": row["high"] * h,
        "hfq_low": row["low"] * h, "hfq_close": row["close"] * h,
    }


def _row_to_stock_df(rows: List[dict]) -> pd.DataFrame:
    """把当日全市场 dict 行转成 24 列宽表（含因子与复权价）。"""
    records = []
    for r in rows:
        r2 = dict(r)
        # baostock 返回数字为字符串，先对参与复权计算的字段转数值
        for k in ("open", "high", "low", "close", "qfq_factor", "hfq_factor"):
            if k in r2 and r2[k] is not None:
                try:
                    r2[k] = float(r2[k])
                except (TypeError, ValueError):
                    pass
        adjust = compute_adjusted_prices(r2)
        records.append({**r2, **adjust})
    if not records:
        return pd.DataFrame(columns=store.ALL_COLS)
    df = pd.DataFrame(records)
    df["code"] = df["code"].astype(str).str.split(".").str[-1]  # sh/sz.600000 → 600000
    # baostock 返回数字为字符串，强制转数值，避免“字符串×因子”出错并保证 Parquet 类型统一
    for col in ["open", "high", "low", "close", "preclose", "volume", "amount",
                "turn", "pctChg", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM",
                "qfq_factor", "hfq_factor",
                "qfq_open", "qfq_high", "qfq_low", "qfq_close",
                "hfq_open", "hfq_high", "hfq_low", "hfq_close"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in store.ALL_COLS:
        if col not in df.columns:
            df[col] = None
    return df[store.ALL_COLS]


def _process_day(root, date: str) -> int:
    """单日处理：拉日K+因子、算复权价、按每股合并写库。返回写入行数。"""
    bars = _fetch_daily_bars(date)
    factors = {f["code"]: f for f in _fetch_factors(date)}
    for b in bars:
        code = str(b.get("code", "")).split(".")[-1]
        fac = factors.get(code) or {"qfq_factor": 1.0, "hfq_factor": 1.0}
        b["qfq_factor"], b["hfq_factor"] = fac["qfq_factor"], fac["hfq_factor"]
    df = _row_to_stock_df(bars)
    if df.empty:
        return 0
    for code, g in df.groupby("code"):
        store.merge_stock(root, code, g)
    return len(df)


def run_full(root: Path, dates: List[str],
             cancel_flag: Callable = lambda: False) -> dict:
    """全量：对 dates 逐日横扫，先清空重建每股文件。返回统计。"""
    root = Path(root)
    total = 0
    processed = 0
    for date in dates:
        if cancel_flag():
            return {"status": "aborted", "processed_dates": processed, "rows": total}
        bars = _fetch_daily_bars(date)
        factors = {f["code"]: f for f in _fetch_factors(date)}
        for b in bars:
            code = str(b.get("code", "")).split(".")[-1]
            fac = factors.get(code) or {"qfq_factor": 1.0, "hfq_factor": 1.0}
            b["qfq_factor"], b["hfq_factor"] = fac["qfq_factor"], fac["hfq_factor"]
        df = _row_to_stock_df(bars)
        if df.empty:
            continue
        processed += 1
        total += len(df)
        for code, g in df.groupby("code"):
            store.write_stock(root, code, g)
    return {"status": "success", "processed_dates": processed, "rows": total}


def run_incremental(root: Path, dates: List[str],
                    cancel_flag: Callable = lambda: False) -> dict:
    """增量：对 dates 逐日横扫，每股 merge 重写；cancel_flag() 为 True 时尽早退出。"""
    root = Path(root)
    processed = 0
    total = 0
    for date in dates:
        if cancel_flag():
            return {"status": "aborted", "processed_dates": processed, "rows": total}
        n = _process_day(root, date)
        processed += 1
        total += n
    return {"status": "success", "processed_dates": processed, "rows": total}


def bind_source(ds) -> None:
    """把真实数据源实例绑定为内部钩子（由 API 接线层调用）。"""
    global _fetch_daily_bars, _fetch_factors, _trade_dates
    _fetch_daily_bars = ds.daily_bars
    _fetch_factors = ds.adjust_factors
    _trade_dates = ds.trade_dates