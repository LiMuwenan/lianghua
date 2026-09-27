# app/services/csv_import.py
# -*- coding: utf-8 -*-
"""历史 CSV → 平台 24 列 Parquet 的一次性转换逻辑。

输入为 baostock 脚本产出的按日分片 CSV（UTF-8 BOM）：
- 日K：date,code,open,...,isST（18 列，code 带 sh./sz. 前缀，非交易日为空文件）
- 复权因子：code,dividOperateDate,foreAdjustFactor,backAdjustFactor,adjustFactor
输出为平台标准 24 列 {code}.parquet（复用 parquet_store.write_stock）。
复权口径（baostock 标准）：前/后复权价 = 原始价 × 对应因子；
因子仅在除权除息日变化，其余交易日沿用最近一次（前向填充），无事件=1.0。
"""
from collections import defaultdict
from pathlib import Path

import pandas as pd

from . import parquet_store as store

# 与既有抓取脚本一致的 dtype 优化（控制 ~3GB 峰值内存）
_FLOAT_COLS = ["open", "high", "low", "close", "preclose", "volume", "amount",
               "turn", "pctChg", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM"]
_INT_COLS = ["adjustflag", "tradestatus", "isST"]
DAILY_DTYPES = {"code": "category", "date": "category"}
DAILY_DTYPES.update({c: "float32" for c in _FLOAT_COLS})
DAILY_DTYPES.update({c: "int32" for c in _INT_COLS})

FACTOR_COLS = ["code", "dividOperateDate", "foreAdjustFactor",
               "backAdjustFactor", "adjustFactor"]


def read_factors(factor_dir) -> dict:
    """读全部因子日分片，返回 {code: [(date, fore, back), ...]}（按日期升序）。"""
    events: dict[str, list] = defaultdict(list)
    for p in sorted(Path(factor_dir).glob("*.csv")):
        try:
            df = pd.read_csv(p, encoding="utf-8-sig",
                             dtype={"foreAdjustFactor": "float64",
                                    "backAdjustFactor": "float64"})
        except Exception:  # noqa: BLE001  单文件损坏不影响整体
            continue
        if df.empty:
            continue
        for _, r in df.iterrows():
            code = str(r["code"]).split(".")[-1]  # sh.600000 → 600000
            events[code].append((str(r["dividOperateDate"]),
                                 float(r["foreAdjustFactor"]),
                                 float(r["backAdjustFactor"])))
    for code in events:
        events[code].sort()
    return dict(events)


def _fill_factors(dates, events):
    """给定交易日序列与该股事件表，前向填充返回 (qfq列表, hfq列表)。

    事件日当天生效；其后的交易日沿用最近一次因子；事件之前的日期=1.0。
    """
    qfq, hfq = [], []
    i, n = 0, len(events)
    cur_f = cur_b = 1.0
    for d in dates:
        while i < n and events[i][0] <= d:
            cur_f, cur_b = events[i][1], events[i][2]
            i += 1
        qfq.append(cur_f)
        hfq.append(cur_b)
    return qfq, hfq
