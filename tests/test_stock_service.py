# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "d:/Project/lianghua")

import pytest
from app.services import stock_service
from app.services.stock_service import is_a_stock


def test_is_a_stock_filters_indexes():
    """沪深 A 股代码：识别个股、剔除指数。"""
    assert is_a_stock("sh.600000") is True
    assert is_a_stock("sh.688001") is True
    assert is_a_stock("sz.000001") is True
    assert is_a_stock("sz.300750") is True
    assert is_a_stock("sh.000001") is False     # 上证指数
    assert is_a_stock("sz.399001") is False     # 深证成指


def test_init_stocks_upserts(tmp_path, monkeypatch):
    """init_stocks：数据源返回列表 → 存入 stock 表；重复调用不重复登记。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database import Base
    from app.models import Stock

    engine = create_engine(f"sqlite:///{tmp_path}/meta.db")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    class FakeDS:
        def stock_basics(self):
            return [
                {"code": "sh600000", "name": "浦发银行", "industry": ""},
                {"code": "sz000001", "name": "平安银行", "industry": ""},
            ]

    res = stock_service.init_stocks(db, FakeDS())
    assert res["count"] == 2
    codes = {s.code for s in db.query(Stock).all()}
    assert codes == {"sh600000", "sz000001"}
    # 二次调用幂等：不再新增
    res2 = stock_service.init_stocks(db, FakeDS())
    assert res2["count"] == 0
    assert db.query(Stock).count() == 2


def test_suggest_trade_day_uses_day_with_data(monkeypatch):
    """建议交易日：优先当天有数据的交易日，否则回退到最近有数据的一天。"""
    class FakeDS:
        def __init__(self, first_nonempty):
            self.first_nonempty = first_nonempty
        def universe(self, d):
            return ["x"] if d == self.first_nonempty else []

    fake = FakeDS(first_nonempty="2026-09-25")     # 4 个自然日前的某交易日
    out = stock_service.suggest_trade_day(fake)
    assert out == "2026-09-25"