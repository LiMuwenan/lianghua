# -*- coding: utf-8 -*-
"""平台配置加载：读取 config/config.yaml，提供全局路径与参数。"""
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
        self.data_dir = ROOT / data.get("data_dir", "data/market/daily_price")
        self.cors_origins = data.get("cors_origins", ["http://127.0.0.1:8000"])
        self.scan_dirs = data.get("scan_dirs", ["script", "strategy/strategy"])
        self.datasets = data.get("datasets", [])
        self.task_timeout_sec = int(data.get("task_timeout_sec", 7200))

    def absolute_script_path(self, rel: str) -> Path:
        """把 manifest 中的相对 script 路径映射为仓库内绝对路径。"""
        return (ROOT / rel).resolve()


def load_config() -> Config:
    cfg_path = ROOT / "config" / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Config(data)


def _early_check() -> None:
    """提供命令行快捷方式：python -m app.config 打印配置。"""
    print(load_config().__dict__)


if __name__ == "__main__":
    _early_check()
    sys.exit(0)