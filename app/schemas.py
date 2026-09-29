# -*- coding: utf-8 -*-
"""Pydantic 请求/响应模型。"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


# ---------- 策略 ----------
class StrategyOut(BaseModel):
    id: int
    name: str
    kind: str
    script_path: str
    manifest_path: str = ""
    params_schema: dict = {}
    data_dep: str = ""
    output_pattern: str = ""
    tags: str = ""

    class Config:
        from_attributes = True


class RunRequest(BaseModel):
    """触发运行，可携带参数覆盖。"""
    params: dict = {}


# ---------- 数据集 ----------
class DatasetOut(BaseModel):
    id: int
    name: str
    type: str = ""
    data_dir: str = ""
    update_type: str = ""
    last_run_at: Optional[datetime] = None
    latest_data_date: str = ""
    lag_days: Optional[int] = None
    status: str = ""
    file_count: int = 0


# ---------- 任务 ----------
class TaskOut(BaseModel):
    id: int
    kind: str
    ref_id: int
    ref_name: str = ""
    params: dict = {}
    status: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    log_path: str = ""
    result_summary: dict = {}
    output_files: list = []
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ---------- 个股（P1） ----------
class StockOut(BaseModel):
    code: str
    name: str = ""
    industry: str = ""

    class Config:
        from_attributes = True


class KlineSeries(BaseModel):
    """一组 K 线价量序列（raw/qfq/hfq 各一份）。"""
    dates: list = []
    open: list = []
    high: list = []
    low: list = []
    close: list = []
    volume: list = []


class KlineOut(BaseModel):
    """单股区间日K，一次返回不复权/前复权/后复权三组价量。"""
    code: str = ""
    raw: KlineSeries = KlineSeries()
    qfq: KlineSeries = KlineSeries()
    hfq: KlineSeries = KlineSeries()


class StockInitOut(BaseModel):
    ok: bool
    count: int = 0
    message: str = ""


# ---------- 定时任务（P1 完整 CRUD） ----------
class CronCreate(BaseModel):
    name: str
    kind: str = "ingest"              # ingest | strategy
    ref_id: int
    cron_expr: str = ""               # 标准 cron 5 字段：minute hour dom month dow
    params: dict = {}
    enabled: bool = True


class CronUpdate(BaseModel):
    name: str | None = None
    cron_expr: str | None = None
    enabled: bool | None = None
    params: dict | None = None


class CronOut(BaseModel):
    id: int
    name: str
    kind: str
    ref_id: int
    cron_expr: str = ""
    params: dict = {}
    enabled: bool = False
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None

    class Config:
        from_attributes = True