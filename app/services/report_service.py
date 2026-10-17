# -*- coding: utf-8 -*-
"""每日大盘汇总编排：就绪检查 → 指数补抓 → 汇总 → 渲染归档 → 发送。

- 最近交易日：由 ingest._trade_dates 钩子取（接线层已绑真实数据源）。
- 就绪条件：个股最新日期 == 交易日，且至少一只配置指数有该日数据。
- 指数补抓为 best-effort：缺失的指数跳过，不阻断；缺失板块渲染为「数据缺失」。
- 发送缺配置/缺授权码时记为未发送（不报错），状态仍为 success。
"""
import logging
import os
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import func

from ..models import CronTask, StockFreshness
from . import email_sender, index_ingest, index_store, market_summary, report_render

logger = logging.getLogger("app.services.report_service")

REPORT_REF_NAME = "每日大盘汇总"


def resolve_trade_date(today: str | None = None, lookback_days: int = 15):
    """最近交易日（<= today）：走 ingest._trade_dates 钩子；取不到返回 None。"""
    from . import ingest
    today = today or date.today().isoformat()
    start = (date.fromisoformat(today) - timedelta(days=lookback_days)).isoformat()
    days = ingest._trade_dates(start, today)
    return days[-1] if days else None


def _stock_latest(db, data_dir) -> str | None:
    """个股最新日期：优先元库断点表最大值；为空时用 DuckDB 全量取目录内 parquet 的 date 最大值。"""
    try:
        row = db.query(func.max(StockFreshness.latest_date)).scalar()
    except Exception:  # noqa: BLE001
        row = None
    if row is not None:
        return row.isoformat() if hasattr(row, "isoformat") else str(row)
    if not market_summary._has_parquet(data_dir):
        return None
    import duckdb
    con = duckdb.connect()
    try:
        v = con.execute(
            f"SELECT MAX(date) FROM read_parquet('{market_summary._parquet_glob(data_dir)}')"
        ).fetchone()[0]
    finally:
        con.close()
    return None if v is None else str(v)


def readiness_check(cfg, db, trade_date: str) -> dict:
    """返回 {ready, stock_latest, index_ready, reasons}。"""
    s_latest = _stock_latest(db, cfg.data_dir)
    idx_ready = [c for c in cfg.report_index_codes
                 if index_store.latest_date(cfg.index_dir, c) == trade_date]
    reasons = []
    if s_latest != trade_date:
        reasons.append(f"个股最新日期 {s_latest or '无'} != 交易日 {trade_date}")
    if not idx_ready:
        reasons.append(f"无指数数据落在交易日 {trade_date}")
    return {"ready": not reasons, "stock_latest": s_latest,
            "index_ready": idx_ready, "reasons": reasons}


def _stock_maps(db):
    """从 stock 表取 {code: industry} 与 {code: name}。表空/异常返回空 dict。"""
    from ..models import Stock
    try:
        rows = db.query(Stock).all()
    except Exception:  # noqa: BLE001
        return {}, {}
    return ({r.code: (r.industry or "") for r in rows},
            {r.code: (r.name or "") for r in rows})


def build_metrics(cfg, db, trade_date: str) -> dict:
    """汇总指标：跨股读 parquet + 指数 + 行业/名称映射。"""
    industry_map, name_map = _stock_maps(db)
    return market_summary.build_summary(
        cfg.data_dir, cfg.index_dir, trade_date,
        cfg.report_index_codes, industry_map, name_map)


def render_only(cfg, db, trade_date: str) -> dict:
    """仅汇总 + 渲染，不落盘（供预览）。返回 {trade_date, metrics, html}。"""
    metrics = build_metrics(cfg, db, trade_date)
    html = report_render.render_report(metrics, trade_date, cfg.report_subject_prefix)
    return {"trade_date": trade_date, "metrics": metrics, "html": html}


def generate(cfg, db, trade_date: str) -> dict:
    """汇总 + 渲染 + 归档，返回 {trade_date, metrics, html, html_path}。"""
    out = render_only(cfg, db, trade_date)
    out_dir = Path(cfg.output_dir) / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"{trade_date}.html"
    html_path.write_text(out["html"], encoding="utf-8")
    out["html_path"] = str(html_path)
    return out


