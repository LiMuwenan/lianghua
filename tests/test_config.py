# -*- coding: utf-8 -*-
"""配置加载测试：验证 APP_CONFIG 环境变量选择配置文件。"""
from app import config


def test_app_config_env_picks_custom_file(tmp_path, monkeypatch):
    custom = tmp_path / "custom.yaml"
    # 用与仓库默认 config/config.yaml 不同的相对路径，证明确实读取了自定义文件
    custom.write_text(
        "database: data/custom.db\n"
        "output_dir: outputs-custom\n"
        "data_dir: data/market-custom\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_CONFIG", str(custom))
    cfg = config.load_config()
    assert cfg.database == cfg.ROOT / "data" / "custom.db"
    assert cfg.output_dir == cfg.ROOT / "outputs-custom"
    assert cfg.data_dir == (cfg.ROOT / "data" / "market-custom").resolve()