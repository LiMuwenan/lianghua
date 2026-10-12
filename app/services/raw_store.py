# -*- coding: utf-8 -*-
"""原始层存储：按交易日落遗留脚本同格式 CSV（每日股票数据 / 因子数据）。

用途：审计与可重建。平台终态主数据仍是按股票分文件的 Parquet；本层为附加留存：
每个交易日一个 {date}.csv，UTF-8 BOM、带表头、已存在则跳过、
无数据写「仅表头空文件」作为已处理标记、先写临时文件再原子替换。
列口径对齐遗留脚本 script/获取全量数据-日分组.py 与 script/获取全量增量复权因子.py。
"""
import os
from pathlib import Path

import pandas as pd

DAILY_RAW_COLS = ["date", "code", "open", "high", "low", "close", "preclose",
                  "volume", "amount", "adjustflag", "turn", "tradestatus",
                  "pctChg", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM", "isST"]
FACTOR_RAW_COLS = ["code", "dividOperateDate", "foreAdjustFactor",
                   "backAdjustFactor", "adjustFactor"]


def daily_path(root, date: str) -> Path:
    return Path(root) / f"{date}.csv"


def factor_path(root, date: str) -> Path:
    return Path(root) / f"{date}.csv"


def has_daily(root, date: str) -> bool:
    """当日日K原始层文件是否已存在。"""
    return daily_path(root, date).exists()


def has_factor(root, date: str) -> bool:
    """当日因子原始层文件是否已存在。"""
    return factor_path(root, date).exists()


def _project(rows, cols: list) -> pd.DataFrame:
    """按目标列裁剪：缺列补空、多余列丢弃；rows 为空则返回仅表头的空表。"""
    records = [{c: r.get(c) for c in cols} for r in rows]
    return pd.DataFrame(records, columns=cols)


def _atomic_write_csv(p: Path, df: pd.DataFrame) -> None:
    """先写同目录临时文件再原子替换，避免中断留下半个文件。"""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, p)


def write_daily(root, date: str, bars) -> Path | None:
    """当日全市场不复权日K原样落 {date}.csv；已存在返回 None（跳过）。"""
    p = daily_path(root, date)
    if p.exists():
        return None
    _atomic_write_csv(p, _project(bars, DAILY_RAW_COLS))
    return p


def write_factor(root, date: str, factor_rows) -> Path | None:
    """当日复权因子事件原样落 {date}.csv；已存在返回 None（跳过）。

    字段回退：兼容仅含归一化字段的入参（code←raw_code or code、
    dividOperateDate←dividOperateDate or date、fore/back←fore/back or qfq/hfq）。
    """
    p = factor_path(root, date)
    if p.exists():
        return None
    rows = [{
        "code": r.get("raw_code") or r.get("code"),
        "dividOperateDate": r.get("dividOperateDate") or r.get("date"),
        "foreAdjustFactor": r.get("foreAdjustFactor", r.get("qfq_factor")),
        "backAdjustFactor": r.get("backAdjustFactor", r.get("hfq_factor")),
        "adjustFactor": r.get("adjustFactor"),
    } for r in factor_rows]
    _atomic_write_csv(p, _project(rows, FACTOR_RAW_COLS))
    return p