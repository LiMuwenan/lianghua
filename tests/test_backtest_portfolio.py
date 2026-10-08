# -*- coding: utf-8 -*-
"""组合调仓回测脚本纯函数单测（importlib 从路径加载，不 import app.*）。"""
import importlib.util
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "strategy" / "backtest" / "组合调仓回测.py"


def load_mod():
    spec = importlib.util.spec_from_file_location("bt", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


DAYS = ["2024-01-31", "2024-02-01", "2024-02-02", "2024-02-29", "2024-03-01"]


def _close(a, b):
    return pd.DataFrame({"a": a, "b": b}, index=DAYS)


def test_turnover_symmetric():
    m = load_mod()
    assert m._turnover(["a", "b"], ["a", "b"]) == 0.0
    assert m._turnover(["c", "d"], ["a", "b"]) == 1.0
    assert abs(m._turnover(["a", "c"], ["a", "b"]) - 0.5) < 1e-12


def test_nav_starts_at_one_and_drawdown_nonpositive():
    m = load_mod()
    close = _close([10.0, 11.0, 12.0, 13.0, 14.0], [10.0, 9.0, 8.0, 7.0, 6.0])
    holdings = {"2024-01-31": ["a", "b"], "2024-02-29": ["a", "b"]}
    nav = m.compute_nav(close, holdings, ["2024-01-31", "2024-02-29"], 2, 0.0)
    assert abs(nav.iloc[0] - 1.0) < 1e-12
    assert abs(nav.loc["2024-01-31"] - 1.0) < 1e-12
    dd = m.drawdown(nav)
    assert (dd <= 1e-12).all()
    assert (nav > 0).all()


def test_hold_n_slices_pool():
    m = load_mod()
    close = _close([10.0, 11.0, 12.0, 13.0, 14.0], [10.0, 10.0, 10.0, 10.0, 10.0])
    holdings = {"2024-01-31": ["a", "b"], "2024-02-29": ["a", "b"]}
    nav = m.compute_nav(close, holdings, ["2024-01-31", "2024-02-29"], 1, 0.0)  # 只持 a
    assert nav.iloc[-1] > 1.0


def test_cost_reduces_nav():
    m = load_mod()
    close = _close([10.0, 11.0, 12.0, 13.0, 14.0], [10.0, 10.5, 11.0, 11.5, 12.0])
    holdings = {"2024-01-31": ["a", "b"], "2024-02-29": ["a", "b"]}
    reb = ["2024-01-31", "2024-02-29"]
    nav0 = m.compute_nav(close, holdings, reb, 2, 0.0)
    nav1 = m.compute_nav(close, holdings, reb, 2, 0.002)
    assert nav1.iloc[-1] < nav0.iloc[-1]


def test_benchmark_nav_start_at_one():
    m = load_mod()
    close = _close([10.0, 11.0, 12.0, 13.0, 14.0], [10.0, 10.0, 10.0, 10.0, 10.0])
    nav = m.benchmark_nav(close, DAYS)
    assert abs(nav.iloc[0] - 1.0) < 1e-12
    assert nav.iloc[-1] > 1.0


def test_metrics_self_consistent():
    m = load_mod()
    close = _close([10.0, 11.0, 12.0, 13.0, 14.0], [10.0, 10.5, 11.0, 11.5, 12.0])
    holdings = {"2024-01-31": ["a", "b"], "2024-02-29": ["a", "b"]}
    reb = ["2024-01-31", "2024-02-29"]
    nav = m.compute_nav(close, holdings, reb, 2, 0.002)
    bench = m.benchmark_nav(close, list(nav.index))
    mets = m.compute_metrics(nav, bench, holdings, reb, 0.002, 2, "market_equal", "tag", 1_000_000.0)
    assert abs(mets["total_return"] - (mets["final_nav"] - 1.0)) < 1e-6
    assert abs(mets["excess_return"] - (mets["total_return"] - mets["benchmark_total_return"])) < 1e-6
    assert mets["max_drawdown"] <= 0
    assert mets["n_periods"] == len(reb)
    assert mets["start"] == "2024-01-31" and mets["end"] == "2024-03-01"
    assert abs(mets["avg_turnover"] - 1.0 / 2) < 1e-6   # 首期全额(1.0) + 第二期不变(0.0) 的均值