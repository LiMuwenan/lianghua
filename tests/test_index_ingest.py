# -*- coding: utf-8 -*-
"""指数抓取：增量区间、写 parquet、取不到则跳过。"""
import pandas as pd

from app.services import index_ingest, index_store


def _row(date, close):
    return {"date": date, "code": "sh.000001", "open": close, "high": close,
            "low": close, "close": close, "preclose": close,
            "volume": "1", "amount": "1", "pctChg": "0.5"}


def test_run_index_fetch_writes_and_increments(tmp_path, monkeypatch):
    calls = []

    def fake(code, start, end):
        calls.append((code, start, end))
        return [_row("2024-01-02", 3000.0), _row("2024-01-03", 3010.0)]

    monkeypatch.setattr(index_ingest, "_fetch_index", fake)
    res = index_ingest.run_index_fetch(tmp_path, ["sh.000001"], "2024-01-03")
    assert res["fetched"] == {"sh.000001": 2}
    assert res["latest"]["sh.000001"] == "2024-01-03"
    assert calls == [("sh.000001", "2005-01-01", "2024-01-03")]

    # 第二次：已最新 → 不再请求，start=次日
    calls.clear()
    res2 = index_ingest.run_index_fetch(tmp_path, ["sh.000001"], "2024-01-03")
    assert res2["fetched"] == {} and calls == []
    assert res2["latest"]["sh.000001"] == "2024-01-03"


def test_run_index_fetch_skips_empty_and_error(tmp_path, monkeypatch):
    def fake(code, start, end):
        if code == "sh.000852":
            return []
        raise RuntimeError("boom")

    monkeypatch.setattr(index_ingest, "_fetch_index", fake)
    res = index_ingest.run_index_fetch(tmp_path, ["sh.000852", "sz.399001"], "2024-01-03")
    assert set(res["skipped"]) == {"sh.000852", "sz.399001"}
    assert res["fetched"] == {}

    # 增量起点：已有数据时从次一日开始
    index_store.write_index(tmp_path, "sh000001", pd.DataFrame([_row("2024-01-02", 1.0)]))
    got = {}

    def fake2(code, start, end):
        got["start"] = start
        return [_row("2024-01-03", 2.0)]

    monkeypatch.setattr(index_ingest, "_fetch_index", fake2)
    index_ingest.run_index_fetch(tmp_path, ["sh.000001"], "2024-01-03")
    assert got["start"] == "2024-01-03"
