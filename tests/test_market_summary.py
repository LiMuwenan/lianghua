# -*- coding: utf-8 -*-
"""大盘汇总指标：宽度/涨跌停阈值/量能/估值/行业/热度（纯函数）。"""
import pandas as pd

from app.services import market_summary as ms


def test_limit_threshold_by_board():
    assert ms.limit_threshold("sh600000", False) == 10.0
    assert ms.limit_threshold("sz300750", False) == 20.0
    assert ms.limit_threshold("sh688001", False) == 20.0
    assert ms.limit_threshold("bj430047", False) == 30.0
    assert ms.limit_threshold("sh600000", True) == 5.0


def _cross(rows):
    return pd.DataFrame(rows, columns=[
        "code", "pctChg", "tradestatus", "volume", "amount", "turn",
        "peTTM", "pbMRQ", "isST"])


def test_compute_breadth_counts():
    df = _cross([
        ("sh600000", 10.02, 1, 100, 1e6, 1.0, 10.0, 1.0, 0),   # 涨停
        ("sh600001", -10.03, 1, 100, 1e6, 1.0, 10.0, 1.0, 0),  # 跌停
        ("sz300750", 20.0, 1, 100, 1e6, 1.0, 10.0, 1.0, 0),    # 创业涨停
        ("sz000001", 2.0, 1, 100, 1e6, 1.0, 10.0, 1.0, 0),     # 上涨
        ("sz000002", -1.0, 1, 100, 1e6, 1.0, 10.0, 1.0, 0),    # 下跌
        ("sz000003", 0.0, 1, 100, 1e6, 1.0, 10.0, 1.0, 0),     # 平盘
        ("sh600002", 1.0, 0, 0, 0.0, 0.0, 10.0, 1.0, 0),       # 停牌 tradestatus!=1
    ])
    b = ms.compute_breadth(df)
    assert b["up"] == 3 and b["down"] == 2 and b["flat"] == 1
    assert b["limit_up"] == 2 and b["limit_down"] == 1
    assert b["suspended"] == 1
    assert b["total"] == 6


def test_compute_breadth_empty_is_all_none():
    b = ms.compute_breadth(pd.DataFrame(columns=["code", "pctChg"]))
    assert b["up"] is None and b["total"] == 0


def test_compute_volume_and_state():
    df = _cross([
        ("sh600000", 1.0, 1, 100, 300.0, 2.0, 10.0, 1.0, 0),
        ("sz000001", 1.0, 1, 200, 200.0, 4.0, 10.0, 1.0, 0),
    ])
    v = ms.compute_volume(df, prev_amount=400.0, ma5_amount=400.0)
    assert v["amount"] == 500.0 and v["volume"] == 300.0
    assert abs(v["turn_avg"] - 3.0) < 1e-9
    assert abs(v["vs_prev_pct"] - 25.0) < 1e-9
    assert v["state"] == "放量"

    v2 = ms.compute_volume(df, prev_amount=600.0, ma5_amount=600.0)
    assert v2["state"] == "缩量"


def test_compute_valuation_percentile():
    df = _cross([
        ("sh600000", 1.0, 1, 1, 1.0, 1.0, 10.0, 1.0, 0),
        ("sz000001", 1.0, 1, 1, 1.0, 1.0, 20.0, 2.0, 0),
        ("sz000002", 1.0, 1, 1, 1.0, 1.0, 30.0, 3.0, 0),
    ])
    v = ms.compute_valuation(df, hist_pe=[10.0, 20.0, 30.0, 40.0], hist_pb=[1.0, 2.0])
    assert v["pe_median"] == 20.0 and v["pb_median"] == 2.0
    assert v["pe_pct_1y"] == 50.0      # 20 在 [10,20,30,40] 中 <= 的有 2/4
    assert v["pb_pct_1y"] == 100.0     # 2 在 [1,2] 中 <= 的有 2/2
