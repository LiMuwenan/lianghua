# -*- coding: utf-8 -*-
"""任务终止行为测试：terminate 命中运行中任务、置取消事件、返回结果。"""
import sys
import threading
import time
sys.path.insert(0, "d:/Project/lianghua")

from app.config import load_config
from app.services.task_service import TaskService


def test_terminate_cancel_event():
    """terminate 未登记返回 False；登记后返回 True 且取消事件被置位。"""
    svc = TaskService(load_config())
    assert svc.terminate(999) is False

    svc._register(100)
    assert svc.terminate(100) is True
    assert svc.cancel_event(100).is_set()
    svc._unregister(100)
    assert svc.terminate(100) is False


def test_terminate_kills_subprocess(tmp_path):
    """运行一个 sleep 子进程，terminate 应 kill 并返回 aborted 摘要。"""
    svc = TaskService(load_config())
    script = tmp_path / "sleep_script.py"
    script.write_text("import time; time.sleep(30)\n", encoding="utf-8")
    result = {}

    def worker():
        svc._register(200)
        ev = svc.cancel_event(200)
        # 模拟 _run_subprocess 的轮询：等待取消事件或进程结束
        proc = None
        try:
            import subprocess
            proc = subprocess.Popen(
                [sys.executable, str(script)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            svc._procs[200] = proc
            deadline = time.time() + 60
            while proc.poll() is None:
                # 每次循环先判取消（与 task_service._run_subprocess 一致）
                if ev.is_set():
                    proc.kill()
                    proc.wait()
                    result["status"] = "aborted"
                    return
                if time.time() > deadline:
                    result["status"] = "timeout"
                    return
                time.sleep(0.05)
            if ev.is_set():
                result["status"] = "aborted"
        finally:
            svc._unregister(200)

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.5)
    assert svc.terminate(200) is True
    t.join(timeout=5)
    assert result.get("status") == "aborted"