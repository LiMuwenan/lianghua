# -*- coding: utf-8 -*-
"""平台配置加载：读取配置文件，提供全局路径与参数。"""
import os
import sys
from pathlib import Path

import yaml

# 仓库根目录 = 本文件上两级
ROOT = Path(__file__).resolve().parents[1]


class Config:
    """统一配置对象，启动时加载一次。"""

    def __init__(self, data: dict):
        self.raw = data
        self.ROOT = ROOT
        self.database = ROOT / data.get("database", "data/meta.db")
        self.output_dir = ROOT / data.get("output_dir", "outputs")
        self.logs_dir = self.output_dir / "logs"
        self.data_dir = Config._p(ROOT, data.get("data_dir", "data/market/daily_price"))
        self.daily_dir = Config._p(ROOT, data.get("daily_dir", r"G:\各种资料\量化数据\A股日K日分组"))
        self.factor_dir = Config._p(ROOT, data.get("factor_dir", r"G:\各种资料\量化数据\复权因子每日"))
        # 指数 Parquet 目录（与 data_dir 并列，独立分文件夹）
        self.index_dir = Config._p(ROOT, data.get("index_dir", "data/market/index_daily"))
        # 报告配置
        _r = data.get("report", {}) or {}
        self.report_enabled = bool(_r.get("enabled", True))
        self.report_cron = str(_r.get("cron", "0 18 * * 1-5"))
        self.report_subject_prefix = str(_r.get("subject_prefix", "A股每日大盘汇总"))
        self.report_recipients = list(_r.get("recipients", []) or [])
        self.report_index_codes = list(_r.get("index_codes", []) or []) or [
            "sh.000001", "sz.399001", "sz.399006", "sh.000688",
            "sh.000016", "sh.000300", "sh.000905", "sh.000852",
        ]
        # SMTP（发件用 126；授权码从环境变量读取，不入 config）
        _s = data.get("smtp", {}) or {}
        self.smtp_host = str(_s.get("host", "smtp.126.com"))
        self.smtp_port = int(_s.get("port", 465))
        self.smtp_use_ssl = bool(_s.get("use_ssl", True))
        self.smtp_username = str(_s.get("username", ""))
        self.smtp_from = str(_s.get("from_addr", "") or self.smtp_username)
        self.smtp_password_env = str(_s.get("password_env", "SMTP_PASSWORD"))
        self.cors_origins = data.get("cors_origins", ["http://127.0.0.1:8000"])
        self.scan_dirs = data.get("scan_dirs", ["script", "strategy/strategy"])
        self.datasets = data.get("datasets", [])
        self.task_timeout_sec = int(data.get("task_timeout_sec", 7200))

    @staticmethod
    def _p(root: Path, v: str) -> Path:
        """路径：绝对路径直接用；相对路径解析到仓库根下。"""
        p = Path(v).expanduser()
        return p if p.is_absolute() else (root / p).resolve()

    def absolute_script_path(self, rel: str) -> Path:
        """把 manifest 中的相对 script 路径映射为仓库内绝对路径。"""
        return (ROOT / rel).resolve()


def _config_path() -> Path:
    """返回配置文件路径：env APP_CONFIG 优先，否则仓库默认 config.yaml。"""
    env = os.environ.get("APP_CONFIG")
    if env:
        return Path(env).expanduser()
    return ROOT / "config" / "config.yaml"


def load_config() -> Config:
    cfg_path = _config_path()
    with open(cfg_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Config(data)


def _early_check() -> None:
    """提供命令行快捷方式：python -m app.config 打印配置。"""
    print(load_config().__dict__)


if __name__ == "__main__":
    _early_check()
    sys.exit(0)