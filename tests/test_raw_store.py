# -*- coding: utf-8 -*-
"""原始层写盘测试：列序、编码、幂等、空日标记、自动建目录。"""
import sys
sys.path.insert(0, "d:/Project/lianghua")

import pandas as pd

from app.services import raw_store


def _bars(date, code="sh.600000"):
    """含摄取过程追加的 qfq_factor/hfq_factor 键，验证会被剔除。"""
    return [{
        "date": date, "code": code, "open": "10", "high": "10.5", "low": "9.8",
        "close": "10.2", "preclose": "10.1", "volume": "100", "amount": "1000",
        "adjustflag": "3", "turn": "1.2", "tradestatus": "1", "pctChg": "0.5",
        "peTTM": "12", "pbMRQ": "1.1", "psTTM": "2", "pcfNcfTTM": "3", "isST": "0",
        "qfq_factor": 0.5, "hfq_factor": 2.0,
    }]


def test_write_daily_columns_and_encoding(tmp_path):
    """18 列、列序固定、UTF-8 BOM、保留原始 code（带点）、剔除多余键。"""
    p = raw_store.write_daily(tmp_path, "2024-01-02", _bars("2024-01-02"))
    assert p.name == "2024-01-02.csv"
    assert p.read_bytes().startswith(b"\xef\xbb\xbf")
    df = pd.read_csv(p, encoding="utf-8-sig", dtype=str)
    assert list(df.columns) == raw_store.DAILY_RAW_COLS
    assert df.loc[0, "code"] == "sh.600000"
    assert "qfq_factor" not in df.columns


def test_write_daily_idempotent(tmp_path):
    """文件已存在 → 返回 None 且内容不变。"""
    p1 = raw_store.write_daily(tmp_path, "2024-01-02", _bars("2024-01-02"))
    before = p1.read_bytes()
    p2 = raw_store.write_daily(tmp_path, "2024-01-02",
                               _bars("2024-01-02", code="sz.000001"))
    assert p2 is None
    assert raw_store.daily_path(tmp_path, "2024-01-02").read_bytes() == before


def test_write_daily_empty_day_marker(tmp_path):
    """空日写仅表头文件作为已处理标记，存在性为 True。"""
    p = raw_store.write_daily(tmp_path, "2024-01-03", [])
    assert raw_store.has_daily(tmp_path, "2024-01-03") is True
    df = pd.read_csv(p, encoding="utf-8-sig")
    assert df.empty and list(df.columns) == raw_store.DAILY_RAW_COLS


def test_write_daily_creates_dir(tmp_path):
    """目标目录不存在时自动创建。"""
    root = tmp_path / "nested" / "daily"
    raw_store.write_daily(root, "2024-01-02", _bars("2024-01-02"))
    assert root.is_dir()