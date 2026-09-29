# -*- coding: utf-8 -*-
"""定时调度：APScheduler 接入 cron_task，应用重启后从库恢复已启用任务。

设计（对齐方案 5.3 / P1）：
- BackgroundScheduler 进程内调度，cron_expr 为标准 5 字段（minute hour dom month dow）。
- 触发统一走任务执行路径（串行队列/日志/手动终止）：_trigger 回调由 API 接线层绑定，
  落到 TaskService（kind=ingest → 触发「获取数据」；kind=strategy → 触发回测）。
- 每次触发落一条 task_run，并刷新 cron_task.last_run_at / next_run_at。
- CRUD 后调用 sync_all(db) 重建调度（任务量小，重建代价可忽略）。
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from ..config import Config
from ..database import SessionLocal
from ..models import CronTask

logger = logging.getLogger("app.scheduler")

TZ = ZoneInfo("Asia/Shanghai")


def compute_next(cron_expr: str):
    """由 cron 表达式计算下次触发时间（naive datetime），无效/空返回 None。

    独立于调度器 job 状态，重启/启用即刻可算，供 next_run_at 展示。
    """
    excl = (cron_expr or "").strip()
    if not excl:
        return None
    try:
        trig = CronTrigger.from_crontab(excl, timezone=TZ)
        nrt = trig.get_next_fire_time(None, datetime.now(TZ))
        return nrt.replace(tzinfo=None) if nrt else None
    except Exception:  # noqa: BLE001
        return None


class CronScheduler:
    """全局唯一调度器，负责少数 cron 任务的注册/恢复与触发回调。"""

    def __init__(self):
        self.scheduler = BackgroundScheduler(timezone=TZ)
        self._trigger_cb = None          # callable(cron_task) -> task_id

    def bind(self, cb) -> None:
        """绑定触发回调（由 main 接线层提供，落到 TaskService）。"""
        self._trigger_cb = cb

    def start(self) -> None:
        self.scheduler.start()

    def shutdown(self) -> None:
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            pass

    # ---------- 注册 / 恢复 ----------
    def _add(self, ct: CronTask):
        """为单个启用任务注册 job。未启用/无有效 cron 则跳过。"""
        if not ct.enabled or not ct.cron_expr:
            return None
        try:
            trigger = CronTrigger.from_crontab(ct.cron_expr, timezone=TZ)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cron 表达式无效，跳过 %s: %s", ct.cron_expr, exc)
            return None
        return self.scheduler.add_job(
            self._run, trigger,
            args=[ct.id], id=str(ct.id), replace_existing=True,
        )

    def _remove(self, cron_id: int) -> None:
        jid = str(cron_id)
        if self.scheduler.get_job(jid):
            try:
                self.scheduler.remove_job(jid)
            except Exception:  # noqa: BLE001
                pass

    def _run(self, cron_id: int) -> None:
        """被 APScheduler 触发：落到 TaskService，并刷新 last/next_run_at。"""
        db = SessionLocal()
        try:
            ct = db.query(CronTask).get(cron_id)
            if ct is None or not ct.enabled:
                return
            if self._trigger_cb:
                self._trigger_cb(ct)
            ct.last_run_at = datetime.now()
            self._refresh_next_run(db, ct)
            db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("cron %s 触发异常", cron_id)
            db.rollback()
        finally:
            db.close()

    def _refresh_next_run(self, db, ct: CronTask) -> None:
        ct.next_run_at = compute_next(ct.cron_expr) if ct.enabled else None

    def sync_all(self, db: Session) -> None:
        """按库重建调度：清空重建启用任务，并刷新所有 next_run_at。"""
        # 移除现有 job
        for job in list(self.scheduler.get_jobs()):
            try:
                job.remove()
            except Exception:  # noqa: BLE001
                pass
        for ct in db.query(CronTask).filter(CronTask.enabled.is_(True)).all():
            self._add(ct)
        for ct in db.query(CronTask).all():
            self._refresh_next_run(db, ct)
        db.commit()

    def restore(self, db: Session) -> None:
        """应用启动时恢复：重建调度（等价于 sync_all）。"""
        self.sync_all(db)

    def next_run(self, cron_id: int):
        job = self.scheduler.get_job(str(cron_id))
        nrt = job.next_run_time if job else None
        return nrt.replace(tzinfo=None) if nrt else None


# 全局单例
_scheduler: CronScheduler | None = None


def get_scheduler() -> CronScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = CronScheduler()
    return _scheduler