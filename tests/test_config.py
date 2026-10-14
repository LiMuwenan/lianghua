# -*- coding: utf-8 -*-
"""配置加载测试：验证 APP_CONFIG 环境变量选择配置文件。"""
from pathlib import Path

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


def test_daily_factor_dirs_from_config(tmp_path, monkeypatch):
    """daily_dir/factor_dir 从配置读取：绝对路径直接用，相对路径解析到仓库根。"""
    custom = tmp_path / "cfg.yaml"
    custom.write_text(
        "daily_dir: data/raw/daily\n"
        "factor_dir: D:/abs/factor\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_CONFIG", str(custom))
    cfg = config.load_config()
    assert cfg.daily_dir == (cfg.ROOT / "data" / "raw" / "daily").resolve()
    assert cfg.factor_dir == Path("D:/abs/factor")


def test_index_and_report_config(tmp_path, monkeypatch):
    """index_dir 与 report/smtp 段：相对路径解析到仓库根，缺省有合理默认。"""
    custom = tmp_path / "cfg2.yaml"
    custom.write_text(
        "index_dir: data/market/index_daily\n"
        "report:\n"
        "  enabled: true\n"
        "  cron: \"0 18 * * 1-5\"\n"
        "  recipients: [\"a@outlook.com\"]\n"
        "  subject_prefix: \"A股每日大盘汇总\"\n"
        "  index_codes: [sh.000001, sz.399001]\n"
        "smtp:\n"
        "  host: smtp.126.com\n"
        "  port: 465\n"
        "  use_ssl: true\n"
        "  username: \"sender@126.com\"\n"
        "  from_addr: \"sender@126.com\"\n"
        "  password_env: SMTP_PASSWORD\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_CONFIG", str(custom))
    cfg = config.load_config()
    assert cfg.index_dir == (cfg.ROOT / "data" / "market" / "index_daily").resolve()
    assert cfg.report_enabled is True
    assert cfg.report_cron == "0 18 * * 1-5"
    assert cfg.report_recipients == ["a@outlook.com"]
    assert cfg.report_subject_prefix == "A股每日大盘汇总"
    assert cfg.report_index_codes == ["sh.000001", "sz.399001"]
    assert cfg.smtp_host == "smtp.126.com"
    assert cfg.smtp_port == 465
    assert cfg.smtp_use_ssl is True
    assert cfg.smtp_username == "sender@126.com"
    assert cfg.smtp_from == "sender@126.com"
    assert cfg.smtp_password_env == "SMTP_PASSWORD"


def test_report_defaults_when_absent(tmp_path, monkeypatch):
    """未配置 report/smtp 段时给出默认值（不报错）。"""
    custom = tmp_path / "cfg3.yaml"
    custom.write_text("data_dir: data/market\n", encoding="utf-8")
    monkeypatch.setenv("APP_CONFIG", str(custom))
    cfg = config.load_config()
    assert cfg.index_dir == (cfg.ROOT / "data" / "market" / "index_daily").resolve()
    assert cfg.report_enabled is True
    assert cfg.report_recipients == []
    assert cfg.smtp_host == "smtp.126.com"
    assert cfg.smtp_port == 465
    assert len(cfg.report_index_codes) == 8