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


def test_compute_industry_and_hot():
    df = pd.DataFrame([
        ("sh600000", "浦发银行", 5.0, 1, 1, 1.0, 1, 0),
        ("sh600001", "甲", 1.0, 1, 1, 1.0, 1, 0),
        ("sz000001", "平安银行", -3.0, 1, 1, 1.0, 1, 0),
        ("sz000002", "乙", -6.0, 1, 1, 1.0, 1, 0),
    ], columns=["code", "name", "pctChg", "tradestatus", "volume", "amount", "turn", "isST"])
    ind = ms.compute_industry(df, {"sh600000": "银行", "sh600001": "银行",
                                   "sz000001": "地产", "sz000002": "地产"})
    assert ind["top"][0]["industry"] == "银行"
    assert abs(ind["top"][0]["avg_pct"] - 3.0) < 1e-9
    assert ind["bottom"][0]["industry"] == "地产"

    hot = ms.compute_hot(df)
    assert hot["gainers"][0]["code"] == "sh600000"
    assert hot["losers"][0]["code"] == "sz000002"


def test_compute_industry_empty_when_no_map():
    df = pd.DataFrame([("sh600000", 5.0, 1, 1, 1.0)],
                      columns=["code", "pctChg", "tradestatus", "volume", "amount"])
    ind = ms.compute_industry(df, {})
    assert ind["top"] == [] and ind["bottom"] == []


def test_build_summary_reads_parquet(tmp_path):
    """build_summary：跨股 DuckDB 聚合 + 指数读取，输出完整指标 dict。"""
    from app.services import index_store as ist
    data_dir = tmp_path / "daily_price"
    index_dir = tmp_path / "index_daily"
    data_dir.mkdir(); index_dir.mkdir()

    # 造 2 只个股 2 个交易日
    for code, name in (("sh600000", "浦发银行"), ("sz000001", "平安银行")):
        rows = []
        for d, pct in (("2024-01-02", 1.0), ("2024-01-03", pct_val(code))):
            rows.append({
                "date": d, "code": code, "open": 10.0, "high": 10.0, "low": 10.0,
                "close": 10.0, "preclose": 10.0, "volume": 100, "amount": 1000.0,
                "turn": 1.0, "pctChg": pct, "tradestatus": 1, "isST": 0,
                "peTTM": 10.0, "pbMRQ": 1.0, "psTTM": 1.0, "pcfNcfTTM": 1.0,
                "qfq_factor": 1.0, "hfq_factor": 1.0,
                "qfq_open": 10, "qfq_high": 10, "qfq_low": 10, "qfq_close": 10,
                "hfq_open": 10, "hfq_high": 10, "hfq_low": 10, "hfq_close": 10,
            })
        pd.DataFrame(rows).to_parquet(data_dir / f"{code}.parquet", index=False)

    ist.write_index(index_dir, "sh.000001", pd.DataFrame([{
        "date": "2024-01-03", "code": "sh.000001", "open": 3000, "high": 3010,
        "low": 2990, "close": 3005, "preclose": 3000, "volume": 1, "amount": 1e9,
        "pctChg": 0.17}]))

    m = ms.build_summary(data_dir, index_dir, "2024-01-03",
                         ["sh.000001"], {"sh600000": "银行"}, {"sh600000": "浦发银行"})
    assert m["trade_date"] == "2024-01-03"
    assert m["breadth"]["up"] == 1 and m["breadth"]["down"] == 1
    assert m["volume"]["amount"] == 2000.0
    assert m["index"]["rows"][0]["name"] == "上证综指"
    assert abs(m["index"]["rows"][0]["close"] - 3005.0) < 1e-9
    assert m["missing"] == []


def pct_val(code):
    return 2.0 if code == "sh600000" else -2.0
