# tests/test_csv_import.py
# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "d:/Project/lianghua")

import pandas as pd
import pytest

from app.services import csv_import


def _write_bom_csv(path, rows, columns):
    df = pd.DataFrame(rows, columns=columns)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def test_read_factors_strips_prefix_and_skips_empty(tmp_path):
    """因子目录：解析各日分片，code 去前缀，空文件跳过，事件按日期升序。"""
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
    assert set(events) == {"600000", "000001"}
    assert events["600000"] == [("2024-01-02", 0.5, 2.0), ("2024-06-01", 1.0, 4.0)]
    assert events["000001"] == [("2024-01-02", 0.9, 1.5)]


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
