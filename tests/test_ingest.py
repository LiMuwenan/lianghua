# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "d:/Project/lianghua")

import pytest
from app.services import ingest, parquet_store as store


def fake_daily_bars(date):
    """返回某日全市场不复权日K的一行（code 已归一化为纯数字）。"""
    return [{"code": "000001", "date": date, "open": 1, "high": 1, "low": 1,
             "close": 1, "preclose": 1, "volume": 1, "amount": 1, "turn": 0,
             "pctChg": 0, "tradestatus": "1", "isST": "0", "peTTM": 1,
             "pbMRQ": 1, "psTTM": 1, "pcfNcfTTM": 1}]


def test_compute_adjusted_prices():
    """给定原始价+因子，算出的前/后复权价正确。"""
    row = {"open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2,
           "qfq_factor": 1.5, "hfq_factor": 8.0}
    out = ingest.compute_adjusted_prices(row)
    assert out["qfq_close"] == pytest.approx(10.2 * 1.5)
    assert out["hfq_close"] == pytest.approx(10.2 * 8.0)
    assert out["hfq_high"] == pytest.approx(10.5 * 8.0)
    assert out["qfq_open"] == pytest.approx(10.0 * 1.5)


def _bind(monkeypatch):
    monkeypatch.setattr(ingest, "_fetch_daily_bars", fake_daily_bars)
    monkeypatch.setattr(ingest, "_fetch_factors", lambda date: [])


def _stock_df(code, date):
    """构造单只股票 24 列 DataFrame（含复权列），供 write_stock 直接落盘。"""
    row = {**fake_daily_bars(date)[0], "code": code,
           "qfq_factor": 1.0, "hfq_factor": 1.0}
    return ingest._row_to_stock_df([row])


def test_run_fetch_initial_directory(tmp_path, monkeypatch):
    """首次获取（freshness 空）：整段新建每股 parquet，并记下断点位点。"""
    _bind(monkeypatch)
    freshness = {}
    res = ingest.run_fetch(tmp_path, dates=["2024-01-02", "2024-01-05"],
                            cancel_flag=lambda: False, freshness=freshness)
    assert res["status"] == "success"
    assert res["processed_dates"] == 2 and res["skipped_dates"] == 0
    assert res["rows"] == 2
    assert freshness["000001"] == "2024-01-05"
    back = store.read_stock(tmp_path, "000001")
    assert list(back["date"]) == ["2024-01-02", "2024-01-05"]


def test_run_fetch_skips_existing_dates(tmp_path, monkeypatch):
    """已有日期不重复抓/不重复写：整日(≤断点位点)跳过；晚于位点的追加。"""
    _bind(monkeypatch)
    # 第一次：写入 d1
    ingest.run_fetch(tmp_path, dates=["2024-01-02"], cancel_flag=lambda: False, freshness={})
    # 第二次：断点位点=d1，dates=[d1, d5] → d1 整日跳过，d5 追加
    freshness = {"000001": "2024-01-02"}
    res = ingest.run_fetch(tmp_path, dates=["2024-01-02", "2024-01-05"],
                            cancel_flag=lambda: False, freshness=freshness)
    assert res["processed_dates"] == 1 and res["skipped_dates"] == 1
    back = store.read_stock(tmp_path, "000001")
    assert list(back["date"]) == ["2024-01-02", "2024-01-05"]  # 不重复 d1


def test_run_fetch_per_stock_skip(tmp_path, monkeypatch):
    """某股目标日已含则不重写；另一股落后位点则追加。"""
    def bars(date):
        # 市场当日含 000001 与 000002 两只
        return [fake_daily_bars(date)[0],
                {**fake_daily_bars(date)[0], "code": "000002"}]
    monkeypatch.setattr(ingest, "_fetch_daily_bars", bars)
    monkeypatch.setattr(ingest, "_fetch_factors", lambda date: [])
    # 000001 落后到 d0；000002 已到目标日 d8
    store.write_stock(tmp_path, "000001", _stock_df("000001", "2024-01-02"))
    store.write_stock(tmp_path, "000002", _stock_df("000002", "2024-01-08"))
    freshness = {"000001": "2024-01-02", "000002": "2024-01-08"}
    res = ingest.run_fetch(tmp_path, dates=["2024-01-08"], cancel_flag=lambda: False,
                            freshness=freshness)
    assert res["status"] == "success" and res["skipped_dates"] == 0
    assert freshness["000001"] == "2024-01-08"   # 落后 → 追加并推进
    assert freshness["000002"] == "2024-01-08"   # 已含该日 → 位点不变
    assert len(store.read_stock(tmp_path, "000001")) == 2   # d0 + d8
    assert len(store.read_stock(tmp_path, "000002")) == 1   # 仅 d8，不重复写


