# tests/test_csv_import.py
# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "d:/Project/lianghua")

import pandas as pd
import pytest

from app.services import csv_import
from app.services import parquet_store as store
from app.services.csv_import import FACTOR_COLS


def _write_bom_csv(path, rows, columns):
    df = pd.DataFrame(rows, columns=columns)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def test_read_factors_preserves_prefix_and_skips_empty(tmp_path):
    """因子目录：解析各日分片，code 保留市场前缀(去点)，空文件跳过，事件按日期升序。"""
    d = tmp_path / "factors"
    d.mkdir()
    cols = ["code", "dividOperateDate", "foreAdjustFactor", "backAdjustFactor", "adjustFactor"]
    _write_bom_csv(d / "2024-01-02.csv", [
        ["sh.600000", "2024-01-02", 0.5, 2.0, 2.0],
        ["sz.000001", "2024-01-02", 0.9, 1.5, 1.5],
    ], cols)
    _write_bom_csv(d / "2024-06-01.csv", [
        ["sh.600000", "2024-06-01", 1.0, 4.0, 4.0],
    ], cols)
    _write_bom_csv(d / "2024-06-02.csv", [], cols)  # 空文件（无除权日）

    events = csv_import.read_factors(str(d))
    assert set(events) == {"sh600000", "sz000001"}
    assert events["sh600000"] == [("2024-01-02", 0.5, 2.0), ("2024-06-01", 1.0, 4.0)]
    assert events["sz000001"] == [("2024-01-02", 0.9, 1.5)]


def test_fill_factors_forward_fill(tmp_path):
    """前向填充：事件日当天生效，其后沿用；事件前与无事件=1.0。"""
    events = [("2024-01-05", 0.5, 2.0), ("2024-02-01", 1.0, 4.0)]
    dates = ["2024-01-02", "2024-01-05", "2024-01-08", "2024-02-01", "2024-02-05"]
    qfq, hfq = csv_import._fill_factors(dates, events)
    assert qfq == [1.0, 0.5, 0.5, 1.0, 1.0]
    assert hfq == [1.0, 2.0, 2.0, 4.0, 4.0]
    # 无任何事件 → 全程 1.0
    qfq2, hfq2 = csv_import._fill_factors(dates, [])
    assert qfq2 == [1.0] * 5 and hfq2 == [1.0] * 5


DAILY_COLS = ["date", "code", "open", "high", "low", "close", "preclose",
              "volume", "amount", "adjustflag", "turn", "tradestatus",
              "pctChg", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM", "isST"]


def test_convert_end_to_end(tmp_path):
    """端到端：日K分片+因子分片 → 24 列 parquet，复权价与前向填充正确。"""
    daily = tmp_path / "daily"
    daily.mkdir()
    factors = tmp_path / "factors"
    factors.mkdir()
    out = tmp_path / "out"

    # 日K：d1（两只股票）、d2（一只，除权日）、d3（空文件=非交易日）
    _write_bom_csv(daily / "2024-01-02.csv", [
        ["2024-01-02", "sh.600000", 10, 10.5, 9.8, 10.2, 10, 100, 1000, 3, 1.0, 1, 2.0, 10, 1, 1, 1, 0],
        ["2024-01-02", "sz.000001", 5, 5.2, 4.9, 5.1, 5, 200, 2000, 3, 0.5, 1, 1.0, 20, 2, 2, 2, 0],
    ], DAILY_COLS)
    _write_bom_csv(daily / "2024-01-05.csv", [
        ["2024-01-05", "sh.600000", 11, 11.5, 10.8, 11.2, 10.2, 150, 1500, 3, 1.2, 1, 2.5, 11, 1, 1, 1, 0],
    ], DAILY_COLS)
    _write_bom_csv(daily / "2024-01-06.csv", [], DAILY_COLS)  # 非交易日

    # 因子：d1 无事件；d2(2024-01-05) 600000 除权
    _write_bom_csv(factors / "2024-01-05.csv", [
        ["sh.600000", "2024-01-05", 0.5, 2.0, 2.0],
    ], FACTOR_COLS)

    stats = csv_import.convert(str(daily), str(factors), str(out))

    assert stats["stocks"] == 2
    assert stats["rows"] == 3  # sh600000 两行 + sz000001 一行
    df = store.read_stock(out, "sh600000")
    assert len(df) == 2
    assert list(df.columns) == store.ALL_COLS
    # 除权日前(无事件)：因子 1.0，复权价=原始价
    r1 = df.iloc[0]
    assert r1["qfq_factor"] == 1.0 and r1["qfq_close"] == 10.2
    # 除权日及之后：沿用事件因子 0.5/2.0
    r2 = df.iloc[1]
    assert r2["qfq_factor"] == 0.5 and r2["hfq_factor"] == 2.0
    assert r2["qfq_close"] == pytest.approx(11.2 * 0.5)
    assert r2["hfq_close"] == pytest.approx(11.2 * 2.0)
    # sz000001 无事件：全程 1.0，行数 1
    df2 = store.read_stock(out, "sz000001")
    assert len(df2) == 1 and df2.iloc[0]["qfq_factor"] == 1.0
