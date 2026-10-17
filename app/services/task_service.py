# -*- coding: utf-8 -*-
"""任务执行服务：串行 FIFO 队列 + subprocess 隔离运行真实脚本。

设计要点（对齐方案文档第 4/11 节）：
- 同一时刻只执行一个任务（串行），避免子进程互相踩数据目录。
- 任务状态：queued → running → success/failed/partial_failed/skipped。
- 实时抓取 stdout/stderr 写入 outputs/logs/{task_id}.log，UTF-8。
- 参数覆盖：临时参数化副本，在原脚本目录作为工作目录运行，原文件不动。
- 单任务超时后 kill 子进程（subprocess 超时 + kill）。
"""
import logging
import queue
import subprocess
import sys
import threading
from datetime import date, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import Config, load_config
from ..database import SessionLocal
from ..models import Dataset, StockFreshness, Strategy, TaskRun
from . import ingest, param_inject

logger = logging.getLogger("app.task_service")


def _resolve_start(start_arg: str, ds_latest: str, freshness: dict) -> str:
    """解析无/有显式日期时的摄取起点：显式参数 > dataset 最新位点 > 每股断点全局最小 > 2005-01-01。"""
    if start_arg and start_arg.strip():
        return start_arg.strip()
    if ds_latest and ds_latest.strip():
        return ds_latest.strip()
    return ingest._global_min(freshness) or "2005-01-01"


def _freshness_from_file(p: Path):
    """读 parquet 的 date 列得到 (latest_date_str 或 None, row_count)，损坏则 (None,0)。"""
    try:
        from . import parquet_store as store
        df = store.read_stock(p.parent, p.stem)
        if df.empty:
            return None, 0
        return str(df["date"].max()), int(len(df))
    except Exception:  # noqa: BLE001
        return None, 0


