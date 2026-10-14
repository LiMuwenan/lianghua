# -*- coding: utf-8 -*-
"""报告渲染：把指标 dict 渲染为 HTML 邮件（Jinja2 模板）。"""
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

UP_COLOR = "#d60000"     # A股红涨
DOWN_COLOR = "#1a9c3c"   # A股绿跌
FLAT_COLOR = "#6a737d"

_TPL_DIR = Path(__file__).resolve().parents[1] / "templates"   # app/templates
_env = Environment(loader=FileSystemLoader(str(_TPL_DIR)),
                   # 模板名以 .j2 结尾，故扩展名列表须含 j2，否则 select_autoescape 判定为关闭
                   autoescape=select_autoescape(["html", "xml", "j2"]))


def _pctcolor(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return FLAT_COLOR
    if v > 0:
        return UP_COLOR
    if v < 0:
        return DOWN_COLOR
    return FLAT_COLOR


def _pct(v):
    if v is None:
        return "—"
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return "—"


def _fmtnum(v, nd=2):
    if v is None:
        return "—"
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return "—"


def _yi2(v):
    if v is None:
        return "—"
    try:
        return f"{float(v) / 1e8:.1f}"
    except (TypeError, ValueError):
        return "—"


_env.filters["pctcolor"] = _pctcolor
_env.filters["pct"] = _pct
_env.filters["fmtnum"] = _fmtnum
_env.filters["yi2"] = _yi2


def render_report(metrics: dict, trade_date: str, subject_prefix: str = "") -> str:
    """渲染邮件 HTML。metrics 见 market_summary.build_summary。"""
    tpl = _env.get_template("daily_report.html.j2")
    return tpl.render(m=metrics, trade_date=trade_date, subject_prefix=subject_prefix)
