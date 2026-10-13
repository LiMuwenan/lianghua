# -*- coding: utf-8 -*-
"""任务日志实时可读测试：运行中任务应在启动时即把 log_path 落库。

背景：日志文件在子进程/摄取过程中持续追加写入，但 /api/tasks/{id}/log 依赖
DB 里的 task.log_path。若 log_path 直到任务结束才落库，运行期间前端刷新日志
只能读到空内容，看不到最新日志。
"""
import sys
sys.path.insert(0, "d:/Project/lianghua")

from types import SimpleNamespace

from app.config import Config
from app.services import task_service as ts


class _FakeQuery:
    def __init__(self, task):
        self._task = task

    def get(self, *_):
        return self._task

    def all(self):
        return []


class _FakeDB:
    def __init__(self, task):
        self._task = task

    def query(self, *_):
        return _FakeQuery(self._task)

    def commit(self):
        pass

    def close(self):
        pass


def test_execute_task_persists_log_path_before_running(tmp_path, monkeypatch):
    """任务开跑前就应把 log_path 落库，前端运行中才能实时读到日志。"""
    cfg = Config({
        "data_dir": str(tmp_path / "market"),
        "output_dir": str(tmp_path / "out"),
    })
    svc = ts.TaskService(cfg)

    task = SimpleNamespace(
        id=7, kind="ingest", ref_id=1, params={}, status="queued",
        started_at=None, finished_at=None, log_path="",
        exit_code=None, result_summary=None, output_files=None,
    )
    db = _FakeDB(task)
    monkeypatch.setattr(ts, "SessionLocal", lambda: db)

    seen = {}

    def fake_run_ingest(t, log_path):
        # 运行期间，DB 中的 log_path 应已指向真实日志文件
        seen["during_run"] = t.log_path
        return {"exit_code": 0, "status": "success", "summary": {}}

    monkeypatch.setattr(svc, "_run_ingest", fake_run_ingest)
    svc._execute_task(7)

    assert seen["during_run"], "运行中任务的 log_path 不应为空"
    assert str(seen["during_run"]).endswith("task_7.log")
