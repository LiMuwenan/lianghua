# -*- coding: utf-8 -*-
"""指数日线抓取：按指数代码区间拉取，落 {index_dir}/{code}.parquet；取不到则跳过。

- 每股起点 = 该指数已入库最新日的次一日，无则 start_default（默认 2005-01-01）。
- 单指数查询空结果/异常 → 记 skipped，不写文件、不报错（不阻断）。
- 数据源通过模块级钩子 _fetch_index 注入：默认空实现（便于测试替换），
  运行时由接线层 bind_source 绑到真实 BaostockDataSource.index_daily。
"""
import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from . import index_store

logger = logging.getLogger("app.services.index_ingest")

_fetch_index = lambda code, start, end: []   # noqa: E731


def bind_source(ds) -> None:
    """绑定真实数据源的指数日线方法。"""
    global _fetch_index
    _fetch_index = ds.index_daily


def _next_day(iso: str) -> str:
    return (date.fromisoformat(iso) + timedelta(days=1)).isoformat()


def run_index_fetch(root, codes, end_date: str, start_default: str = "2005-01-01",
                    log=None) -> dict:
    """对 codes 逐个增量抓取至 end_date，写 {root}/{code}.parquet。

    返回 {"fetched": {code: rows}, "skipped": [code], "latest": {code: date}}。
    """
    def _log(m: str) -> None:
        if log:
            log(m)
        logger.info(m)

    root = Path(root)
    fetched, skipped, latest = {}, [], {}
    for code in codes:
        last = index_store.latest_date(root, code)
        start = _next_day(last) if last else start_default
        if start > end_date:
            latest[code] = last
            _log(f"指数 {code}: 已最新({last})，跳过")
            continue
        try:
            rows = _fetch_index(code, start, end_date)
        except Exception as exc:  # noqa: BLE001
            skipped.append(code)
            _log(f"指数 {code}: 抓取异常，跳过（{exc}）")
            continue
        if not rows:
            skipped.append(code)
            _log(f"指数 {code}: 无数据，跳过")
            continue
        index_store.merge_index(root, code, pd.DataFrame(rows))
        fetched[code] = len(rows)
        latest[code] = index_store.latest_date(root, code)
        _log(f"指数 {code}: 写入 {len(rows)} 行，最新 {latest[code]}")
    return {"fetched": fetched, "skipped": skipped, "latest": latest}
