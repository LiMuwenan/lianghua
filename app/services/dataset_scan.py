# -*- coding: utf-8 -*-
"""数据扫描：基于真实 Parquet 文件与元库断点基线（stock_freshness）。

不造假值——所有数字都来自对实际文件/内容的真实读取：
- file_count    = 目录下 *.parquet 文件数（每股一文件，= 已入库股票数）
- latest_date   = max(stock_freshness.latest_date)，表空则按目录内文件最大 date 兜底
- lag_days      = max(0, 今天 - latest_date)
- 若目录不存在，file_count=0、status=未生成。
"""
import datetime
import logging
from pathlib import Path

from ..config import Config
from ..models import Dataset, StockFreshness
from . import parquet_store as store

logger = logging.getLogger("app.dataset_scan")


def scan_one(cfg: Config, ds_cfg: dict, db_session) -> dict:
    """扫描单个数据集目录，返回 {file_count, latest_data_date, lag_days, status}。"""
    raw = str(ds_cfg.get("dir", "")).strip()
    data_dir = Path(raw).expanduser()
    if data_dir.is_absolute():
        # 用户显式配置的绝对目录（如存放于外部盘），直接信任
        data_dir = data_dir.resolve()
    else:
        # 相对路径按仓库根解析，并做越权校验：不允许逃逸出仓库根
        data_dir = (cfg.ROOT / data_dir).resolve()
        allowed_parent = cfg.ROOT.resolve()
        if allowed_parent not in data_dir.parents and data_dir != allowed_parent:
            return {"file_count": 0, "latest_data_date": "", "lag_days": None,
                    "status": "目录越权"}

    glob = ds_cfg.get("file_glob", "*.parquet")
    files = sorted(data_dir.glob(glob)) if data_dir.is_dir() else []
    file_count = len(files)

    if file_count == 0:
        return {"file_count": 0, "latest_data_date": "", "lag_days": None,
                "status": "未生成"}

    # 最新日期：优先元库断点表，其次扫描目录内文件 date 列取最大
    latest_date = ""
    freshest: StockFreshness | None = (
        db_session.query(StockFreshness)
        .order_by(StockFreshness.latest_date.desc())
        .first()
    )
    if freshest is not None and freshest.latest_date is not None:
        latest_date = freshest.latest_date.isoformat()
    else:
        maxd = None
        for f in files[:200]:
            try:
                df = store.read_stock(data_dir, f.stem)
                if not df.empty:
                    d = datetime.date.fromisoformat(str(df["date"].max()))
                    maxd = d if maxd is None or d > maxd else maxd
            except Exception:  # noqa: BLE001  单个文件损坏不影响整体
                continue
        if maxd is not None:
            latest_date = maxd.isoformat()

    lag_days = None
    if latest_date:
        try:
            lag_days = max(0, (datetime.date.today() - datetime.date.fromisoformat(latest_date)).days)
        except ValueError:  # noqa
            lag_days = None

    return {
        "file_count": file_count,
        "latest_data_date": latest_date,
        "lag_days": lag_days,
        "status": "正常" if file_count > 0 else "缺失",
    }


def scan_all(cfg: Config, db_session, db_session_factory=None) -> dict:
    """扫描 config 中声明的所有数据集并更新 dataset 表。"""
    result = {}
    for ds_cfg in cfg.datasets:
        name = ds_cfg.get("name", "")
        data = scan_one(cfg, ds_cfg, db_session)
        row = db_session.query(Dataset).filter(Dataset.name == name).first()
        if row is None:
            row = Dataset(name=name)
            db_session.add(row)
        row.type = ds_cfg.get("type", "")
        row.data_dir = ds_cfg.get("dir", "")
        row.file_count = data["file_count"]
        row.latest_data_date = data["latest_data_date"]
        row.lag_days = data["lag_days"]
        row.status = data["status"]
        result[name] = data
    db_session.commit()
    return result