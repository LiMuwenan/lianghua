# -*- coding: utf-8 -*-
"""数据源因子接口测试：归一化字段契约不变，同时保留原始层所需原始字段。"""
import sys
sys.path.insert(0, "d:/Project/lianghua")

from app.datasources.baostock import BaostockDataSource


def test_adjust_factors_keeps_raw_fields(monkeypatch):
    ds = BaostockDataSource()
    monkeypatch.setattr(ds, "_query", lambda fn: object())
    monkeypatch.setattr(ds, "_rows", lambda rs, date: [{
        "code": "sh.600000", "dividOperateDate": "2024-01-02",
        "foreAdjustFactor": "0.9", "backAdjustFactor": "3.3", "adjustFactor": "1.1",
    }])
    out = ds.adjust_factors("2024-01-02")
    assert len(out) == 1
    row = out[0]
    # 归一化字段（摄取落 parquet 用，契约不变）
    assert row["code"] == "sh600000"
    assert row["qfq_factor"] == 0.9 and row["hfq_factor"] == 3.3
    # 原始字段（原始层落 CSV 用）
    assert row["raw_code"] == "sh.600000"
    assert row["dividOperateDate"] == "2024-01-02"
    assert float(row["foreAdjustFactor"]) == 0.9
    assert float(row["backAdjustFactor"]) == 3.3
    assert float(row["adjustFactor"]) == 1.1