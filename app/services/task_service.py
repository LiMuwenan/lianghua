# -*- coding: utf-8 -*-
"""任务执行服务：串行 FIFO 队列 + subprocess 隔离运行真实脚本。

设计要点（对齐方案文档第 4/11 节）：
- 同一时刻只执行一个任务（串行），避免子进程互相踩数据目录。
- 任务状态：queued → running → success/failed/partial_failed。
- 实时抓取 stdout/stderr 写入 outputs/logs/{task_id}.log，UTF-8。
- 参数覆盖：临时参数化副本，在原脚本目录作为工作目录运行，原文件不动。
- 单任务超时后 kill 子进程（subprocess 超时 + kill）。
"""
import logging
import queue
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import Config, load_config
from ..database import SessionLocal
from ..models import Strategy, TaskRun
from . import param_inject

logger = logging.getLogger("app.task_service")


class TaskService:
    """全局唯一任务调度器。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._q: queue.Queue = queue.Queue()
        self.running_task_id: int | None = None
        # 各任务最后日志终点，供前端轮询进度
        self._worker_stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="task-worker")
        self._lock = threading.Lock()
        # 任务终止支持：task_id -> 子进程 / 取消事件
        self._procs: dict[int, "subprocess.Popen"] = {}
        self._cancel_events: dict[int, threading.Event] = {}
        self._reg_lock = threading.Lock()

    def start(self):
        self._thread.start()

    def stop(self):
        self._worker_stop.set()
        self._thread.join(timeout=2)

    # ---------- 对外：提交任务 ----------
    def enqueue(self, db: Session, strategy, params: dict = None) -> TaskRun:
        """创建 queued 状态任务并入队。返回 TaskRun。"""
        task = TaskRun(
            kind=strategy.kind,
            ref_id=strategy.id,
            ref_name=strategy.name,
            status="queued",
            params=params or {},
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        self._q.put(task.id)
        return task

    # ---------- 任务终止支持 ----------
    def _register(self, task_id: int, proc: "subprocess.Popen" = None) -> None:
        """登记任务的取消事件与（可选）子进程句柄。"""
        with self._reg_lock:
            self._cancel_events[task_id] = threading.Event()
            if proc is not None:
                self._procs[task_id] = proc

    def _unregister(self, task_id: int) -> None:
        with self._reg_lock:
            self._procs.pop(task_id, None)
            self._cancel_events.pop(task_id, None)

    def cancel_event(self, task_id: int) -> threading.Event:
        """返回任务取消事件（不存在则返回新的空事件，避免误导）。"""
        with self._reg_lock:
            return self._cancel_events.get(task_id, threading.Event())

    def terminate(self, task_id: int) -> bool:
        """请求终止运行中任务：置取消事件；若是子进程则立即 kill。返回是否命中运行中任务。"""
        with self._reg_lock:
            ev = self._cancel_events.get(task_id)
            proc = self._procs.get(task_id)
        if ev is None and proc is None:
            return False
        if ev is not None:
            ev.set()
        if proc is not None:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        return True

    # ---------- 内部：工作线程 ----------
    def _loop(self):
        while not self._worker_stop.is_set():
            try:
                tid = self._q.get(timeout=1)
            except queue.Empty:
                continue
            self._execute_task(tid)
            with self._lock:
                self.running_task_id = None

    def _execute_task(self, task_id: int):
        db = SessionLocal()
        try:
            task = db.query(TaskRun).get(task_id)
            if task is None:
                return
            task.status = "running"
            task.started_at = datetime.now()
            db.commit()
            with self._lock:
                self.running_task_id = task_id

            # 准备日志文件
            self.cfg.logs_dir.mkdir(parents=True, exist_ok=True)
            log_path = self.cfg.logs_dir / f"task_{task_id}.log"

            # 取策略定义
            strategy = db.query(Strategy).get(task.ref_id)
            if strategy is None:
                task.status = "failed"
                task.finished_at = datetime.now()
                task.result_summary = {"error": f"引用的脚本登记不存在: ref_id={task.ref_id}"}
                db.commit()
                return

            result = self._run_subprocess(
                strategy, log_path, params=task.params or {}, timeout=self.cfg.task_timeout_sec
            )

            task.finished_at = datetime.now()
            task.log_path = str(log_path)
            task.exit_code = result["exit_code"]
            task.result_summary = result["summary"]
            task.status = result["status"]
            db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.exception("任务 %s 执行异常", task_id)
            try:
                task = db.query(TaskRun).get(task_id)
                task.status = "failed"
                task.finished_at = datetime.now()
                task.result_summary = {"error": str(exc)}
                db.commit()
            except Exception:  # noqa
                db.rollback()
        finally:
            db.close()

    def _run_subprocess(self, strategy, log_path: Path, params: dict, timeout: int) -> dict:
        """构造并运行临时参数化副本，流式写日志。"""
        script_rel = strategy.script_path
        script_abs = self.cfg.absolute_script_path(script_rel)
        if not script_abs.is_file():
            return {
                "exit_code": -1,
                "status": "failed",
                "summary": {"error": f"脚本不存在: {script_abs}"},
            }
        schema = strategy.params_schema or {}
        tmp_path = None
        try:
            tmp_path = param_inject.build_temp_copy(script_abs, params or {}, schema)
            workdir = str(script_abs.parent)
            cmd = [sys.executable or "python", str(tmp_path)]
            header = f"$ 运行: {' '.join(cmd)}\n$ 工作目录: {workdir}\n"
            if params:
                header += f"$ 参数覆盖: {params}\n"
            header += "\n"
            log_path.write_text(header, encoding="utf-8")

            proc = subprocess.Popen(
                cmd,
                cwd=workdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            # 登记本次运行的任务号与其子进程，供 terminate() 取消
            self._register(self.running_task_id, proc)
            stdout = proc.stdout
            lines = []
            with open(log_path, "a", encoding="utf-8", errors="replace") as f:
                deadline = datetime.now().timestamp() + (timeout if timeout and timeout > 0 else 7200)
                while True:
                    # 每次循环先判用户终止（优先级最高，避免被 kill 后的 poll 状态早退）
                    if self._cancel_events.get(self.running_task_id, threading.Event()).is_set():
                        if proc.poll() is None:
                            proc.kill()
                            proc.wait()
                        f.write("\n[用户终止]\n")
                        return {
                            "exit_code": None,
                            "status": "aborted",
                            "summary": {"canceled": True},
                        }
                    line = stdout.readline()
                    if line:
                        text = line.decode("utf-8", errors="replace")
                        f.write(text)
                        f.flush()
                        lines.append(text)
                    elif proc.poll() is not None:
                        # 进程已结束，清空剩余
                        break
                    else:
                        # 无输出且仍在运行
                        if datetime.now().timestamp() > deadline:
                            proc.kill()
                            proc.wait()
                            f.write("\n[超时强制终止]\n")
                            return {
                                "exit_code": None,
                                "status": "failed",
                                "summary": {"error": f"任务超时(>{timeout}s)，已终止", "timeout": True},
                            }
                        # 短暂阻塞等待下一行输出
                        try:
                            line = stdout.readline()
                        except Exception:  # noqa
                            break
                    # 进程结束且无更多输出则退出
                    if proc.poll() is not None and not line:
                        break
                # 兜底 drain
                for leftover in stdout:
                    text = leftover.decode("utf-8", errors="replace")
                    f.write(text)
                    f.flush()
                # 追加 exit 信息
                f.write(f"\n[exit_code={proc.poll()}]\n")
            code = proc.poll()
            status = "success" if code == 0 else "failed"
            return {
                "exit_code": code,
                "status": status,
                "summary": {"lines": len(lines), "exit_code": code},
            }
        finally:
            self._unregister(self.running_task_id)
            if tmp_path and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)


# 全局单例（由 main 绑定）
_service: TaskService | None = None


def init_task_service(cfg: Config) -> TaskService:
    global _service
    if _service is None:
        _service = TaskService(cfg)
    return _service


def get_service() -> TaskService:
    global _service
    if _service is None:
        _service = TaskService(load_config())
    return _service