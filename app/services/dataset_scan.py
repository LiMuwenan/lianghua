# -*- coding: utf-8 -*-
"""数据覆盖度扫描：基于现有真实数据目录的文件扫描，得到覆盖度/滞后天数/最新日期。

不造假值——所有数字都来自对实际文件/内容的最小读取：
- 每日：目录下 {date}.csv，latest = 最大文件名日期，lag = 今天 - latest（自然日）。
- 合并 / A股日K数据：{code}.csv / {code}.xlsx，latest = 任一文件最近修改时间、count = 股票文件数。
覆盖度 = 实际文件数 / 期望数（期望数来自 config；无法定义时存 0，前端显示实际文件数）。
"""
import datetime
import logging
import re
from pathlib import Path

from ..config import Config
from ..models import Dataset

logger = logging.getLogger("app.dataset_scan")

_DAILY_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _latest_mmtime(file: Path) -> str:
    return datetime.date.fromtimestamp(file.stat().st_mtime).isoformat()


def scan_one(cfg: Config, ds_cfg: dict) -> dict:
    """扫描单个数据集目录，返回扫描结果字段。"""
    rel_dir = ds_cfg.get("dir", "")
    data_dir = (cfg.ROOT / rel_dir).resolve()
    allowed_parent = cfg.ROOT.resolve()
    if allowed_parent not in data_dir.parents and data_dir != allowed_parent:
        # 只接受仓库内目录，避免任意读取
        return {"missing": True}

    glob = ds_cfg.get("file_glob", "*.csv")
    if not data_dir.is_dir():
        files = []
    else:
        files = sorted(data_dir.glob(glob))

    count = len(files)
    if count == 0:
        return {"file_count": 0, "latest_data_date": "", "lag_days": None, "coverage": 0.0, "status": "未生成"}

    # 每日：文件名含日期
    latest_date = ""
    lag_days = None
    if ds_cfg.get("type") == "每日":
        dates = []
        for f in files:
            m = _DAILY_RE.search(f.name)
            if m:
                dates.append(datetime.date.fromisoformat(m.group(1)))
        if dates:
            latest_date = max(dates).isoformat()
            lag_days = (datetime.date.today() - max(dates)).days
    else:
        latest_date = _latest_mmtime(files[-1])

    expected = int(ds_cfg.get("expected_per_day", 0) or 0)
    coverage = min(1.0, count / expected) if expected else 0.0
    status = "正常" if count > 0 else "缺失"
    return {
        "file_count": count,
        "latest_data_date": latest_date,
        "lag_days": lag_days,
        "coverage": round(coverage, 4),
        "status": status,
    }


def scan_all(cfg: Config, db_session) -> dict:
    """扫描 config 中声明的所有数据集并更新 dataset 表。"""
    result = {}
    for ds_cfg in cfg.datasets:
        name = ds_cfg.get("name", "")
        data = scan_one(cfg, ds_cfg)
        row = db_session.query(Dataset).filter(Dataset.name == name).first()
        if row is None:
            row = Dataset(name=name)
            db_session.add(row)
        row.type = ds_cfg.get("type", "")
        row.data_dir = ds_cfg.get("dir", "")
        if data.get("missing"):
            row.status = "目录缺失"
            row.file_count = 0
        else:
            row.file_count = data["file_count"]
            row.latest_data_date = data["latest_data_date"]
            row.lag_days = data["lag_days"]
            row.coverage = data["coverage"]
            row.status = data["status"]
        result[name] = data
    db_session.commit()
    return result