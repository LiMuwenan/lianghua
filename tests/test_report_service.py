# -*- coding: utf-8 -*-
"""报告编排：就绪检查跳过、生成归档、发送分支。"""
from datetime import date

import pandas as pd

from app.services import ingest, report_service, index_store


def _cfg(tmp_path):
    class C:
        ROOT = tmp_path
        data_dir = tmp_path / "daily_price"
        index_dir = tmp_path / "index_daily"
        output_dir = tmp_path / "outputs"
        report_enabled = True
        report_cron = "0 18 * * 1-5"
        report_subject_prefix = "A股每日大盘汇总"
        report_recipients = ["a@outlook.com"]
        report_index_codes = ["sh.000001"]
        smtp_host = "smtp.126.com"
        smtp_port = 465
        smtp_use_ssl = True
        smtp_username = "sender@126.com"
        smtp_from = "sender@126.com"
        smtp_password_env = "SMTP_PASSWORD"
    C.data_dir.mkdir(parents=True, exist_ok=True)
    C.index_dir.mkdir(parents=True, exist_ok=True)
    return C


def _write_stock(data_dir, code, d):
    pd.DataFrame([{
        "date": d, "code": code, "open": 10, "high": 10, "low": 10, "close": 10,
        "preclose": 10, "volume": 100, "amount": 1000.0, "turn": 1.0, "pctChg": 1.0,
        "tradestatus": 1, "isST": 0, "peTTM": 10.0, "pbMRQ": 1.0, "psTTM": 1.0,
        "pcfNcfTTM": 1.0, "qfq_factor": 1.0, "hfq_factor": 1.0,
        "qfq_open": 10, "qfq_high": 10, "qfq_low": 10, "qfq_close": 10,
        "hfq_open": 10, "hfq_high": 10, "hfq_low": 10, "hfq_close": 10,
    }]).to_parquet(data_dir / f"{code}.parquet", index=False)


def test_resolve_trade_date(monkeypatch):
    monkeypatch.setattr(ingest, "_trade_dates", lambda s, e: ["2026-10-09", "2026-10-12"])
    assert report_service.resolve_trade_date("2026-10-14") == "2026-10-12"
    monkeypatch.setattr(ingest, "_trade_dates", lambda s, e: [])
    assert report_service.resolve_trade_date("2026-10-14") is None


def test_stock_latest_fallback_scans_all_files(tmp_path):
    """断点表为空时回退扫描需覆盖全部文件，最新日期不在前 200 个文件名中也要取到。"""
    cfg = _cfg(tmp_path)
    for i in range(205):
        _write_stock(cfg.data_dir, f"sh60{i:04d}", "2026-10-13" if i == 204 else "2026-01-02")

    class DB:
        def query(self, *a, **k):
            class Q:
                def scalar(self):
                    return None
            return Q()

    assert report_service._stock_latest(DB(), cfg.data_dir) == "2026-10-13"


def test_run_skips_when_not_ready(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(ingest, "_trade_dates", lambda s, e: ["2026-10-13"])
    monkeypatch.setattr(report_service.index_ingest, "run_index_fetch",
                        lambda *a, **k: {"fetched": {}, "skipped": [], "latest": {}})
    # 无个股文件 → stock_latest 为 None

    class DB:
        def query(self, *a, **k):
            class Q:
                def scalar(self): return None
                def max(self): return None
            return Q()

    res = report_service.run_daily_report(cfg, DB(), log=lambda m: None, send=True)
    assert res["status"] == "skipped"
    assert "个股" in res["reason"]


def test_run_success_generates_and_sends(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(ingest, "_trade_dates", lambda s, e: ["2026-10-13"])
    monkeypatch.setattr(report_service.index_ingest, "run_index_fetch",
                        lambda *a, **k: {"fetched": {"sh.000001": 1}, "skipped": [], "latest": {}})
    _write_stock(cfg.data_dir, "sh600000", "2026-10-13")
    index_store.write_index(cfg.index_dir, "sh.000001", pd.DataFrame([{
        "date": "2026-10-13", "code": "sh.000001", "open": 3000, "high": 3010,
        "low": 2990, "close": 3005, "preclose": 3000, "volume": 1, "amount": 1e9,
        "pctChg": 0.17}]))

    class Row:
        code = "sh600000"
        name = "浦发银行"
        industry = "银行"

    class DB:
        def query(self, *a, **k):
            class Q:
                def scalar(self): return date(2026, 10, 13)
                def all(self): return [Row()]
            return Q()

    sent = {}

    def fake_send(cfg_, recipients, subject, html, password):
        sent["recipients"] = recipients
        sent["subject"] = subject
        sent["html"] = html

    monkeypatch.setattr(report_service.email_sender, "send_email", fake_send)
    monkeypatch.setenv("SMTP_PASSWORD", "授权码")

    res = report_service.run_daily_report(cfg, DB(), log=lambda m: None, send=True)
    assert res["status"] == "success"
    assert res["email"]["sent"] is True
    assert sent["subject"] == "A股每日大盘汇总 | 2026-10-13"
    assert "上证综指" in sent["html"]
    # 归档 HTML 存在
    from pathlib import Path
    assert Path(res["html_path"]).is_file()
