# -*- coding: utf-8 -*-
"""个股基础信息初始化：从 baostock 拉取 A 股列表入库（stock 表）。

数据来源：query_all_stock 返回当日全部证券 {code, tradeStatus, code_name}。
A 股（沪深）代码规则：
- 沪市(sh)：600/601/603/605/688/689 （000 开头的 sh 是上证指数，非个股）
- 深市(sz)：000/001/002/003/300/301 （399 开头的 sz 是深证指数，非个股）
据此过滤出 A 股个股，归一化 code（sh.600000 → sh600000）后 upsert。
baostock 未提供行业字段，industry 置空、预留。
"""
import datetime
import logging

from sqlalchemy.orm import Session

from ..models import Stock

logger = logging.getLogger("app.stock_service")

# 沪/深 A 股代码前缀（用于从 query_all_stock 里筛掉指数等非个股）
_SH_STOCK_PREFIXES = ("600", "601", "603", "605", "688", "689")
_SZ_STOCK_PREFIXES = ("000", "001", "002", "003", "300", "301")


def is_a_stock(code: str) -> bool:
    """判断归一化/带点 delta code 是否为沪深 A 股个股（排除指数）。

    code 形如 'sh.600000' 或 'sz300750'；返回 True 表示是个股。
    """
    c = str(code).replace(" ", "")
    # 提取市场前缀与数字部分
    market, _, num = c.partition(".")
    if market not in ("sh", "sz") or not num:
        return False
    if not num.isdigit():
        return False
    if market == "sh":
        return num.startswith(_SH_STOCK_PREFIXES)
    return num.startswith(_SZ_STOCK_PREFIXES)


def init_stocks(db: Session, ds) -> dict:
    """全量初始化：从数据源拉取 A 股列表，upsert 到 stock 表。

    返回 {"count": 本次新增条数}。已存在的不重复登记（按 code 更新名称）。
    """
    basics = ds.stock_basics()          # [{code, name, industry}]
    new_count = 0
    for b in basics:
        code = str(b.get("code") or "").strip()
        if not code:
            continue
        name = str(b.get("name") or "").strip()
        industry = str(b.get("industry") or "").strip()
        row = db.query(Stock).filter(Stock.code == code).first()
        if row is None:
            db.add(Stock(code=code, name=name, industry=industry))
            new_count += 1
        else:
            row.name = name or row.name
            row.industry = industry
    db.commit()
    return {"count": new_count}     # 仅返回新增数；全量数量由前端 /api/stocks 反映


def suggest_trade_day(ds) -> str:
    """返回一个最近且确有股票数据的交易日字符串（供 query_all_stock 的 day 参数）。"""
    today = datetime.date.today()
    for i in range(8):
        d = today - datetime.timedelta(days=i)
        try:
            rows = ds.universe(d.isoformat())
            if rows:                  # 有数据（正常交易日）
                return d.isoformat()
        except Exception:  # noqa: BLE001
            continue
    return today.isoformat()          # 兜底：当天做参数，由数据源容错