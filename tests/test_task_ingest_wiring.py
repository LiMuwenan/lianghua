# -*- coding: utf-8 -*-
"""接线测试：_run_ingest 把配置里的原始层目录透传给 ingest.run_fetch。"""
import sys
sys.path.insert(0, "d:/Project/lianghua")

from types import SimpleNamespace

from app.config import Config
from app.services import task_service as ts


class _FakeQuery:
    def get(self, *_):
        return None

    def all(self):
        return []


class _FakeDB:
    def query(self, *_):
        return _FakeQuery()

    def commit(self):
        pass

    def close(self):
        pass


def test_run_ingest_passes_raw_dirs(tmp_path, monkeypatch):
    captured = {}

    def fake_run_fetch(root, dates, cancel_flag, freshness, initial_factors,
                       log=None, daily_dir=None, factor_dir=None):
        captured["daily_dir"] = daily_dir
        captured["factor_dir"] = factor_dir
        return {"status": "success", "processed_dates": 0, "skipped_dates": 0, "rows": 0}

    monkeypatch.setattr(ts.ingest, "run_fetch", fake_run_fetch)
    monkeypatch.setattr(ts.ingest, "_trade_dates", lambda s, e: [])
    monkeypatch.setattr(ts, "SessionLocal", lambda: _FakeDB())

    cfg = Config({
        "data_dir": str(tmp_path / "market"),
        "daily_dir": str(tmp_path / "daily"),
        "factor_dir": str(tmp_path / "factor"),
        "output_dir": str(tmp_path / "out"),
    })
    svc = ts.TaskService(cfg)
    task = SimpleNamespace(id=1, ref_id=1, params={})
    res = svc._run_ingest(task, tmp_path / "task.log")
    assert res["status"] == "success"
    assert captured["daily_dir"] == tmp_path / "daily"
    assert captured["factor_dir"] == tmp_path / "factor"


class _FakeDS:
    """伪数据源：记录 connect 次数，可模拟登录失败。"""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def connect(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("baostock 登录失败: 连接超时")


def _cfg(tmp_path):
    return Config({
        "data_dir": str(tmp_path / "market"),
        "daily_dir": str(tmp_path / "daily"),
        "factor_dir": str(tmp_path / "factor"),
        "output_dir": str(tmp_path / "out"),
    })


def test_run_ingest_logs_in_at_task_start(tmp_path, monkeypatch):
    """系统启动不登录；每次任务开始必须先建立一次 baostock 会话。"""
    monkeypatch.setattr(ts.ingest, "run_fetch",
                        lambda *a, **k: {"status": "success", "processed_dates": 0,
                                         "skipped_dates": 0, "rows": 0})
    monkeypatch.setattr(ts.ingest, "_trade_dates", lambda s, e: ["2024-01-02"])
    monkeypatch.setattr(ts, "SessionLocal", lambda: _FakeDB())

    ds = _FakeDS()
    svc = ts.TaskService(_cfg(tmp_path), ds=ds)
    res = svc._run_ingest(SimpleNamespace(id=1, ref_id=1, params={}),
                          tmp_path / "task.log")
    assert res["status"] == "success"
    assert ds.calls == 1


def test_run_ingest_fails_loudly_when_login_fails(tmp_path, monkeypatch):
    """登录失败必须让任务失败并在日志留痕，而不是静默跑出 0 条后标记成功。"""
    called = {"run": 0}
    monkeypatch.setattr(ts.ingest, "run_fetch",
                        lambda *a, **k: called.update(run=1) or {})
    monkeypatch.setattr(ts.ingest, "_trade_dates", lambda s, e: [])
    monkeypatch.setattr(ts, "SessionLocal", lambda: _FakeDB())

    log_path = tmp_path / "task.log"
    svc = ts.TaskService(_cfg(tmp_path), ds=_FakeDS(fail=True))
    res = svc._run_ingest(SimpleNamespace(id=2, ref_id=1, params={}), log_path)

    assert res["status"] == "failed"
    assert called["run"] == 0
    assert "登录失败" in log_path.read_text(encoding="utf-8")


def test_run_report_logs_in_at_task_start(tmp_path, monkeypatch):
    """报告任务同样在开始时登录（它要拉指数数据）。"""
    from app.services import report_service
    monkeypatch.setattr(report_service, "run_daily_report",
                        lambda *a, **k: {"status": "success", "output_files": []})
    monkeypatch.setattr(ts, "SessionLocal", lambda: _FakeDB())

    ds = _FakeDS()
    svc = ts.TaskService(_cfg(tmp_path), ds=ds)
    res = svc._run_report(SimpleNamespace(id=3, ref_id=0, params={}),
                          tmp_path / "task.log")
    assert res["status"] == "success"
    assert ds.calls == 1