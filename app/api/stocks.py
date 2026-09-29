# -*- coding: utf-8 -*-
"""个股 API（P1）：选股器列表、初始化、单股区间日K（三复权只读）。"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..config import load_config
from ..database import get_db
from ..models import Stock
from ..schemas import KlineOut, StockInitOut, StockOut
from ..services import parquet_reader
from ..services.stock_service import init_stocks

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


@router.get("", response_model=list[StockOut])
def list_stocks(q: str = Query("", description="按 code/名称模糊搜索，空=全部"),
                limit: int = 500, db: Session = Depends(get_db)):
    """选股器：全部或按 code/名称搜索的 A 股列表。"""
    rows = db.query(Stock)
    if q and q.strip():
        kw = f"%{q.strip()}%"
        rows = rows.filter((Stock.code.like(kw)) | (Stock.name.like(kw)))
    return rows.order_by(Stock.code).limit(min(limit, 2000)).all()


@router.post("/init", response_model=StockInitOut)
def init(request: Request, db: Session = Depends(get_db)):
    """初始化 A 股基础信息入库（从 baostock 拉取，幂等 upsert）。"""
    ds = getattr(request.app.state, "ds", None)
    if ds is None:
        raise HTTPException(503, "数据源尚未就绪")
    try:
        res = init_stocks(db, ds)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"股票初始化失败: {exc}")
    total = db.query(Stock).count()
    return StockInitOut(ok=True, count=res["count"],
                        message=f"新增 {res['count']} 只，当前共 {total} 只")


@router.get("/{code}/kline", response_model=KlineOut)
def kline(code: str,
          from_: str = Query("", alias="from", description="区间起始 YYYY-MM-DD"),
          to: str = Query("", alias="to", description="区间结束 YYYY-MM-DD")):
    """按 code + 区间读日K，一次返回不复权/前复权/后复权三组价量（只读 Parquet）。"""
    code = code.replace(".", "").strip()   # 兼容 sh.600000 / sh600000 传入
    cfg = load_config()
    return parquet_reader.read_kline(cfg.data_dir, code, from_, to)