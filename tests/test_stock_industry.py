# -*- coding: utf-8 -*-
"""baostock stock_basics 附带行业：用 query_stock_industry 填充 industry。"""
import baostock as bs

from app.datasources.baostock import BaostockDataSource


class _RS:
    def __init__(self, rows, fields, code="0", msg="ok"):
        self.error_code, self.error_msg = code, msg
        self.fields = list(fields)
        self._rows = list(rows)
        self._i = -1

    def next(self):
        self._i += 1
        return self._i < len(self._rows)

    def get_row_data(self):
        return list(self._rows[self._i])


def test_stock_basics_fills_industry(monkeypatch):
    def fake_all_stock(day=None):
        return _RS(
            rows=[("sh.600000", "1", "浦发银行"), ("sh.000001", "1", "上证指数")],
            fields=["code", "tradeStatus", "code_name"],
        )

    def fake_industry(code=None, date=None):
        return _RS(
            rows=[("2024-01-01", "sh.600000", "浦发银行", "银行", "证监会")],
            fields=["updateDate", "code", "code_name", "industry", "industryClassification"],
        )

    monkeypatch.setattr(bs, "query_all_stock", fake_all_stock)
    monkeypatch.setattr(bs, "query_stock_industry", fake_industry)
    # suggest_trade_day 依赖 universe，会走 query_all_stock
    ds = BaostockDataSource()
    out = ds.stock_basics()
    by = {b["code"]: b for b in out}
    assert by["sh600000"]["name"] == "浦发银行"
    assert by["sh600000"]["industry"] == "银行"
    assert "sh000001" not in by     # 指数被过滤
