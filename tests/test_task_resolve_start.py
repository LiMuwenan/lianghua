# tests/test_task_resolve_start.py
# -*- coding: utf-8 -*-
"""无指定日期时摄取起点解析：dataset 最新位点优先于每股断点全局最小。"""
import sys
sys.path.insert(0, "d:/Project/lianghua")

from app.services.task_service import _resolve_start


def test_explicit_start_wins():
    """显式传入日期必然优先，dataset/断点皆被忽略。"""
    assert _resolve_start("2026-01-01", "2026-09-23", {"a": "2010-01-01"}) == "2026-01-01"


def test_dataset_latest_over_global_min():
    """无显式日期：优先用 dataset 的 latest_data_date（最新整体位点），
    不受退市/停更股旧断点(2010) 拉回。"""
    freshness = {"a": "2010-01-01", "b": "2026-09-20"}   # 全局最小=2010
    assert _resolve_start("", "2026-09-23", freshness) == "2026-09-23"


def test_falls_back_to_global_min_when_ds_empty():
    """dataset 起点为空时，回退到每股断点全局最小。"""
    assert _resolve_start("", "", {"a": "2010-01-01", "b": "2026-09-20"}) == "2010-01-01"


def test_falls_back_to_2005_when_nothing():
    """既无显式日期也无任何断点 → 2005-01-01。"""
    assert _resolve_start("", "", {}) == "2005-01-01"