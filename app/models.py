# -*- coding: utf-8 -*-
"""元数据库 ORM 模型：dataset / strategy / task_run / cron_task。"""
from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, Column, Date, DateTime, Float, Integer, String, Text,
)

from .database import Base


class Dataset(Base):
    """数据集：对应一个真实数据目录，记录扫描得到的覆盖状态。"""
    __tablename__ = "dataset"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)      # 每日/合并/A股日K数据
    type = Column(String, default="")                       # 每日/合并/日K
    data_dir = Column(String, default="")                   # 相对仓库根目录
    update_type = Column(String, default="")                # 全量/增量/复权
    last_run_at = Column(DateTime, nullable=True)
    latest_data_date = Column(String, default="")           # 最新数据日期
    lag_days = Column(Integer, default=0)                   # 滞后天数
    status = Column(String, default="")                     # 正常/缺失/未生成
    file_count = Column(Integer, default=0)                 # 实际文件数


class Strategy(Base):
    """策略/数据脚本登记（由 manifest 扫描产生）。"""
    __tablename__ = "strategy"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    kind = Column(String, default="script")                 # script/strategy/backtest
    script_path = Column(String, nullable=False)            # 相对仓库路径
    manifest_path = Column(String, default="")              # manifest yaml 路径
    params_schema = Column(JSON, default=dict)              # 声明式参数 schema
    data_dep = Column(String, default="")                   # 数据依赖
    output_pattern = Column(String, default="")             # 输出文件/目录模式
    tags = Column(String, default="")

    # 保持唯一：同一脚本只登记一次
    __table_args__ = (__import__("sqlalchemy").UniqueConstraint("kind", "script_path", name="uq_kind_script"),)


class TaskRun(Base):
    """一次任务运行记录。"""
    __tablename__ = "task_run"

    id = Column(Integer, primary_key=True)
    kind = Column(String, default="script")                 # script/strategy/backtest/ingest
    # ref_id 语义随 kind 变化：script/strategy→strategy.id；ingest→dataset.id；可为空
    ref_id = Column(Integer, nullable=True)
    ref_name = Column(String, default="")                   # 冗余名称，便于展示
    params = Column(JSON, default=dict)
    # 合法状态集合：queued/running/success/failed/partial_failed/aborted
    status = Column(String, default="queued")
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    exit_code = Column(Integer, nullable=True)
    log_path = Column(String, default="")                   # stdout/stderr 日志文件路径
    result_summary = Column(JSON, default=dict)             # 结果摘要
    output_files = Column(JSON, default=list)               # 本次运行产出文件清单

    created_at = Column(DateTime, default=datetime.now)


class StockFreshness(Base):
    """每股断点续传基线：记录该股已入库的最新日期与行数。"""
    __tablename__ = "stock_freshness"
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(16), unique=True, index=True, nullable=False)
    latest_date = Column(Date, nullable=True)
    row_count = Column(Integer, default=0)


class CronTask(Base):
    """定时任务（P0 仅建表 + 预留接口，不实例化调度）。"""
    __tablename__ = "cron_task"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    kind = Column(String, default="script")
    ref_id = Column(Integer, nullable=False)
    cron_expr = Column(String, default="")
    params = Column(JSON, default=dict)
    enabled = Column(Boolean, default=False)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)