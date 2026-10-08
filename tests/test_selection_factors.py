# -*- coding: utf-8 -*-
"""多因子选股脚本纯函数单测（用 importlib 从路径加载，不 import app.*）。"""
import importlib.util
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "strategy" / "strategy" / "选股-多因子打分.py"


def load_mod():
    spec = importlib.util.spec_from_file_location("sel", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_value_direction_low_pe_pb_scores_higher():
    m = load_mod()
    g = pd.DataFrame({
        "code": ["a", "b", "c"],
        "peTTM": [5.0, 20.0, 50.0],
        "pbMRQ": [1.0, 3.0, 6.0],
    })
    s = m.cross_section_scores(g, {"value": 1.0})
    assert s.loc[0, "value"] > s.loc[1, "value"] > s.loc[2, "value"]
    assert abs(s.loc[0, "score"] - s.loc[0, "value"]) < 1e-12


def test_volatility_direction_low_vol_scores_higher():
    m = load_mod()
    g = pd.DataFrame({"code": ["a", "b", "c"], "vol20": [0.01, 0.02, 0.03]})
    s = m.cross_section_scores(g, {"volatility": 1.0})
    assert s.loc[0, "volatility"] > s.loc[2, "volatility"]


def test_momentum_direction_high_return_scores_higher():
    m = load_mod()
    g = pd.DataFrame({
        "code": ["a", "b", "c"],
        "ret20": [0.3, 0.1, -0.1],
        "ret60": [0.5, 0.2, -0.2],
    })
    s = m.cross_section_scores(g, {"momentum": 1.0})
    assert s.loc[0, "momentum"] > s.loc[1, "momentum"] > s.loc[2, "momentum"]


def test_liquidity_direction_high_amount_scores_higher():
    m = load_mod()
    g = pd.DataFrame({"code": ["a", "b", "c"], "amount20": [1e9, 1e8, 1e6]})
    s = m.cross_section_scores(g, {"liquidity": 1.0})
    assert s.loc[0, "liquidity"] > s.loc[2, "liquidity"]


def test_zero_weight_factor_is_skipped():
    m = load_mod()
    g = pd.DataFrame({
        "code": ["a", "b"],
        "ret20": [0.9, -0.9],
        "ret60": [0.9, -0.9],
        "vol20": [0.01, 0.5],
    })
    s0 = m.cross_section_scores(g, {"volatility": 1.0})
    s1 = m.cross_section_scores(g, {"volatility": 1.0, "momentum": 0.0})
    assert (s0["score"] == s1["score"]).all()
    assert abs(s0.loc[0, "score"] - s0.loc[0, "volatility"]) < 1e-12


def test_missing_values_neutral():
    m = load_mod()
    g = pd.DataFrame({"code": ["a", "b"], "amount20": [float("nan"), 1e8]})
    s = m.cross_section_scores(g, {"liquidity": 1.0})
    assert abs(s.loc[0, "liquidity"] - 0.5) < 1e-12


def test_pick_topn_order():
    m = load_mod()
    scored = pd.DataFrame({"code": ["a", "b", "c"], "score": [0.1, 0.9, 0.5]})
    top = m.pick_topn(scored, 2)
    assert list(top["code"]) == ["b", "c"]
    assert list(top["rank"]) == [1, 2]


def test_filter_universe_rules():
    m = load_mod()
    g = pd.DataFrame({
        "code": ["sh600000", "sz000001", "sh000300", "sz399001", "sh600001"],
        "isST": ["0", "1", "0", "0", "0"],
        "tradestatus": ["1", "1", "1", "1", "0"],
        "cum_days": [300, 300, 300, 300, 10],
        "amount20": [1e8, 1e8, 1e8, 1e8, 1e8],
    })
    out = m.filter_universe(g, 5e7, 120)
    assert list(out["code"]) == ["sh600000"]


def test_filter_universe_low_liquidity_removed():
    m = load_mod()
    g = pd.DataFrame({
        "code": ["sh600000"],
        "isST": ["0"], "tradestatus": ["1"], "cum_days": [300], "amount20": [1e6],
    })
    assert len(m.filter_universe(g, 5e7, 120)) == 0


def _panel(n=70, code="sh600000"):
    dates = pd.date_range("2024-01-01", periods=n, freq="D").strftime("%Y-%m-%d").tolist()
    return pd.DataFrame({
        "code": [code] * n,
        "date": dates,
        "qfq_close": [10.0 + i for i in range(n)],
        "amount": [1e8] * n,
        "peTTM": [10.0] * n,
        "pbMRQ": [1.0] * n,
        "tradestatus": ["1"] * n,
        "isST": ["0"] * n,
    })


def test_compute_panel_factors():
    m = load_mod()
    out = m.compute_panel_factors(_panel(70))
    assert out["cum_days"].iloc[-1] == 70
    # ret20 = close[-1]/close[-1-20] - 1
    expect = (10.0 + 69) / (10.0 + 49) - 1
    assert abs(out["ret20"].iloc[-1] - expect) < 1e-9
    assert out["vol20"].notna().iloc[-1]
    assert abs(out["amount20"].iloc[-1] - 1e8) < 1e-6


def test_rebalance_dates_monthly():
    m = load_mod()
    dates = ["2024-01-30", "2024-01-31", "2024-02-01", "2024-02-28", "2024-02-29", "2024-03-01"]
    assert m.rebalance_dates(dates, "M") == ["2024-01-31", "2024-02-29", "2024-03-01"]


def test_rebalance_dates_weekly():
    m = load_mod()
    dates = ["2024-01-01", "2024-01-05", "2024-01-08", "2024-01-12"]
    assert m.rebalance_dates(dates, "W") == ["2024-01-05", "2024-01-12"]


def test_select_pool_columns_and_order():
    m = load_mod()
    panel = pd.concat([_panel(70, "sh600000"), _panel(70, "sz000001")], ignore_index=True)
    panel.loc[panel["code"] == "sz000001", "peTTM"] = 100.0  # b 高估值
    panel = m.compute_panel_factors(panel)
    pool = m.select_pool(panel, {"value": 1.0}, 1, "M", 0.0, 1)
    assert list(pool.columns) == [
        "date", "code", "score", "rank", "value", "momentum", "volatility", "liquidity",
    ]
    assert pool.iloc[0]["code"] == "sh600000"  # 低估值优先
    assert pool.iloc[0]["rank"] == 1