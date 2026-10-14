# -*- coding: utf-8 -*-
"""大盘汇总指标计算。

分两层：
- 纯函数层（本模块 compute_* / limit_threshold）：输入 DataFrame → 指标 dict，便于单测；
- IO 层（build_summary）：用 DuckDB 跨股读 parquet + index_store 读指数，拼装完整指标。

原则：数据缺失返回 None / 空 → 渲染层显示「数据缺失」，不造假。
"""
import math
from pathlib import Path

import pandas as pd

# 涨跌停幅度（近似口径，单位 %）
MAIN_BOARD = 10.0
STAR_GEM = 20.0
ST = 5.0
BEIJING = 30.0
TOL = 0.1   # 阈值容差：pctChg 达到 (阈值 ± 容差) 即判定

INDEX_NAMES = {
    "sh000001": "上证综指", "sz399001": "深证成指", "sz399006": "创业板指",
    "sh000688": "科创50", "sh000016": "上证50", "sh000300": "沪深300",
    "sh000905": "中证500", "sh000852": "中证1000",
}


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "1.0", "true", "yes")


def limit_threshold(code: str, is_st: bool) -> float:
    """涨跌停幅度（近似）：ST 5%，北交所 30%，科创/创业板 20%，其余 10%。"""
    if is_st:
        return ST
    c = str(code)
    market, num = (c[:2], c[2:]) if c[:2] in ("sh", "sz", "bj") else ("", c)
    if market == "bj" or num.startswith(("4", "8")):
        return BEIJING
    if num.startswith(("688", "689", "300", "301")):
        return STAR_GEM
    return MAIN_BOARD


def compute_breadth(df: pd.DataFrame) -> dict:
    """涨跌/平盘/涨停/跌停/停牌家数（剔停牌后统计涨跌）。"""
    cols = {"up": None, "down": None, "flat": None,
            "limit_up": None, "limit_down": None, "suspended": None, "total": 0}
    if df is None or df.empty:
        return cols

    st_num = pd.to_numeric(df.get("tradestatus"), errors="coerce")
    vol = pd.to_numeric(df.get("volume"), errors="coerce").fillna(0)
    suspended_mask = (st_num != 1) | (vol == 0)
    live = df[~suspended_mask]
    pct = pd.to_numeric(live["pctChg"], errors="coerce")

    limit_up = limit_down = 0
    for code, p, isst in zip(live["code"], pct, live.get("isST", pd.Series([0] * len(live)))):
        if p is None or (isinstance(p, float) and math.isnan(p)):
            continue
        th = limit_threshold(code, _truthy(isst))
        if p >= th - TOL:
            limit_up += 1
        elif p <= -(th - TOL):
            limit_down += 1

    return {
        "up": int((pct > 0).sum()),
        "down": int((pct < 0).sum()),
        "flat": int((pct == 0).sum()),
        "limit_up": int(limit_up),
        "limit_down": int(limit_down),
        "suspended": int(suspended_mask.sum()),
        "total": int(len(live)),
    }


def _num_sum(df: pd.DataFrame, col: str):
    if df is None or df.empty or col not in df.columns:
        return None
    s = pd.to_numeric(df[col], errors="coerce")
    return None if s.dropna().empty else float(s.sum())


def compute_volume(df: pd.DataFrame, prev_amount, ma5_amount) -> dict:
    """全市场成交额/量、平均换手率；对比前一日与近 5 日均值给出放量/缩量。"""
    if df is None or df.empty:
        return {"amount": None, "volume": None, "turn_avg": None,
                "vs_prev_pct": None, "vs_ma5_pct": None, "state": None}
    amount = _num_sum(df, "amount")
    volume = _num_sum(df, "volume")
    turn = pd.to_numeric(df.get("turn"), errors="coerce")
    turn_avg = None if turn.dropna().empty else float(turn.mean())

    vs_prev = (amount / prev_amount - 1) * 100 if (amount is not None and prev_amount) else None
    vs_ma5 = (amount / ma5_amount - 1) * 100 if (amount is not None and ma5_amount) else None
    state = None
    if vs_ma5 is not None:
        state = "放量" if vs_ma5 > 0 else "缩量"
    return {"amount": amount, "volume": volume, "turn_avg": turn_avg,
            "vs_prev_pct": vs_prev, "vs_ma5_pct": vs_ma5, "state": state}


def _percentile(series, value):
    """value 在 series 中的分位（%）：<= value 的比例。series 可含 None/NaN。"""
    if value is None:
        return None
    vals = [float(x) for x in (series or [])
            if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if not vals:
        return None
    return round(sum(1 for x in vals if x <= value) / len(vals) * 100, 2)


def compute_valuation(df: pd.DataFrame, hist_pe, hist_pb) -> dict:
    """PE/PB 中位数 + 近一年分位。"""
    if df is None or df.empty:
        return {"pe_median": None, "pb_median": None, "pe_pct_1y": None, "pb_pct_1y": None}
    pe = pd.to_numeric(df.get("peTTM"), errors="coerce")
    pb = pd.to_numeric(df.get("pbMRQ"), errors="coerce")
    pe_med = None if pe.dropna().empty else float(pe.median())
    pb_med = None if pb.dropna().empty else float(pb.median())
    return {"pe_median": pe_med, "pb_median": pb_med,
            "pe_pct_1y": _percentile(hist_pe, pe_med),
            "pb_pct_1y": _percentile(hist_pb, pb_med)}