def refresh_freshness(data_dir: Path) -> None:
    """扫描目录内 parquet 的真实位点更新断点基线（不造假值）。"""
    db = SessionLocal()
    try:
        files = sorted(data_dir.glob("*.parquet"))
        for p in files:
            code = p.stem
            latest, rows = _freshness_from_file(p)
            existing = db.query(StockFreshness).filter(StockFreshness.code == code).first()
            if latest is None and rows == 0:
                continue
            if existing is None:
                existing = StockFreshness(code=code)
                db.add(existing)
            if latest:
                existing.latest_date = date.fromisoformat(latest)
            existing.row_count = rows
        db.commit()
    finally:
        db.close()


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

    def enqueue_ingest(self, db: Session, ds_name: str, ds_id: int,
                       params: dict = None) -> TaskRun:
        """创建内建摄取任务（kind=ingest），queued 并入队。ref_id 指向 dataset.id。"""
        params = dict(params or {})
        task = TaskRun(
            kind="ingest",
            ref_id=ds_id,
            ref_name=ds_name,
            status="queued",
            params=params,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        self._q.put(task.id)
        return task

    def enqueue_report(self, db: Session, params: dict = None) -> TaskRun:
        """创建每日大盘汇总任务（kind=report），queued 并入队。ref_id=0（无外部引用）。"""
        task = TaskRun(
            kind="report",
            ref_id=0,
            ref_name="每日大盘汇总",
            status="queued",
            params=params or {},
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        self._q.put(task.id)
        return task

    # ---------- 任务终止支持 ----------
    def _register(self, task_id: int, proc: "subprocess.Popen" = None) -> threading.Event:
        """登记任务的取消事件与（可选）子进程句柄。取消事件已存在则复用（不覆盖，
        避免运行期间被重新登记后丢失已置位的终止请求）。"""
        with self._reg_lock:
            ev = self._cancel_events.get(task_id)
            if ev is None:
                ev = threading.Event()
                self._cancel_events[task_id] = ev
            if proc is not None:
                self._procs[task_id] = proc
            return ev

    def _unregister(self, task_id: int) -> None:
        with self._reg_lock:
            self._procs.pop(task_id, None)
            self._cancel_events.pop(task_id, None)

    def cancel_event(self, task_id: int) -> threading.Event:
        """返回任务取消事件（不存在则返回新的空事件，避免误导）。"""
        with self._reg_lock:
            return self._cancel_events.get(task_id, threading.Event())

    def terminate(self, task_id: int) -> bool:
        """请求终止任务：运行中 → 置取消事件并 kill 子进程；排队中/进程残留的行 → 落库标记已终止。

        返回是否真的命中一个可终止的任务（未命中时调用方回 409）。
        """
        with self._reg_lock:
            ev = self._cancel_events.get(task_id)
            proc = self._procs.get(task_id)
        hit = False
        if ev is not None:
            ev.set()
            hit = True
        if proc is not None:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            hit = True
        if hit:
            return True
        # 未在本次进程运行中登记：可能是排队中，或上个进程被中断后残留的 running 行
        return self._abort_pending_row(task_id)

    def _abort_pending_row(self, task_id: int) -> bool:
        """兜底终止：把 queued/running 状态的行直接落库标记为已终止。"""
        db = SessionLocal()
        try:
            task = db.query(TaskRun).get(task_id)
            if task is None or task.status not in ("queued", "running"):
                return False
            task.status = "aborted"
            task.finished_at = datetime.now()
            task.result_summary = {"canceled": True,
                                  "reason": "未在本次进程运行中，已直接标记终止"}
            db.commit()
            logger.info("任务 %s 未在运行中，已兜底标记为已终止", task_id)
            return True
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("兜底终止任务 %s 失败", task_id)
            return False
        finally:
            db.close()

    def reconcile_orphans(self) -> int:
        """启动清理：上一个进程被中断后遗留的 queued/running 行本进程不会执行，
        统一落库标记为已终止（诚实标注，避免列表长期停留在「运行中」）。返回清理条数。"""
        db = SessionLocal()
        try:
            rows = (db.query(TaskRun)
                    .filter(TaskRun.status.in_(("queued", "running")))
                    .all())
            if not rows:
                return 0
            now = datetime.now()
            for task in rows:
                task.status = "aborted"
                task.finished_at = now
                task.result_summary = {"canceled": True, "reason": "服务重启，前次运行中断"}
            db.commit()
            logger.info("启动清理：%d 个中断任务已标记为已终止", len(rows))
            return len(rows)
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("启动清理中断任务失败")
            return 0
        finally:
            db.close()

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
            # 排队期间已被终止（或其它终态）的任务不再执行，避免被覆盖回 running
            if task.status != "queued":
                logger.info("任务 %s 状态已为 %s，跳过执行", task_id, task.status)
                return
            # 先登记取消事件（早于 running 落库），保证任何时刻终止请求都能被接住
            self._register(task_id)
            # 准备日志文件
            self.cfg.logs_dir.mkdir(parents=True, exist_ok=True)
            log_path = self.cfg.logs_dir / f"task_{task_id}.log"

            task.status = "running"
            task.started_at = datetime.now()
            # 开跑前先落库日志路径，保证运行期间 /log 接口能实时读到追加中的日志
            task.log_path = str(log_path)
            db.commit()
            with self._lock:
                self.running_task_id = task_id

            if task.kind == "ingest":
                result = self._run_ingest(task, log_path)
            elif task.kind == "report":
                result = self._run_report(task, log_path)
            else:
                # 取策略定义
                strategy = db.query(Strategy).get(task.ref_id)
                if strategy is None:
                    task.status = "failed"
                    task.finished_at = datetime.now()
                    task.result_summary = {"error": f"引用的脚本登记不存在: ref_id={task.ref_id}"}
                    db.commit()
                    return
                result = self._run_subprocess(
                    task_id, strategy, log_path, params=task.params or {},
                    timeout=self.cfg.task_timeout_sec,
                )

            task.finished_at = datetime.now()
            task.exit_code = result["exit_code"]
            task.result_summary = result["summary"]
            if result.get("output_files"):
                task.output_files = result["output_files"]
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
            self._unregister(task_id)
            db.close()

    def _run_ingest(self, task, log_path: Path) -> dict:
        """内建获取数据：调用 ingest.run_fetch，协作式取消走任务取消事件。

        数据源钩子（_fetch_daily_bars/_fetch_factors/_trade_dates）已在 main 接线层绑定。
        起始日期：task.params['start_date'] 可空；留空则用该 dataset 的 latest_data_date（最新整体位点）
        续抓新交易日；dataset 起点为空时兜底按每股断点位点最小续传（空库默认 2005-01-01）。
        """
        params = task.params or {}
        start_arg = str(params.get("start_date") or "").strip()
        self._register(task.id)
        cancel_flag = self.cancel_event(task.id).is_set
        try:
            trade_dates = ingest._trade_dates
            data_dir = self.cfg.data_dir
            daily_dir = self.cfg.daily_dir
            factor_dir = self.cfg.factor_dir
            # 每股断点位点（来自元库 stock_freshness），用于跳过已有日期
            freshness = self._freshness_map()
            # 各股既有 parquet 末行因子，作为增量起始因子（衔接历史前向填充）
            initial_factors = self._initial_factors(data_dir, freshness)
            today = date.today().isoformat()
            # 起始日期：优先用任务的 start_date；未指定则用该 dataset 的 latest_data_date
            # （最新整体位点，续抓新交易日；避免被长期停更/退市股拉回很久以前）。
            ds_latest = ""
            _db = SessionLocal()
            try:
                _ds_row = _db.query(Dataset).get(task.ref_id)
                if _ds_row is not None:
                    ds_latest = (getattr(_ds_row, "latest_data_date", "") or "").strip()
            finally:
                _db.close()
            if start_arg:
                start = start_arg
            else:
                start = _resolve_start(start_arg, ds_latest, freshness)
            dates = trade_dates(start, today)

            log_path.write_text(
                f"$ 获取数据 start={start} (自动=dataset断点/每股断点/2005) dates={len(dates)} 天\n"
                f"$ 原始层 daily_dir={daily_dir} factor_dir={factor_dir}\n",
                encoding="utf-8")

            def _append_log(line: str) -> None:
                # 摄取进度写入任务日志文件，前端任务日志实时可读
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")

            res = ingest.run_fetch(data_dir, dates, cancel_flag, freshness,
                                   initial_factors, log=_append_log,
                                   daily_dir=daily_dir, factor_dir=factor_dir)

            status = res.get("status", "failed")
            summary = {"start_date": start, "rows": res.get("rows", 0),
                       "processed_dates": res.get("processed_dates", 0),
                       "skipped_dates": res.get("skipped_dates", 0)}
            if status == "aborted":
                summary["canceled"] = True
            elif status == "success":
                # 更新断点基线：重扫目录内所有 parquet 的位点（真实扫描，不造假）
                self._refresh_freshness(data_dir)
            return {
                "exit_code": 0 if status == "success" else None,
                "status": status,
                "summary": summary,
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("内建摄取任务 %s 异常", task.id)
            return {
                "exit_code": None,
                "status": "failed",
                "summary": {"error": str(exc)},
            }
        finally:
            self._unregister(task.id)

    def _run_report(self, task, log_path: Path) -> dict:
        """每日大盘汇总：走 report_service，日志写任务日志文件；支持协作式取消。"""
        from . import report_service
        self._register(task.id)
        cancel_flag = self.cancel_event(task.id).is_set
        try:
            log_path.write_text("$ 每日大盘汇总\n", encoding="utf-8")

            def _log(line: str) -> None:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")

            db = SessionLocal()
            try:
                res = report_service.run_daily_report(
                    self.cfg, db, log=_log, send=True, cancel=cancel_flag)
            finally:
                db.close()
            status = res.get("status", "failed")
            summary = {k: v for k, v in res.items() if k != "metrics"}
            if status == "aborted":
                summary["canceled"] = True
            return {
                "exit_code": 0 if status == "success" else None,
                "status": status,
                "summary": summary,
                "output_files": res.get("output_files") or [],
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("报告任务 %s 异常", task.id)
            return {"exit_code": None, "status": "failed",
                    "summary": {"error": str(exc)}, "output_files": []}
        finally:
            self._unregister(task.id)

    def _freshness_map(self) -> dict:
        """读元库每股断点位点 → {code: 'YYYY-MM-DD'}，供 run_fetch 跳过已有日期。"""
        db = SessionLocal()
        try:
            return {
                r.code: (r.latest_date.isoformat() if r.latest_date else None)
                for r in db.query(StockFreshness).all()
            }
        finally:
            db.close()

    def _initial_factors(self, data_dir: Path, freshness: dict) -> dict:
        """读各股既有 parquet 末行因子，作为增量起始因子（衔接历史前向填充）。"""
        from . import parquet_store as store
        out = {}
        for code in freshness:
            p = store.stock_path(data_dir, code)
            if not p.exists():
                continue
            try:
                df = store.read_stock(data_dir, code)
                if df.empty:
                    continue
                row = df.iloc[-1]
                out[code] = {"qfq_factor": float(row["qfq_factor"]),
                             "hfq_factor": float(row["hfq_factor"])}
            except Exception:  # noqa: BLE001
                continue
        return out

    def _refresh_freshness(self, data_dir: Path) -> None:
        refresh_freshness(data_dir)

    def _run_subprocess(self, task_id: int, strategy, log_path: Path, params: dict,
                        timeout: int) -> dict:
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
            cancel_event = self._register(task_id, proc)
            stdout = proc.stdout
            lines = []
            with open(log_path, "a", encoding="utf-8", errors="replace") as f:
                deadline = datetime.now().timestamp() + (timeout if timeout and timeout > 0 else 7200)
                while True:
                    # 每次循环先判用户终止（优先级最高，避免被 kill 后的 poll 状态早退）
                    if cancel_event.is_set():
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
            self._unregister(task_id)
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