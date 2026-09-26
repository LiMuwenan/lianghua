# -*- coding: utf-8 -*-
"""Parquet 存储层：按股票分文件，写/读/增量 merge/断点位点。"""
from pathlib import Path

import pandas as pd

DATA_COLS = ["date", "code", "open", "high", "low", "close", "preclose",
             "volume", "amount", "turn", "pctChg", "tradestatus", "isST",
             "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM"]
FACTOR_COLS = ["qfq_factor", "hfq_factor"]
QFQ_PRICE = ["qfq_open", "qfq_high", "qfq_low", "qfq_close"]
HFQ_PRICE = ["hfq_open", "hfq_high", "hfq_low", "hfq_close"]
ALL_COLS = DATA_COLS + FACTOR_COLS + QFQ_PRICE + HFQ_PRICE  # 24 列


def stock_path(root, code: str) -> Path:
    return Path(root) / f"{code}.parquet"


def write_stock(root, code: str, df: pd.DataFrame) -> Path:
    """全量新建：将 24 列 df 落为 {code}.parquet，按日期升序。"""
    df = df[ALL_COLS].copy()
    df = df.sort_values("date").drop_duplicates("date", keep="last")
    p = stock_path(root, code)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)
    return p


def read_stock(root, code: str) -> pd.DataFrame:
    p = stock_path(root, code)
    if not p.exists():
        return pd.DataFrame(columns=ALL_COLS)
    return pd.read_parquet(p)


def merge_stock(root, code: str, df: pd.DataFrame) -> pd.DataFrame:
    """增量合并：读旧 → concat 新 → 按日期去重升序 → 重写，返回合并结果。"""
    base = read_stock(root, code)
    new = df[ALL_COLS].copy()
    merged = pd.concat([base, new], ignore_index=True)
    merged = merged.sort_values("date").drop_duplicates("date", keep="last")
    write_stock(root, code, merged)
    return merged


def freshness(root, code: str):
    """返回 (latest_date_str_or_None, row_count)。"""
    p = stock_path(root, code)
    if not p.exists():
        return None, 0
    df = pd.read_parquet(p, columns=["date"])
    if df.empty:
        return None, 0
    return str(df["date"].max()), int(len(df))