def _send(cfg, trade_date: str, html: str, log) -> dict:
    """按配置发送；缺配置/失败记原因，不抛异常。"""
    if not cfg.report_recipients:
        log("未配置收件人(report.recipients)，跳过发送")
        return {"sent": False, "reason": "未配置 report.recipients"}
    password = os.environ.get(cfg.smtp_password_env, "")
    if not (cfg.smtp_username and password):
        log(f"缺 smtp.username 或环境变量 {cfg.smtp_password_env}，跳过发送")
        return {"sent": False, "reason": f"缺 smtp.username 或环境变量 {cfg.smtp_password_env}"}
    subject = f"{cfg.report_subject_prefix} | {trade_date}"
    try:
        email_sender.send_email(cfg, cfg.report_recipients, subject, html, password)
        log(f"邮件已发送至 {cfg.report_recipients}")
        return {"sent": True, "recipients": list(cfg.report_recipients)}
    except Exception as exc:  # noqa: BLE001
        log(f"邮件发送失败：{exc}")
        return {"sent": False, "reason": str(exc)}


def run_daily_report(cfg, db, log=None, send: bool = True, today: str | None = None,
                     cancel=None) -> dict:
    """完整流程。返回 dict（含 status/trade_date/reason/metrics/html_path/email/output_files）。

    `cancel()` 返回 True 时在阶段边界尽早退出，返回 status=aborted（支持手动终止）。
    """
    def _log(m: str) -> None:
        if log:
            log(m)
        logger.info(m)

    def _aborted(trade_date=None, output_files=None) -> dict:
        return {"status": "aborted", "trade_date": trade_date, "reason": "已终止",
                "output_files": output_files or []}

    if cancel and cancel():
        _log("收到终止请求，跳过报告生成")
        return _aborted()

    trade_date = resolve_trade_date(today)
    if not trade_date:
        _log("无法确定最近交易日（数据源未就绪），跳过")
        return {"status": "skipped", "trade_date": None,
                "reason": "无法确定最近交易日", "output_files": []}
    _log(f"最近交易日 = {trade_date}")

    try:
        r = index_ingest.run_index_fetch(cfg.index_dir, cfg.report_index_codes,
                                         trade_date, log=_log, cancel=cancel)
        _log(f"指数抓取：新增 {len(r['fetched'])}，跳过 {len(r['skipped'])}")
        if r.get("canceled"):
            return _aborted(trade_date)
    except Exception as exc:  # noqa: BLE001
        _log(f"指数抓取异常（不阻断）：{exc}")

    ready = readiness_check(cfg, db, trade_date)
    if not ready["ready"]:
        reason = "；".join(ready["reasons"])
        _log(f"数据未就绪，跳过发送：{reason}")
        return {"status": "skipped", "trade_date": trade_date, "reason": reason,
                "stock_latest": ready["stock_latest"],
                "index_ready": ready["index_ready"], "output_files": []}

    if cancel and cancel():
        _log("收到终止请求，跳过汇总与发送")
        return _aborted(trade_date)

    gen = generate(cfg, db, trade_date)
    _log(f"已生成并归档：{gen['html_path']}")

    email_info = _send(cfg, trade_date, gen["html"], _log) if send else {"sent": False, "reason": "预览模式未发送"}
    return {"status": "success", "trade_date": trade_date, "reason": "",
            "metrics": gen["metrics"], "html_path": gen["html_path"],
            "email": email_info, "output_files": [gen["html_path"]]}


def ensure_report_cron(db, cfg) -> None:
    """若无 kind=report 的定时任务，按配置播种一条（默认 cron 工作日 18:00）。"""
    exists = db.query(CronTask).filter(CronTask.kind == "report").first()
    if exists is not None:
        return
    db.add(CronTask(name=REPORT_REF_NAME, kind="report", ref_id=0,
                    cron_expr=cfg.report_cron, params={}, enabled=cfg.report_enabled))
    db.commit()
