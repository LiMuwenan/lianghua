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


# ---------- 定时任务（P0 预留） ----------
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