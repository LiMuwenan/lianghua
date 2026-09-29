# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "d:/Project/lianghua")

import pandas as pd
from app.services import parquet_reader, parquet_store as store


def _df_sh600000():
    """构造 600000 两日 24 列样本（前/后复权价 = 原始价 × 因子），便于只读断言。"""
    rows = []
    for date, close, qfq, hfq in [
        ("2024-01-02", 10.0, 1.5, 8.0),
        ("2024-01-05", 11.0, 1.5, 8.0),
    ]:
        rows.append({
            "date": date, "code": "sh600000",
            "open": close * 0.99, "high": close * 1.01, "low": close * 0.98, "close": close,
            "preclose": close * 0.99, "volume": 1_000_000, "amount": 10_000_000.0,
            "turn": 1.0, "pctChg": 1.0, "tradestatus": "1", "isST": "0",
            "peTTM": 15.0, "pbMRQ": 2.0, "psTTM": 1.0, "pcfNcfTTM": 5.0,
            "qfq_factor": qfq, "hfq_factor": hfq,
            "qfq_open": close * 0.99 * qfq, "qfq_high": close * 1.01 * qfq,
            "qfq_low": close * 0.98 * qfq, "qfq_close": close * qfq,
            "hfq_open": close * 0.99 * hfq, "hfq_high": close * 1.01 * hfq,
            "hfq_low": close * 0.98 * hfq, "hfq_close": close * hfq,
        })
    return pd.DataFrame(rows)[store.ALL_COLS]


def test_read_kline_three_series_and_shared_volume(tmp_path):
    """返回三组价量，volume 三档共用原始成交量，复权价为原始×因子。"""
    store.write_stock(tmp_path, "sh600000", _df_sh600000())
    out = parquet_reader.read_kline(tmp_path, "sh600000")
    assert out["code"] == "sh600000"
    assert out["raw"]["dates"] == ["2024-01-02", "2024-01-05"]
    assert out["raw"]["close"] == [10.0, 11.0]
    assert out["qfq"]["close"] == [15.0, 16.5]       # ×1.5
    assert out["hfq"]["close"] == [80.0, 88.0]       # ×8.0
    assert out["raw"]["volume"] == out["qfq"]["volume"] == out["hfq"]["volume"] == [1_000_000, 1_000_000]


def test_read_kline_range_filter(tmp_path):
    """按从/to 区间过滤，仅返回区间内日期。"""
    store.write_stock(tmp_path, "sh600000", _df_sh600000())
    out = parquet_reader.read_kline(tmp_path, "sh600000", from_date="2024-01-05", to_date="2024-12-31")
    assert out["raw"]["dates"] == ["2024-01-05"]
    assert out["raw"]["close"] == [11.0]


def test_read_kline_missing_file_returns_empty(tmp_path):
    """文件缺失 → 返回三组空序列，不报错。"""
    out = parquet_reader.read_kline(tmp_path, "zz999999")
    assert out["raw"] == {} and out["qfq"] == {} and out["hfq"] == {}