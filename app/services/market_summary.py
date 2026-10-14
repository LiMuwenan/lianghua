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
