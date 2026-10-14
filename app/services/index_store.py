# -*- coding: utf-8 -*-
"""指数 Parquet 存储：按指数代码分文件，写/读/增量 merge/最新日。

与个股 parquet_store 分开：指数无复权/估值，仅约 10 列；
不进个股数据集扫描与 stock_freshness。
"""
from pathlib import Path

import pandas as pd

INDEX_COLS = ["date", "code", "open", "high", "low", "close",
              "preclose", "volume", "amount", "pctChg"]
NUMERIC_COLS = ["open", "high", "low", "close", "preclose", "volume", "amount", "pctChg"]


def normalize(code: str) -> str:
    """sh.000001 → sh000001（文件名不含点，与个股命名一致）。"""
    return str(code).replace(".", "").strip()


def index_path(root, code: str) -> Path:
    return Path(root) / f"{normalize(code)}.parquet"


def _cast(df: pd.DataFrame) -> pd.DataFrame:
    """数值列强制转数值，缺失列补空，裁到 INDEX_COLS 顺序。"""
    df = df.copy()
    for c in NUMERIC_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in INDEX_COLS:
        if c not in df.columns:
            df[c] = None
    if "date" in df.columns:
        df["date"] = df["date"].astype(str)
    return df[INDEX_COLS]


def write_index(root, code: str, df: pd.DataFrame) -> Path:
    """全量新建：按日期升序去重后落 {code}.parquet。"""
    df = _cast(df)
    df = df.sort_values("date").drop_duplicates("date", keep="last")
    p = index_path(root, code)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)
    return p


def read_index(root, code: str) -> pd.DataFrame:
    p = index_path(root, code)
    if not p.exists():
        return pd.DataFrame(columns=INDEX_COLS)
    return pd.read_parquet(p)


def merge_index(root, code: str, df: pd.DataFrame) -> pd.DataFrame:
    """增量合并：读旧 → concat 新 → 按日期去重升序 → 重写。"""
    base = read_index(root, code)
    new = _cast(df)
    merged = new if base.empty else pd.concat([_cast(base), new], ignore_index=True)
    merged = merged.sort_values("date").drop_duplicates("date", keep="last")
    write_index(root, code, merged)
    return merged


def latest_date(root, code: str):
    """返回已入库最新日期字符串，无文件/空文件返回 None。"""
    p = index_path(root, code)
    if not p.exists():
        return None
    df = pd.read_parquet(p, columns=["date"])
    if df.empty:
        return None
    return str(df["date"].max())


def list_codes(root) -> list:
    """列出目录内已有指数代码（文件名去点）。"""
    d = Path(root)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.parquet"))
