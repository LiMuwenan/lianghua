# -*- coding: utf-8 -*-
"""报告渲染：六大板块、红涨绿跌、数据缺失显示。"""
from app.services import report_render


def _metrics(missing=None):
    return {
        "trade_date": "2026-10-13",
        "index": {"available": True, "rows": [
            {"code": "sh000001", "name": "上证综指", "close": 3100.5, "pctChg": 0.85, "amount": 4.2e11},
            {"code": "sz399001", "name": "深证成指", "close": 9800.0, "pctChg": -0.42, "amount": 5.0e11},
        ]},
        "breadth": {"up": 3200, "down": 1600, "flat": 120,
                    "limit_up": 55, "limit_down": 8, "suspended": 30, "total": 4920},
        "volume": {"amount": 9.2e11, "volume": 7.1e10, "turn_avg": 1.23,
                   "vs_prev_pct": 6.5, "vs_ma5_pct": -3.2, "state": "缩量"},
        "valuation": {"pe_median": 22.1, "pb_median": 1.85,
                      "pe_pct_1y": 35.0, "pb_pct_1y": 12.0},
        "industry": {"top": [{"industry": "银行", "avg_pct": 2.1, "count": 42}],
                     "bottom": [{"industry": "地产", "avg_pct": -3.3, "count": 88}]},
        "hot": {"gainers": [{"code": "sz300750", "name": "宁德时代", "pctChg": 10.0}],
                "losers": [{"code": "sh600000", "name": "浦发银行", "pctChg": -6.1}]},
        "conclusion": "上证综指+0.85%，上涨3200家/下跌1600家，缩量。",
        "missing": missing or [],
    }


def test_render_contains_all_sections_and_colors():
    html = report_render.render_report(_metrics(), "2026-10-13")
    for key in ("核心指数", "市场宽度", "量能", "估值", "行业", "个股热度", "一句话结论"):
        assert key in html
    assert "上证综指" in html and "3100.50" in html
    assert report_render.UP_COLOR in html      # 红涨
    assert report_render.DOWN_COLOR in html    # 绿跌
    assert "近似口径" in html
    assert "2026-10-13" in html


def test_render_escapes_html_in_external_text():
    """模板名 daily_report.html.j2 也必须启用自动转义：外部文本里的 HTML 需被转义。"""
    m = _metrics()
    m["index"]["rows"][0]["name"] = "<script>alert(1)</script>"
    html = report_render.render_report(m, "2026-10-13")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_shows_missing_sections():
    m = _metrics(missing=["核心指数", "行业"])
    m["index"] = {"available": False, "rows": []}
    m["industry"] = {"top": [], "bottom": []}
    html = report_render.render_report(m, "2026-10-13")
    assert "数据缺失" in html
    assert "核心指数" in html and "行业" in html
