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