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