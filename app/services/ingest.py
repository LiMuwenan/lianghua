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
    df["code"] = df["code"].astype(str).str.replace(".", "", regex=False)   # sh.600000 → sh600000
    # baostock 返回数字为字符串，强制转数值，避免“字符串×因子”出错并保证 Parquet 类型统一
    for col in ["open", "high", "low", "close", "preclose", "volume", "amount",
                "turn", "pctChg", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM",
                "qfq_factor", "hfq_factor",
                "qfq_open", "qfq_high", "qfq_low", "qfq_close",
                "hfq_open", "hfq_high", "hfq_low", "hfq_close",
                # 整型标记列同样来自 baostock 字符串，需转数值以与既有 parquet 类型一致，
                # 否则 merge 时 str 与 int 混成 object，写 parquet 会 ArrowInvalid
                "adjustflag", "tradestatus", "isST"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in store.ALL_COLS:
        if col not in df.columns:
            df[col] = None
    return df[store.ALL_COLS]


def run_fetch(root, dates: List[str],
              cancel_flag: Callable = lambda: False,
              freshness: dict | None = None,
              initial_factors: dict | None = None) -> dict:
    """统一「获取数据」：对 dates 逐日横扫全市场，按股票新建或追加写 Parquet。

    - `freshness`: {code: latest_date(str|None)}，每股断点位点（来自元库 stock_freshness）。
      - 某股已有日期（date ≤ latest）→ 该股当日跳过不重写；
      - 若某一日所有已知股票均已包含（date ≤ 所有 latest 的最小值）→ 整日跳过，不请求数据源。
      返回 {"status", "processed_dates", "skipped_dates", "rows"}。
    - `initial_factors`: {code: {"qfq_factor":..,"hfq_factor":..}}，各股起始因子
      （增量续传时取该股既有 parquet 末行，衔接历史前向填充；缺省 1.0）。
    - 因子口径：除权日（当日有 factor 事件）更新该股因子；非除权日沿用最近一次。
    - `cancel_flag()` 返回 True 时在每日边界尽早退出，返回 status=aborted。
    """
    root = Path(root)
    for k, v in (freshness or {}).items():
        freshness[k] = _iso(v)  # 原位规范化，调用方可见每股最新位点
    total = 0
    processed = 0
    skipped = 0
    min_done = _global_min(freshness)  # 所有已知股票 latest 的最小值，无则 None
    last_factor = dict(initial_factors or {})  # {code: {"qfq_factor":..,"hfq_factor":..}}
    for date in dates:
        if cancel_flag():
            return {"status": "aborted", "processed_dates": processed,
                    "skipped_dates": skipped, "rows": total}
        if min_done is not None and date <= min_done:
            skipped += 1
            continue
        bars = _fetch_daily_bars(date)
        factors = {f["code"]: f for f in _fetch_factors(date)}
        for b in bars:
            code = str(b.get("code", "")).replace(".", "")   # sh.600000 → sh600000（文件名不含点）
            if code in factors:
                # 除权日：更新该股最近因子
                last_factor[code] = {"qfq_factor": factors[code]["qfq_factor"],
                                     "hfq_factor": factors[code]["hfq_factor"]}
            fac = last_factor.get(code) or {"qfq_factor": 1.0, "hfq_factor": 1.0}
            b["qfq_factor"], b["hfq_factor"] = fac["qfq_factor"], fac["hfq_factor"]
        df = _row_to_stock_df(bars)
        if df.empty:
            continue
        processed += 1
        total += len(df)
        for code, g in df.groupby("code"):
            latest = freshness.get(code)
            if latest is not None and date <= latest:
                continue  # 该股已有该日期，不重复写
            # 无文件则整段新建，有则读旧→concat→去重重写（追加）
            if store.stock_path(root, code).exists():
                store.merge_stock(root, code, g)
            else:
                store.write_stock(root, code, g)
            if latest is None or date > latest:
                freshness[code] = date
    return {"status": "success", "processed_dates": processed,
            "skipped_dates": skipped, "rows": total}


def _iso(v):
    """把 date/datetime 或 iso 字符串规范化为 'YYYY-MM-DD'，None 原样返回。"""
    if v is None:
        return None
    s = str(v)
    if len(s) < 10:
        return s
    return s[:10]


def _global_min(freshness: dict):
    """所有已知股票 latest 的最小值；无任何已知则返回 None。"""
    vals = [v for v in freshness.values() if v]
    return min(vals) if vals else None


def bind_source(ds) -> None:
    """把真实数据源实例绑定为内部钩子（由 API 接线层调用）。"""
    global _fetch_daily_bars, _fetch_factors, _trade_dates
    _fetch_daily_bars = ds.daily_bars
    _fetch_factors = ds.adjust_factors
    _trade_dates = ds.trade_dates