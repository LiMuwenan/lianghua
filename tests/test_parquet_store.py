# -*- coding: utf-8 -*-
import sys
from pathlib import Path
sys.path.insert(0, "d:/Project/lianghua")

import pandas as pd
from app.services import parquet_store as store


SCHEMA_24 = [
    "date","code","open","high","low","close","preclose","volume","amount",
    "turn","pctChg","tradestatus","isST","peTTM","pbMRQ","psTTM","pcfNcfTTM",
    "qfq_factor","hfq_factor",
    "qfq_open","qfq_high","qfq_low","qfq_close",
    "hfq_open","hfq_high","hfq_low","hfq_close",
]


def make_row(code="000001", date="2024-01-02", close=10.0, qfq=1.5, hfq=8.0):
    price = {"open": close * 0.99, "high": close * 1.01, "low": close * 0.98, "close": close}
    row = {
        "date": date, "code": code,
        "open": price["open"], "high": price["high"], "low": price["low"], "close": price["close"],
        "preclose": close * 0.99, "volume": 1_000_000, "amount": 10_000_000.0,
        "turn": 1.2, "pctChg": 1.0, "tradestatus": "1", "isST": "0",
        "peTTM": 15.0, "pbMRQ": 2.0, "psTTM": 1.0, "pcfNcfTTM": 5.0,
        "qfq_factor": qfq, "hfq_factor": hfq,
        "qfq_open": price["open"] * qfq, "qfq_high": price["high"] * qfq,
        "qfq_low": price["low"] * qfq, "qfq_close": close * qfq,
        "hfq_open": price["open"] * hfq, "hfq_high": price["high"] * hfq,
        "hfq_low": price["low"] * hfq, "hfq_close": close * hfq,
    }
    df = pd.DataFrame([row])
    # 保证列顺序即为 24 列
    df = df[SCHEMA_24]
    return df


def test_write_and_read(tmp_path):
    code = "000001"
    df = make_row()
    p = store.write_stock(tmp_path, code, df)
    assert p.name == f"{code}.parquet" and p.exists()
    back = store.read_stock(tmp_path, code)
    assert list(back.columns) == SCHEMA_24, list(back.columns)
    assert len(back) == 1 and back.iloc[0]["date"] == "2024-01-02"


def test_merge_incremental(tmp_path):
    code = "000001"
    store.write_stock(tmp_path, code, make_row(code, date="2024-01-02", close=10.0, qfq=1.5))
    new1 = make_row(code, date="2024-01-05", close=10.5, qfq=1.5)
    merged = store.merge_stock(tmp_path, code, new1)
    assert len(merged) == 2
    assert list(merged["date"]) == ["2024-01-02", "2024-01-05"]  # 按日期升序去重
    assert store.freshness(tmp_path, code) == ("2024-01-05", 2)


def test_freshness_empty(tmp_path):
    assert store.freshness(tmp_path, "999999") == (None, 0)