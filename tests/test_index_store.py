# -*- coding: utf-8 -*-
"""指数 Parquet 存储：写/读/增量 merge/最新日/代码归一化。"""
import pandas as pd

from app.services import index_store as st


def make_row(code="sh000001", date="2024-01-02", close=3000.0, pct=1.2):
    return {
        "date": date, "code": code, "open": close * 0.99, "high": close * 1.01,
        "low": close * 0.98, "close": close, "preclose": close * 0.99,
        "volume": "1000", "amount": "1.0e9", "pctChg": pct,
    }


def test_normalize_and_path(tmp_path):
    assert st.normalize("sh.000001") == "sh000001"
    assert st.index_path(tmp_path, "sh.000001").name == "sh000001.parquet"


def test_write_read_and_numeric_cast(tmp_path):
    df = pd.DataFrame([make_row()])
    st.write_index(tmp_path, "sh.000001", df)
    back = st.read_index(tmp_path, "sh.000001")
    assert list(back.columns) == st.INDEX_COLS
    assert float(back.iloc[0]["close"]) == 3000.0
    assert float(back.iloc[0]["volume"]) == 1000.0   # 字符串已转数值


def test_merge_dedup_ascending(tmp_path):
    st.write_index(tmp_path, "sh000001", pd.DataFrame([make_row(date="2024-01-03")]))
    st.merge_index(tmp_path, "sh000001", pd.DataFrame([make_row(date="2024-01-02")]))
    back = st.read_index(tmp_path, "sh000001")
    assert list(back["date"]) == ["2024-01-02", "2024-01-03"]


def test_latest_date_and_missing(tmp_path):
    assert st.latest_date(tmp_path, "sh000001") is None
    st.write_index(tmp_path, "sh000001", pd.DataFrame([make_row(date="2024-01-02")]))
    assert st.latest_date(tmp_path, "sh000001") == "2024-01-02"
