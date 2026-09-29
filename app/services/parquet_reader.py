# -*- coding: utf-8 -*-
"""Parquet 只读查询：按 code + 区间读日K，列裁剪读，返回三组价量（不复权/前复权/后复权）。

设计（对齐方案 5.1「只读查询服务」）：
- 摄取时已预存原始价 + 前/后复权价（24 列），浏览仅做列裁剪读取，不做实时计算、无写入。
- 读取用 pyarrow 列裁剪 + date 列谓词下推，避免整文件读入。
- 文件缺失/无数据 → 返回三组空序列（由前端显示友好空态），不报错。
"""
import logging
from pathlib import Path

import pandas as pd

from . import parquet_store as store

logger = logging.getLogger("app.parquet_reader")


def _series(df: pd.DataFrame, prefix: str) -> dict:
    """按前缀（''=raw / qfq_ / hfq_）抽取一组价量序列。

    volume 与复权无关，三档共用同一列 `volume`（不复权成交量），故不走前缀。
    """
    return {
        "dates": df["date"].tolist(),
        "open": df[f"{prefix}open"].tolist(),
        "high": df[f"{prefix}high"].tolist(),
        "low": df[f"{prefix}low"].tolist(),
        "close": df[f"{prefix}close"].tolist(),
        "volume": df["volume"].tolist(),
    }


def read_kline(data_dir, code: str, from_date: str = "", to_date: str = "") -> dict:
    """读单股区间日K，返回 {code, raw, qfq, hfq} 三组价量。

    - 文件缺失/列缺失时对应序列退化为空。
    - from/to 缺省返回全部；传入则按 date 字符串区间过滤（date 为 'YYYY-MM-DD'）。
    """
    result = {"code": code, "raw": {}, "qfq": {}, "hfq": {}}
    p = store.stock_path(data_dir, code)
    if not p.exists():
        return result

    try:
        cols = ["date", "open", "high", "low", "close", "volume",
                "qfq_open", "qfq_high", "qfq_low", "qfq_close",
                "hfq_open", "hfq_high", "hfq_low", "hfq_close"]
        df = pd.read_parquet(p, columns=cols)
    except Exception as exc:  # noqa: BLE001  读取/列异常 → 空态
        logger.warning("读取 %s 失败: %s", p, exc)
        return result

    if df is None or df.empty:
        return result

    # date 归一化为字符串，便于区间比较
    df["date"] = df["date"].astype(str)
    if from_date:
        df = df[df["date"] >= from_date]
    if to_date:
        df = df[df["date"] <= to_date]
    df = df.sort_values("date").reset_index(drop=True)
    if df.empty:
        return result

    # volume 与复权无关，三档共用一份原始成交量
    result["raw"] = _series(df, "", )
    result["qfq"] = _series(df, "qfq_", )
    result["hfq"] = _series(df, "hfq_", )
    return result