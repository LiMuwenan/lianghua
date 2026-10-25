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
import time
from pathlib import Path
from typing import Callable, List

import pandas as pd

from . import parquet_store as store
from . import raw_store

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


def _num(v) -> float:
    """baostock 数值字段转 float；空串/None/非法值一律转 NaN。

    不能静默保留原始字符串：字符串乘复权因子会抛
    TypeError(can't multiply sequence by non-int of type 'float')，
    单只股票的脏数据就会中断整次多日摄取。
    """
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def _row_to_stock_df(rows: List[dict]) -> pd.DataFrame:
    """把当日全市场 dict 行转成 24 列宽表（含因子与复权价）。"""
    records = []
    blank = 0
    for r in rows:
        r2 = dict(r)
        # baostock 返回数字为字符串，先对参与复权计算的字段转数值（失败转 NaN）
        for k in ("open", "high", "low", "close", "qfq_factor", "hfq_factor"):
            if k in r2:
                r2[k] = _num(r2[k])
        if any(pd.isna(r2.get(k)) for k in ("open", "high", "low", "close")):
            blank += 1
        adjust = compute_adjusted_prices(r2)
        records.append({**r2, **adjust})
    if blank:
        logger.warning("日K 有 %d 行价格为空/非数值，已按 NaN 落表（不复权价同为空）", blank)
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
              initial_factors: dict | None = None,
              log: Callable[[str], None] | None = None,
              daily_dir=None, factor_dir=None) -> dict:
    """统一「获取数据」：对 dates 逐日横扫全市场，按股票新建或追加写 Parquet。

    除终态 Parquet 外，`daily_dir`/`factor_dir` 提供时另落两层原始 CSV
    （审计/可重建）：{dir}/{date}.csv，UTF-8 BOM、带表头、已存在跳过、
    无数据写仅表头空文件；写失败仅记日志，不阻断主链路。
    `freshness` 某股已有日期（date ≤ latest）→ 该股当日跳过不重写；
    整日跳过条件 = 断点已覆盖该日 **且** 原始层两文件都已存在（原始层关闭时退化为原行为）。
    `initial_factors` 为各股起始因子（增量续传时取既有 parquet 末行）。
    `cancel_flag()` 返回 True 时在每日边界尽早退出，返回 status=aborted。
    """
    root = Path(root)
    for k, v in (freshness or {}).items():
        freshness[k] = _iso(v)  # 原位规范化，调用方可见每股最新位点

    def _log(msg: str) -> None:
        """进度日志：既进任务日志文件（log 回调，由调用方写 task_{id}.log），也留控制台。"""
        if log:
            log(msg)
        logger.info(msg)

    total = 0
    processed = 0
    skipped = 0
    n_days = len(dates)
    t_start = time.time()
    min_done = _global_min(freshness)  # 所有已知股票 latest 的最小值，无则 None
    last_factor = dict(initial_factors or {})  # {code: {"qfq_factor":..,"hfq_factor":..}}
    _log(f"开始获取数据：共 {n_days} 个交易日，min_done={min_done}")
    for idx, date in enumerate(dates, 1):
        if cancel_flag():
            _log(f"第 {idx}/{n_days} 日 {date} 前收到取消，提前退出")
            return {"status": "aborted", "processed_dates": processed,
                    "skipped_dates": skipped, "rows": total}
        # 整日跳过：断点已覆盖该日，且（若启用原始层）两层原始文件都已存在
        raw_ready = ((not daily_dir or raw_store.has_daily(daily_dir, date))
                     and (not factor_dir or raw_store.has_factor(factor_dir, date)))
        if min_done is not None and date <= min_done and raw_ready:
            skipped += 1
            continue
        day_start = time.time()
        bars = _fetch_daily_bars(date)
        _n_bars = len(bars)
        factor_rows = _fetch_factors(date)
        factors = {f["code"]: f for f in factor_rows}
        for b in bars:
            code = str(b.get("code", "")).replace(".", "")   # sh.600000 → sh600000（文件名不含点）
            if code in factors:
                # 除权日：更新该股最近因子
                last_factor[code] = {"qfq_factor": factors[code]["qfq_factor"],
                                     "hfq_factor": factors[code]["hfq_factor"]}
            fac = last_factor.get(code) or {"qfq_factor": 1.0, "hfq_factor": 1.0}
            b["qfq_factor"], b["hfq_factor"] = fac["qfq_factor"], fac["hfq_factor"]
        df = _row_to_stock_df(bars)
        # 原始层留存（审计/可重建）：写失败不阻断主链路
        if daily_dir:
            try:
                raw_store.write_daily(daily_dir, date, bars)
            except Exception as exc:  # noqa: BLE001
                _log(f"  {date} 原始层日K写盘失败（不阻断）：{exc}")
        if factor_dir:
            try:
                raw_store.write_factor(factor_dir, date, factor_rows)
            except Exception as exc:  # noqa: BLE001
                _log(f"  {date} 原始层因子写盘失败（不阻断）：{exc}")
        _log(f"进度 {idx:3d}/{n_days}  获取数据源 {date}：日K {_n_bars} 条，因子 {len(factors)} 条"
             f"（耗时 {time.time() - day_start:.1f}s）")
        if df.empty:
            continue
        processed += 1
        total += len(df)
        _wrote = 0
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
            _wrote += 1
        _log(f"  —— {date} 写入 {_wrote} 只股票 {len(g)} 行（耗时 {time.time() - day_start:.1f}s）")
    total_sec = time.time() - t_start
    _log(f"结束获取数据：处理 {processed}/{n_days} 日，跳过 {skipped} 日，共写 {total} 行，"
         f"总耗时 {total_sec:.1f}s")
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