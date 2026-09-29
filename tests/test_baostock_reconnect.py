# tests/test_baostock_reconnect.py
# -*- coding: utf-8 -*-
"""baostock 会话失效自愈：query 返回未登录(10001001)时自动重登一次并重试。"""
import sys
sys.path.insert(0, "d:/Project/lianghua")


class _RS:
    """伪 ResultData：字段 + next/row 翻页 + error_code。"""
    def __init__(self, rows=None, fields=("calendar_date", "is_trading_day"), code="0", msg="ok"):
        self.error_code, self.error_msg = code, msg
        self.fields = list(fields)
        self._rows = list(rows or [])
        self._i = -1

    def next(self):
        self._i += 1
        return self._i < len(self._rows)

    def get_row_data(self):
        return list(self._rows[self._i])


def test_trade_dates_reconnects_on_unauthenticated(monkeypatch):
    """首次查询未登录 → 自动重登 → 重试成功并返回交易日。"""
    import baostock as real_bs
    key = "10001001"
    calls = {"n": 0, "logins": 0}

    def fake_query(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _RS(code=key, msg="用户未登录")
        return _RS(rows=[("2026-09-23", "1"), ("2026-09-24", "1"), ("2026-09-25", "0")])

    def fake_login():
        calls["logins"] += 1
        return _RS()

    monkeypatch.setattr(real_bs, "query_trade_dates", fake_query)
    monkeypatch.setattr(real_bs, "login", fake_login)

    from app.datasources.baostock import BaostockDataSource
    ds = BaostockDataSource()
    days = ds.trade_dates("2026-09-23", "2026-09-24")  # 只保留区间内
    assert days == ["2026-09-23", "2026-09-24"]   # 已重试拿到，且日期被过滤
    assert calls["n"] == 2 and calls["logins"] == 1


def test_trade_dates_returns_empty_on_fatal_error(monkeypatch):
    """非会话类的致命错误：不重放，直接返回空列表（由上层空态兜底）。"""
    import baostock as real_bs
    calls = {"n": 0}

    def fake_query(*a, **k):
        return _RS(code="500", msg="busy")

    monkeypatch.setattr(real_bs, "query_trade_dates", fake_query)
    from app.datasources.baostock import BaostockDataSource
    ds = BaostockDataSource()
    assert ds.trade_dates("2026-09-23", "2026-09-24") == []