def test_run_fetch_per_stock_skip_existing_day(tmp_path, monkeypatch):
    """同一日：领先股票该日已含→跳过不重写；落后股票该日需要→追加。"""
    def bars(date):
        return [fake_daily_bars(date)[0],
                {**fake_daily_bars(date)[0], "code": "000002"}]
    monkeypatch.setattr(ingest, "_fetch_daily_bars", bars)
    monkeypatch.setattr(ingest, "_fetch_factors", lambda date: [])
    # 000001 已到 d5，000002 只到 d0，目标日 = d5
    store.write_stock(tmp_path, "000001", _stock_df("000001", "2024-01-05"))
    store.write_stock(tmp_path, "000002", _stock_df("000002", "2024-01-02"))
    freshness = {"000001": "2024-01-05", "000002": "2024-01-02"}
    res = ingest.run_fetch(tmp_path, dates=["2024-01-05"], cancel_flag=lambda: False,
                            freshness=freshness)
    assert res["skipped_dates"] == 0  # 全局最小位点 d0 < d5，不会整日跳过
    # 000001 已含 d5 → 不重复写；000002 落后 → 追加 d5
    assert freshness["000001"] == "2024-01-05"
    assert freshness["000002"] == "2024-01-05"
    assert len(store.read_stock(tmp_path, "000001")) == 1   # 仍只有 d5 一行
    assert list(store.read_stock(tmp_path, "000002")["date"]) == ["2024-01-02", "2024-01-05"]


def test_run_fetch_cancel(capsys, monkeypatch, tmp_path):
    """协作式取消：cancel_flag 置位后循环尽早退出并返回 aborted。"""
    calls = {"n": 0}

    def fake_daily(date):
        calls["n"] += 1
        return fake_daily_bars(date)

    state = {"n": 0}

    def cancel_flag():
        # 顶部检查：第1、2天正常拉取，第3天边界判定取消 → 共拉取2次
        state["n"] += 1
        return state["n"] >= 3

    monkeypatch.setattr(ingest, "_fetch_daily_bars", fake_daily)
    monkeypatch.setattr(ingest, "_fetch_factors", lambda date: [])

    result = ingest.run_fetch(tmp_path, dates=["2024-01-02", "2024-01-05", "2024-01-08"],
                               cancel_flag=cancel_flag, freshness={})
    assert result["status"] == "aborted"
    assert calls["n"] == 2  # 第二次后即取消


def _factor_row(date, code, qfq, hfq):
    """构造 _fetch_factors 返回行（与 baostock.adjust_factors 口径一致）。"""
    return {"date": date, "code": code, "qfq_factor": qfq, "hfq_factor": hfq}


def test_run_fetch_carries_factor_between_events(tmp_path, monkeypatch):
    """非除权日沿用最近因子（不再默认 1.0）：d1 除权，d2 沿用。"""
    monkeypatch.setattr(ingest, "_fetch_daily_bars", fake_daily_bars)
    monkeypatch.setattr(ingest, "_fetch_factors",
                        lambda date: [_factor_row(date, "000001", 0.5, 2.0)]
                        if date == "2024-01-02" else [])
    res = ingest.run_fetch(tmp_path, dates=["2024-01-02", "2024-01-05"],
                           cancel_flag=lambda: False, freshness={})
    assert res["status"] == "success"
    back = store.read_stock(tmp_path, "000001")
    assert back["qfq_factor"].tolist() == [0.5, 0.5]   # d1 生效，d5 沿用
    assert back["qfq_close"].tolist() == [0.5, 0.5]    # 1 × 0.5
    assert back["hfq_factor"].tolist() == [2.0, 2.0]


def test_run_fetch_initial_factors_from_history(tmp_path, monkeypatch):
    """启动初始因子来自既有 parquet 末行：窗口内无事件也沿用历史因子。"""
    monkeypatch.setattr(ingest, "_fetch_daily_bars", fake_daily_bars)
    monkeypatch.setattr(ingest, "_fetch_factors", lambda date: [])
    # 既有 parquet 末行因子 = 0.5/2.0（模拟转换出的历史）
    store.write_stock(tmp_path, "000001", _stock_df("000001", "2024-01-02"))
    path = store.stock_path(tmp_path, "000001")
    df = store.read_stock(tmp_path, "000001")
    df.loc[0, ["qfq_factor", "hfq_factor"]] = [0.5, 2.0]
    df.to_parquet(path, index=False)
    initial = {"000001": {"qfq_factor": 0.5, "hfq_factor": 2.0}}
    res = ingest.run_fetch(tmp_path, dates=["2024-01-05"],
                           cancel_flag=lambda: False,
                           freshness={"000001": "2024-01-02"},
                           initial_factors=initial)
    assert res["status"] == "success"
    back = store.read_stock(tmp_path, "000001")
    assert back.iloc[-1]["qfq_factor"] == 0.5
    assert back.iloc[-1]["qfq_close"] == pytest.approx(0.5)