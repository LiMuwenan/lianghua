# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "d:/Project/lianghua")

import pytest
from app.services import ingest


def test_compute_adjusted_prices():
    """给定原始价+因子，算出的前/后复权价正确。"""
    row = {"open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2,
           "qfq_factor": 1.5, "hfq_factor": 8.0}
    out = ingest.compute_adjusted_prices(row)
    assert out["qfq_close"] == pytest.approx(10.2 * 1.5)
    assert out["hfq_close"] == pytest.approx(10.2 * 8.0)
    assert out["hfq_high"] == pytest.approx(10.5 * 8.0)
    assert out["qfq_open"] == pytest.approx(10.0 * 1.5)


def test_run_ingest_incremental_cancel(capsys, monkeypatch, tmp_path):
    """协作式取消：cancel_flag 置位后循环尽早退出并返回 aborted。"""
    calls = {"n": 0}

    def fake_daily(date):
        calls["n"] += 1
        return [{"code": "000001", "date": date, "open": 1, "high": 1, "low": 1,
                 "close": 1, "preclose": 1, "volume": 1, "amount": 1, "turn": 0,
                 "pctChg": 0, "tradestatus": "1", "isST": "0", "peTTM": 1,
                 "pbMRQ": 1, "psTTM": 1, "pcfNcfTTM": 1}]

    state = {"n": 0}

    def cancel_flag():
        # 顶部检查：第1、2天正常拉取，第3天边界判定取消 → 共拉取2次
        state["n"] += 1
        return state["n"] >= 3

    # 用 monkeypatch 替换 ingest 内部 fetch 钩子，取消靠传入的 cancel_flag
    monkeypatch.setattr(ingest, "_fetch_daily_bars", fake_daily)
    monkeypatch.setattr(ingest, "_fetch_factors", lambda date: [])

    result = ingest.run_incremental(tmp_path, dates=["2024-01-02", "2024-01-05", "2024-01-08"],
                                    cancel_flag=cancel_flag)
    assert result["status"] == "aborted"
    assert calls["n"] == 2  # 第二次后即取消