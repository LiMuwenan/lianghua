# -*- coding: utf-8 -*-
"""报告 REST 接口：preview 返回 HTML、run 入队、latest 返回最近记录。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.api import reports


def _client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/meta.db",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    DB = sessionmaker(bind=engine)

    app = FastAPI()
    app.include_router(reports.router)

    def _dep():
        db = DB()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _dep
    return TestClient(app), DB


def test_preview_returns_html(tmp_path, monkeypatch):
    client, DB = _client(tmp_path, monkeypatch)

    class FakeGen:
        def generate(self, cfg, db, td):
            return {"trade_date": td, "html": "<html>ok</html>", "metrics": {}}

    monkeypatch.setattr(reports.report_service, "resolve_trade_date", lambda *a, **k: "2026-10-13")
    monkeypatch.setattr(reports.report_service, "generate", FakeGen().generate)
    r = client.get("/api/reports/daily/preview")
    assert r.status_code == 200
    assert "ok" in r.json()["html"]
    assert r.json()["trade_date"] == "2026-10-13"


def test_run_enqueues_task(tmp_path, monkeypatch):
    client, DB = _client(tmp_path, monkeypatch)

    class FakeTask:
        id = 42
        kind = "report"

    class FakeSvc:
        def enqueue_report(self, db, params=None):
            return FakeTask()

    monkeypatch.setattr(reports, "get_service", lambda: FakeSvc())
    r = client.post("/api/reports/daily/run")
    assert r.status_code == 200
    assert r.json() == {"task_id": 42, "kind": "report"}


def test_latest_returns_last_report(tmp_path, monkeypatch):
    client, DB = _client(tmp_path, monkeypatch)
    db = DB()
    from app.models import TaskRun
    db.add(TaskRun(kind="report", ref_id=0, ref_name="每日大盘汇总",
                   status="success", result_summary={"trade_date": "2026-10-13"},
                   output_files=["outputs/reports/2026-10-13.html"]))
    db.add(TaskRun(kind="ingest", ref_id=1, ref_name="x", status="success"))
    db.commit()
    db.close()

    r = client.get("/api/reports/daily/latest")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    assert body["output_files"] == ["outputs/reports/2026-10-13.html"]
