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
