# 内建 Parquet 摄取（24列含复权）与任务手动终止 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为平台补齐 P0 核心——内建数据摄取模块（baostock → 按股票分文件的 Parquet，每行存原始价+前/后复权因子+前/后复权价），并支持运行时手动终止任务。

**Architecture:** 数据摄取按「交易日历逐日横扫」：`query_daily_history_k_AStock(date)` 取当日全市场不复权日K，`query_daily_adjust_factor(date)` 取当日复权因子，两者按 `code` join，算前/后复权价后写入 `data/market/daily_price/{code}.parquet`。任务终止通过 TaskManager 维护 `per-task` 取消事件 + 子进程登记表，区分「子进程策略任务（kill）」与「进程内摄取（协作式轮询取消）」。

**Tech Stack:** Python 3.10+ / FastAPI / SQLAlchemy / baostock / pyarrow(pandas engine) / duckdb(扫描) / 原生 JS + ECharts。

**设计依据：** `docs/设计方案.md` 5.1 / 6.1 / 6.3 / 8 / 9 / 11 节（已提交 `9d2c647`）。

---

## 关键事实（勿偏离）

- `query_daily_history_k_AStock(date)` 参数为**单日期**，返回当日全部 A 股日K（**不复权**），当日已有日期则返回 0 行。
- `query_daily_adjust_factor(date)` 参数为**单日期**，返回当日全部股票的复权因子（含 `qfq_factor`、`hfq_factor`）。
- 两个接口都是"按日横扫全市场"，因此摄取循环的**最外层是交易日**，内层做当日 join。
- 目标文件：`data/market/daily_price/{code}.parquet`，每行 24 列（见下 SCHEMA）。
- 断点续传：元数据库新表 `stock_freshness(code, latest_date, row_count)`。
- 覆盖度 `coverage = parquet 文件数 / 期望股票数（query_all_stock(today)）`；`lag_days = 今天 - 最新交易日`；均来自真实扫描，不造假值。

### 统一列 SCHEMA（模块间共用，首字母小写）

```python
DATA_COLS = ["date", "code", "open", "high", "low", "close", "preclose",
             "volume", "amount", "turn", "pctChg", "tradestatus", "isST",
             "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM"]           # 取自日K接口（不复权）
FACTOR_COLS = ["qfq_factor", "hfq_factor"]                      # 取自复权因子接口
QFQ_PRICE = ["qfq_open", "qfq_high", "qfq_low", "qfq_close"]
HFQ_PRICE = ["hfq_open", "hfq_high", "hfq_low", "hfq_close"]
ALL_COLS = DATA_COLS + FACTOR_COLS + QFQ_PRICE + HFQ_PRICE      # 24 列
```

复权价计算（因子接口语义：复权价 = 原始价 × 对应因子）：
```python
for src, dst_set in (("open", QFQ_PRICE[0] if False else None),):  # 写法见 Task 3 真实代码
    ...
qfq_open = open * qfq_factor   # 同理 high/low/close
hfq_open = open * hfq_factor   # 同理 high/low/close
```

> 说明：若 `query_daily_history_k_AStock` 返回的字段缺 `isST`/`preclose`，按缺失时置空处理（`reindex`），不强制要求存在。

---

## 文件结构

**新增：**
- `app/datasources/__init__.py` —— 数据源包
- `app/datasources/base.py` —— 抽象数据源接口
- `app/datasources/baostock.py` —— baostock 数据源实现
- `app/services/parquet_store.py` —— Parquet 读写 / 增量 merge / 扫描
- `app/services/ingest.py` —— 全量/增量摄取编排（支持协作式取消）
- `tests/test_parquet_store.py` —— Parquet store 单元测试
- `tests/test_ingest.py` —— 摄取计算逻辑单元测试（造假数据，不打真网络）

**修改：**
- `app/models.py` —— 新增 `StockFreshness`；`TaskRun.kind` 加 `ingest`；`TaskRun.status` 加 `aborted`
- `app/services/task_service.py` —— 子进程登记表 + per-task 取消事件 + `terminate()`
- `app/api/tasks.py` —— 新增 `POST /api/tasks/{id}/terminate`
- `app/api/datasets.py` —— `POST /api/datasets/{id}/run` 支持 `mode=full|incremental`，覆盖度/滞后改为扫描 Parquet
- `app/services/dataset_scan.py` —— 改为基于 Parquet/stock_freshness 扫描
- `config/config.yaml` —— datasets 改为「日K」(dir=data/market/daily_price)
- `app/main.py` —— 注册 baostock 数据源 / 依赖注入；启动扫描改走新逻辑
- `web/index.html`、`app/static`（前端 js/css）—— 数据页卡片 + 全量/增量按钮；任务页终止按钮
- `requirements.txt` —— 追加 `pyarrow`、`duckdb`

---

## Task 1: 依赖与 schema（models）

**Files:**
- Modify: `requirements.txt`
- Modify: `app/models.py`

- [ ] **Step 1: requirements 追加依赖**

将 `requirements.txt` 追加两行：
```
pyarrow==17.0.0
duckdb==1.1.3
```

- [ ] **Step 2: models 新增 StockFreshness**

在 `app/models.py` 追加新模型（放在现有 `TaskRun` 之后）：

```python
class StockFreshness(Base):
    """每股断点续传基线：记录该股已入库的最新日期与行数。"""
    __tablename__ = "stock_freshness"
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(16), unique=True, index=True, nullable=False)
    latest_date = Column(Date, nullable=True)
    row_count = Column(Integer, default=0)
```

同时调整 `TaskRun`：`kind` 字段在 `ref_id`（可为空）前提下允许 `"ingest"`；`status` 枚举注释补充 `aborted`。若当前 `TaskRun.status` 无约束，仅需在注释/常量里声明合法值集合：
```python
TASK_STATUSES = ("queued", "running", "success", "failed", "partial", "aborted")
```

- [ ] **Step 3: 建表并验证**

启动一次验证（不删旧库）：
```bash
D:\Program\Anoconda\envs\lianghua\python.exe -c "from app.main import app; print('ok')"
```
再确认 DB 出现新表（重启后首次会执行 `Base.metadata.create_all`）。验证方式无需写死——只要 `stock_freshness` 表能建出即可。

- [ ] **Step 4: Commit**

```bash
git add requirements.txt app/models.py
git commit -m "feat: 新增 stock_freshness 表并使任务支持 ingest/aborted"
```

---

## Task 2: baostock 数据源适配器

**Files:**
- Create: `app/datasources/__init__.py`
- Create: `app/datasources/base.py`
- Create: `app/datasources/baostock.py`

- [ ] **Step 1: 抽象接口 `base.py`**

```python
# -*- coding: utf-8 -*-
"""数据源抽象接口：平台主数据统一通过本接口抓取，便于替换数据源。"""
from typing import List, Optional


class DataSourceError(RuntimeError):
    """数据源调用失败。"""


class BaseStockDataSource:
    name = "base"

    def connect(self) -> None:
        raise NotImplementedError

    def disconnect(self) -> None:
        raise NotImplementedError

    def trade_dates(self, start_date: str, end_date: str) -> List[str]:
        """返回 [start,end] 内的交易日字符串列表(YYYY-MM-DD)。"""
        raise NotImplementedError

    def daily_bars(self, date: str) -> List[dict]:
        """返回某日全市场不复权日K的 dict 列表。"""
        raise NotImplementedError

    def adjust_factors(self, date: str) -> List[dict]:
        """返回某日全市场复权因子 dict 列表(含 code/qfq_factor/hfq_factor)。"""
        raise NotImplementedError

    def universe(self, date: str) -> List[str]:
        """返回截至某日的市场股票代码列表(用于期望覆盖数)。"""
        raise NotImplementedError
```

- [ ] **Step 2: baostock 实现**

```python
# -*- coding: utf-8 -*-
"""baostock 数据源。两个核心接口均为“按单日横扫全市场”。"""
import datetime
import logging

import baostock as bs
import pandas as pd

from .base import BaseStockDataSource, DataSourceError

logger = logging.getLogger("app.datasources.baostock")


class BaostockDataSource(BaseStockDataSource):
    name = "baostock"

    def connect(self) -> None:
        rs = bs.login()
        if rs.error_code != "0":
            raise DataSourceError(f"baostock 登录失败: {rs.error_msg}")

    def disconnect(self) -> None:
        try:
            bs.logout()
        except Exception:  # noqa
            pass

    @staticmethod
    def _rows(result: "Any", date: str) -> List[dict]:
        if result.error_code != "0":
            logger.warning("query 失败(%s,%s): %s", date, result.error_code, result.error_msg)
            return []
        if not result.fields:
            return []
        fields = list(result.fields)
        return [dict(zip(fields, row)) for row in result.get_data()]

    def trade_dates(self, start_date: str, end_date: str) -> List[str]:
        # baostock 返回 trade_date 字段
        rs = bs.query_trade_dates(start_date=start_date, end_date=end_date)
        df = pd.DataFrame([dict(zip(rs.fields, r)) for r in rs.get_data()])
        # trade_date 格式为 'YYYY-MM-DD'
        days = sorted(set(df.loc[df["is_trading_day"] == "1", "calendar_date"]))
        return [d for d in days if start_date <= d <= end_date]

    def daily_bars(self, date: str) -> List[dict]:
        rs = bs.query_daily_history_k_AStock(date=date)
        rows = self._rows(rs, date)
        # 统一单位/类型：volume 单位可能是千股，与旧脚本一致保留原值即可
        return rows

    def adjust_factors(self, date: str) -> List[dict]:
        rs = bs.query_daily_adjust_factor(date=date)
        rows = self._rows(rs, date)
        out = []
        for r in rows:
            try:
                out.append({
                    "date": date,
                    "code": str(r.get("code") or "").split(".")[0],
                    "qfq_factor": float(r.get("qfq_factor") or 1.0),
                    "hfq_factor": float(r.get("hfq_factor") or 1.0),
                })
            except (TypeError, ValueError):
                continue
        return out

    def universe(self, date: str) -> List[str]:
        rs = bs.query_all_stock(day=date)
        rows = self._rows(rs, date)
        return [str(r["code"]).split(".")[0] for r in rows]
```

> `query_trade_dates` 返回列名为 `calendar_date`/`is_trading_day`（baostock 0.9.4）。实现时以实际 `rs.fields` 为准做防御：若字段名不同（如 `trade_date`），Task 实现者须按真实字段名微调，逻辑不变。

- [ ] **Step 3: 冒烟验证（真实网络，短窗口）**

```bash
D:\Program\Anoconda\envs\lianghua\python.exe -X utf8 -c "from app.datasources.baostock import BaostockDataSource as D; d=D(); d.connect(); print(len(d.daily_bars('2024-01-02')), len(d.adjust_factors('2024-01-02')), d.trade_dates('2026-01-02','2026-01-06')); d.disconnect()"
```
预期：输出三个非空/形如 `[(非0),(非0), ['2026-01-02','2026-01-05','2026-01-06']]` 的交易日序列。若当日无数据则 count 为 0（非 0 更好）。

- [ ] **Step 4: Commit**

```bash
git add app/datasources/
git commit -m "feat: baostock 数据源适配器(按日横扫全市场)"
```

---

## Task 3: Parquet 存储层（24列 + 增量 merge + 位点）

**Files:**
- Create: `app/services/parquet_store.py`
- Test: `tests/test_parquet_store.py`

- [ ] **Step 1: 写失败的测试**

```python
# -*- coding: utf-8 -*-
import sys
from pathlib import Path
sys.path.insert(0, "d:/Project/lianghua")

import pandas as pd
from app.services import parquet_store as store


SCHEMA_24 = [
    "date","code","open","high","low","close","preclose","volume","amount",
    "turn","pctChg","tradestatus","isST","peTTM","pbMRQ","psTTM","pcfNcfTTM",
    "qfq_factor","hfq_factor",
    "qfq_open","qfq_high","qfq_low","qfq_close",
    "hfq_open","hfq_high","hfq_low","hfq_close",
]


def make_row(code="000001", date="2024-01-02", close=10.0, qfq=1.5, hfq=8.0):
    price = {"open": close * 0.99, "high": close * 1.01, "low": close * 0.98, "close": close}
    row = {
        "date": date, "code": code,
        "open": price["open"], "high": price["high"], "low": price["low"], "close": price["close"],
        "preclose": close * 0.99, "volume": 1_000_000, "amount": 10_000_000.0,
        "turn": 1.2, "pctChg": 1.0, "tradestatus": "1", "isST": "0",
        "peTTM": 15.0, "pbMRQ": 2.0, "psTTM": 1.0, "pcfNcfTTM": 5.0,
        "qfq_factor": qfq, "hfq_factor": hfq,
        "qfq_open": price["open"] * qfq, "qfq_high": price["high"] * qfq,
        "qfq_low": price["low"] * qfq, "qfq_close": close * qfq,
        "hfq_open": price["open"] * hfq, "hfq_high": price["high"] * hfq,
        "hfq_low": price["low"] * hfq, "hfq_close": close * hfq,
    }
    df = pd.DataFrame([row])
    # 保证列顺序即为 24 列
    df = df[SCHEMA_24].astype({c: "float64" for c in df.columns if df[c].dtype == object and c not in ("date", "code")})
    return df


def test_write_and_read(tmp_path):
    code = "000001"
    df = make_row()
    p = store.write_stock(tmp_path, code, df)
    assert p.name == f"{code}.parquet" and p.exists()
    back = store.read_stock(tmp_path, code)
    assert list(back.columns) == SCHEMA_24, list(back.columns)
    assert len(back) == 1 and back.iloc[0]["date"] == "2024-01-02"


def test_merge_incremental(tmp_path):
    code = "000001"
    store.write_stock(tmp_path, code, make_row(code, date="2024-01-02", close=10.0, qfq=1.5))
    new1 = make_row(code, date="2024-01-05", close=10.5, qfq=1.5)
    merged = store.merge_stock(tmp_path, code, new1)
    assert len(merged) == 2
    assert list(merged["date"]) == ["2024-01-02", "2024-01-05"]  # 按日期升序去重
    assert store.freshness(tmp_path, code) == ("2024-01-05", 2)


def test_freshness_empty(tmp_path):
    assert store.freshness(tmp_path, "999999") == (None, 0)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
D:\Program\Anoconda\envs\lianghua\python.exe -m pytest tests/test_parquet_store.py -v
```
预期：FAIL（`module 'app.services.parquet_store' has no attribute 'write_stock'` 之类）。

- [ ] **Step 3: 实现 `parquet_store.py`**

```python
# -*- coding: utf-8 -*-
"""Parquet 存储层：按股票分文件，写/读/增量 merge/断点位点。"""
import datetime
from pathlib import Path

import pandas as pd

DATA_COLS = ["date", "code", "open", "high", "low", "close", "preclose",
             "volume", "amount", "turn", "pctChg", "tradestatus", "isST",
             "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM"]
FACTOR_COLS = ["qfq_factor", "hfq_factor"]
QFQ_PRICE = ["qfq_open", "qfq_high", "qfq_low", "qfq_close"]
HFQ_PRICE = ["hfq_open", "hfq_high", "hfq_low", "hfq_close"]
ALL_COLS = DATA_COLS + FACTOR_COLS + QFQ_PRICE + HFQ_PRICE  # 24 列


def stock_path(root: Path, code: str) -> Path:
    return Path(root) / f"{code}.parquet"


def write_stock(root, code: str, df: pd.DataFrame) -> Path:
    """全量新建：将 24 列 df 落为 {code}.parquet，按日期升序。"""
    df = df[ALL_COLS].copy()
    df = df.sort_values("date").drop_duplicates("date", keep="last")
    p = stock_path(root, code)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)
    return p


def read_stock(root, code: str) -> pd.DataFrame:
    p = stock_path(root, code)
    if not p.exists():
        return pd.DataFrame(columns=ALL_COLS)
    return pd.read_parquet(p)


def merge_stock(root, code: str, df: pd.DataFrame) -> pd.DataFrame:
    """增量合并：读旧 → concat 新 → 按日期去重升序 → 重写，返回合并结果。"""
    base = read_stock(root, code)
    new = df[ALL_COLS].copy()
    merged = pd.concat([base, new], ignore_index=True)
    merged = merged.sort_values("date").drop_duplicates("date", keep="last")
    write_stock(root, code, merged)
    return merged


def freshness(root, code: str):
    """返回 (latest_date_str_or_None, row_count)。"""
    p = stock_path(root, code)
    if not p.exists():
        return None, 0
    df = pd.read_parquet(p, columns=["date"])
    if df.empty:
        return None, 0
    return str(df["date"].max()), int(len(df))
```

> 性能注：全量 5000 只×多年逐日累加后一次性 `write_stock`；增量每只 `merge_stock` 只读一文件、append 后重写，量级千行，毫秒级。

- [ ] **Step 4: 运行测试确认通过**

```bash
D:\Program\Anoconda\envs\lianghua\python.exe -m pytest tests/test_parquet_store.py -v
```
预期：3 PASS（`tmp_path` 由 pytest 自动提供，无需手工建目录）。

- [ ] **Step 5: Commit**

```bash
git add app/services/parquet_store.py tests/test_parquet_store.py
git commit -m "feat: Parquet 存储层(24列写读/增量merge/断点位点)"
```

---

## Task 4: 摄取编排（全量/增量 + 协作式取消）

**Files:**
- Create: `app/services/ingest.py`
- Test: `tests/test_ingest.py`

- [ ] **Step 1: 写失败的测试（复权价计算 / 按日横扫聚合逻辑，假数据不打网络）**

```python
# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "d:/Project/lianghua")

import pytest
from app.services import ingest


def test_compute_adjusted_prices():
    """给定原始价+因子，算出的前/后复权价正确。"""
    row = {"open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2,
           "qfq_factor": 1.5, "hfq_factor": 8.0}
    out = ingest.compute_adjusted_prices(row)
    assert out["qfq_close"] == pytest.approx(10.2 * 1.5)
    assert out["hfq_close"] == pytest.approx(10.2 * 8.0)
    assert out["hfq_high"] == pytest.approx(10.5 * 8.0)
    assert out["qfq_open"] == pytest.approx(10.0 * 1.5)


def test_run_ingest_incremental_cancel(capsys, monkeypatch, tmp_path):
    """协作式取消：cancel_flag 置位后循环尽早退出并返回 aborted。"""
    calls = {"n": 0}

    def fake_daily(date):
        calls["n"] += 1
        return [{"code": "000001", "date": date, "open": 1, "high": 1, "low": 1,
                 "close": 1, "preclose": 1, "volume": 1, "amount": 1, "turn": 0,
                 "pctChg": 0, "tradestatus": "1", "isST": "0", "peTTM": 1,
                 "pbMRQ": 1, "psTTM": 1, "pcfNcfTTM": 1}]

    cancel_flag = (lambda n=[0]: True if (n[0] := n[0] + 1) >= 2 else False)

    # 用 monkeypatch 替换 ingest 内部 fetch 钩子（实现见 Step 3），取消靠传入的 cancel_flag
    monkeypatch.setattr(ingest, "_fetch_daily_bars", fake_daily)
    monkeypatch.setattr(ingest, "_fetch_factors", lambda date: [])

    result = ingest.run_incremental(tmp_path, dates=["2024-01-02", "2024-01-05", "2024-01-08"],
                                    cancel_flag=cancel_flag)
    assert result["status"] == "aborted"
    assert calls["n"] == 2  # 第二次后即取消
```

- [ ] **Step 2: 运行测试确认失败**

```bash
D:\Program\Anoconda\envs\lianghua\python.exe -m pytest tests/test_ingest.py -v
```
预期：FAIL（`ingest` 不存在）。

- [ ] **Step 3: 实现 `ingest.py`**

```python
# -*- coding: utf-8 -*-
"""数据摄取编排：按交易日历横扫全市场，落 24 列 Parquet，支持协作式取消。"""
import datetime
import logging
from pathlib import Path
from typing import Callable, List

import pandas as pd

from . import parquet_store as store
from ..config import Config
from ..models import StockFreshness
from .. import get_db  # 见 Task 6 接线；若依赖注入不同，按实现时真实入口调整

logger = logging.getLogger("app.services.ingest")

# ---- 可被测试 monkeypatch 的内部钩子 ----
_fetch_daily_bars: Callable = lambda date: []
_fetch_factors: Callable = lambda date: []


def compute_adjusted_prices(row: dict) -> dict:
    """由原始价 + 因子计算前/后复权 OHLC。"""
    q = row["qfq_factor"]
    h = row["hfq_factor"]
    return {
        "qfq_open": row["open"] * q, "qfq_high": row["high"] * q,
        "qfq_low": row["low"] * q, "qfq_close": row["close"] * q,
        "hfq_open": row["open"] * h, "hfq_high": row["high"] * h,
        "hfq_low": row["low"] * h, "hfq_close": row["close"] * h,
    }


def _is_cancelled(cancel_flag: Callable) -> bool:
    return cancel_flag()


def _row_to_stock_df(rows: List[dict]) -> pd.DataFrame:
    """把当日全市场 dict 行转成 24 列宽表（含因子与复权价）。"""
    records = []
    for r in rows:
        r2 = dict(r)
        adjust = compute_adjusted_prices(r2)
        records.append({**r2, **adjust})
    df = pd.DataFrame(records)
    df["code"] = df["code"].astype(str).str.split(".").str[0]
    for col in store.ALL_COLS:
        if col not in df.columns:
            df[col] = None
    return df[store.ALL_COLS]


def run_full():
    """全量：从 ds.connect 抓取全历史(由“起始日期”到昨日)，逐日横扫写库。"""
    raise NotImplementedError("由执行者接线真实数据源后补齐：见下方设计说明")


def run_incremental(root: Path, dates: List[str],
                    cancel_flag: Callable = lambda: False) -> dict:
    """增量：对 dates 逐日横扫，每股 merge 重写；cancel_flag() 为 True 时尽早退出。"""
    root = Path(root)
    for date in dates:
        if cancel_flag():
            return {"status": "aborted", "processed_dates": date}
        bars = _fetch_daily_bars(date)
        factors = {f["code"]: f for f in _fetch_factors(date)}
        for b in bars:
            code = str(b.get("code", "")).split(".")[0]
            fac = factors.get(code) or {"qfq_factor": 1.0, "hfq_factor": 1.0}
            b["qfq_factor"], b["hfq_factor"] = fac["qfq_factor"], fac["hfq_factor"]
        df = _row_to_stock_df(bars)
        if df.empty:
            continue
        for code, g in df.groupby("code"):
            store.merge_stock(root, code, g)
    return {"status": "success"}
```

**接线说明（run_full）：** 在 `app/services/ingest.py` 顶层引入数据源单例，`run_full` 逻辑与 `run_incremental` 相同，只是日期序列来自 `trade_dates(start, end)` 且对空文件调用 `write_stock` 而非 merge。内部通过注入的 `DataSource` 调用 `daily_bars(date)` / `adjust_factors(date)`（二者的行就是 `_fetch_daily_bars/_fetch_factors` 的替身）。**执行者须在实现时把顶层两个 hook 初始化绑定到已注入的 `BaostockDataSource` 实例，保持本文件可测试。**

- [ ] **Step 4: 运行测试确认通过**

```bash
D:\Program\Anoconda\envs\lianghua\python.exe -m pytest tests/test_ingest.py -v
```
预期：2 PASS。

- [ ] **Step 5: Commit**

```bash
git add app/services/ingest.py tests/test_ingest.py
git commit -m "feat: 摄取编排(按日横扫+每股merge+协作式取消)"
```

---

## Task 5: TaskManager 子进程登记 + 手动终止

**Files:**
- Modify: `app/services/task_service.py`

- [ ] **Step 1: 现有结构核对**

读 `app/services/task_service.py` 全文，确认：单工作线程从队列取 `tid` → `_execute_task` → `_run_subprocess`。在 `__init__` 中已有 `self.running_task_id`。

- [ ] **Step 2: 增加登记表与取消事件**

在 `TaskService.__init__` 增加：

```python
self._procs: dict[int, "subprocess.Popen"] = {}          # task_id -> 子进程
self._cancel_events: dict[int, threading.Event] = {}
```

并新增方法：

```python
def _register(self, task_id, proc=None):
    self._cancel_events[task_id] = threading.Event()
    if proc is not None:
        self._procs[task_id] = proc

def _unregister(self, task_id):
    self._procs.pop(task_id, None)
    self._cancel_events.pop(task_id, None)

def terminate(self, task_id: int) -> bool:
    """请求终止运行中任务：置取消事件；若是子进程则立即 kill。返回是否命中运行中任务。"""
    ev = self._cancel_events.get(task_id)
    proc = self._procs.get(task_id)
    if ev is None and proc is None:
        return False
    if ev is not None:
        ev.set()
    if proc is not None:
        try:
            proc.kill()
        except Exception:  # noqa
            pass
    return True
```

- [ ] **Step 3: 子进程路径接入取消**

`_run_subprocess` 打开 `proc` 后立即 `self._register(task_id, proc)`；while 轮询里在「无输出且仍在运行」分支，先判断取消事件（插在超时判断之前）：

```python
elif datetime.now().timestamp() > deadline:
    proc.kill()
    ...
elif self._cancel_events.get(task_id, threading.Event()).is_set():
    proc.kill()
    proc.wait()
    f.write("\n[用户终止]\n")
    return {"exit_code": None, "status": "aborted", "summary": {"canceled": True}}
```

`finally`（或进程结束后）调用 `self._unregister(task_id)`。

> `_execute_task` 中已有 `with self._lock: self.running_task_id = task_id`——保持不动。`terminate()` 返回 False 时，由 API 层返回 404/409。

- [ ] **Step 4: 冒烟验证（单测以磁盘/逻辑为准）**

用一条策略任务跑一个 `time.sleep(60)` 的临时脚本，起任务后立即调用 `terminate(id)`，确认：职责返回 True、任务状态最终为 `aborted`、日志含 `[用户终止]`。验证命令（示意）：
```bash
D:\Program\Anoconda\envs\lianghua\python.exe -m pytest tests/test_terminate.py -v
```
（`tests/test_terminate.py` 由执行者按此行为编写：注册假 proc、置事件后等待循环退出并断言日志。）

- [ ] **Step 5: Commit**

```bash
git add app/services/task_service.py tests/test_terminate.py
git commit -m "feat: 任务手动终止(子进程kill+进程内取消事件)"
```

---

## Task 6: API 接线（数据集 run full/incremental + terminate）

**Files:**
- Modify: `app/api/datasets.py`
- Modify: `app/api/tasks.py`
- Modify: `app/services/dataset_scan.py`
- Modify: `app/main.py`
- Modify: `config/config.yaml`

- [ ] **Step 1: config 数据集定义改为「日K」**

`config/config.yaml` 的 `datasets` 段改为：

```yaml
datasets:
  - name: 平台日K数据
    type: 日K
    dir: data/market/daily_price
    file_glob: "*.parquet"
```

`expected_per_day` 去掉；期望股票数运行时由 `universe(today)` 得到。

- [ ] **Step 2: dataset_scan 改为扫 Parquet + 断点位点**

重写 `app/services/dataset_scan.py` 的 `scan_one/scan_all`，使其：
- `file_count = 目录下 *.parquet 文件数`（=股票数，若元库有 `stock_freshness` 则以表为准，优先文件扫描做真值）；
- `latest_data_date = max(stock_freshness.latest_date)`；
- `lag_days = max(0, (今天 - latest_data_date).days)`（日期缺失则 None）；
- `coverage = (已入库股票数 in stock_freshness) / foreground expected`；expected 从注入的 `BaostockDataSource.universe(today)` 取，取不到时 `coverage=0` 并 `status=扫描依赖不可用`。

> 不造假值：所有数来自真实 parquet/元库。若数据目录不存在，`file_count=0, status=未生成`。

- [ ] **Step 3: datasets API 支持全量/增量**

`app/api/datasets.py` 增加：
```python
@router.post("/{dset_id}/run")
def run_dataset(dset_id: int, mode: str = Query("full"),
                db: Session = Depends(get_db), svc: TaskService = Depends(get_service)):
    """mode=full|incremental 触发内建摄取任务(kind=ingest)。"""
    if mode not in ("full", "incremental"):
        raise HTTPException(422, "mode 仅支持 full|incremental")
    task = svc.enqueue_ingest(dset_id, mode=mode)   # 见 Step 4
    return {"task_id": task.id}
```

- [ ] **Step 4: TaskService.enqueue_ingest**

在 `app/services/task_service.py` 增加入队方法，复用一个 `ref_id`（指向 `dataset` 行）的 `TaskRun`，`kind="ingest"`，并把「协作式取消事件」接入摄取循环（把 `self._cancel_events[task_id].is_set` 作为 `cancel_flag` 传给 `run_incremental`）。`_execute_task` 增加 `if task.kind == "ingest"` 分支，走内建摄取而非 subprocess，结束后用 `run_*` 返回值回填 `status/result_summary/finished_at`。

- [ ] **Step 5: tasks API 增加 terminate**

`app/api/tasks.py`（在 `/running` 之前、`/{tid}` 之前）增加：

```python
@router.post("/{tid}/terminate")
def terminate_task(tid: int, svc: TaskService = Depends(get_service)):
    ok = svc.terminate(tid)
    if not ok:
        raise HTTPException(409, "任务不在运行中，无法终止")
    return {"ok": True, "task_id": tid}
```

> 注意路由顺序：静态 `/terminate` 后缀属于 `/{tid}/...` 动态段，Starlette 按注册顺序匹配，务必把它声明在泛型 `/{tid}` 之前（本项目 `/running` 已有同款处理先例）。

- [ ] **Step 6: main.py 接线**

在 `app/main.py` 启动/依赖处：
- 实例化 `BaostockDataSource` 并注入；在 `app.shutdown` 时 `disconnect()`；
- 把注入的 data source 的 `daily_bars/adjust_factors` 绑定为 `ingest._fetch_daily_bars/_fetch_factors` 的默认实现；
- 首启扫描改走新的 `dataset_scan.scan_all`（扫 Parquet）。

- [ ] **Step 7: 端到端验证**

```bash
D:\Program\Anoconda\envs\lianghua\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
- 打开 `http://127.0.0.1:8000`，数据页出现「平台日K数据」卡片（未生成/股票数 0）。
- 点「获取增量数据」→ 任务经历 queued→running→success/aborted；`最新日期/滞后/覆盖度`随真实扫描更新。
- 运行中（或临时长任务）点「终止」→ 状态变 `aborted`，日志含终止标记。

- [ ] **Step 8: Commit**

```bash
git add app/api/datasets.py app/api/tasks.py app/services/dataset_scan.py app/services/task_service.py app/main.py config/config.yaml
git commit -m "feat: 数据摄取/终止 API 与覆盖度扫描接线"
```

---

## Task 7: 前端（数据页卡片 + 终止按钮）

**Files:**
- Modify: `web/index.html`
- Modify: `app/static/js`（或 `web/js`，以现状为准）
- Modify: `app/static/css`（样式可选）

- [ ] **Step 1: 数据页改为「平台日K数据」卡片**

在数据页渲染单个数据集卡片，展示字段：`股票数(file_count)`、`最新日期(latest_data_date)`、`滞后天数(lag_days)`、`覆盖度(coverage)`、`状态(status)`；两个按钮「获取全量数据」「获取增量数据」，分别 `POST /api/datasets/{id}/run?mode=full` / `?mode=incremental`。

- [ ] **Step 2: 任务页「终止」按钮**

任务列表中 `status==='running'` 的行显示「终止」按钮，点击调 `POST /api/tasks/{id}/terminate`，成功后刷新任务列表；非 running 隐藏按钮。

- [ ] **Step 3: 前端自测**

- 数据页卡片渲染正确、按钮可触发任务；
- 任务页 running 行有终止按钮、完成/失败/aborted 无按钮；
- 终止 API 返回 409 时前端给出「任务不在运行中」提示。

- [ ] **Step 4: Commit**

```bash
git add web app/static
git commit -m "feat: 前端数据页全/增量按钮与任务终止按钮"
```

---

## Self-Review 摘要

- **Spec 覆盖**：设计 5.1(全/增量)、6.1(24列)、6.3(stock_freshness)、8(terminate API)、9(数据页/任务页)、11(手动终止) 均有对应 Task。
- **占位符**：`run_full` 明确标注为接线代实现；其余无 TBD。
- **类型一致性**：`ABORTED/aborted`、`qfq_factor/hfq_factor`、`stock_freshness` 全程一致；`_fetch_daily_bars/_fetch_factors` 钩子被 Task 4 测试与 Task 6 接线共用。

---

## 执行交接

计划已保存。两条执行路径：
1. **Subagent-Driven（推荐）**：每个 Task 派发一个全新子代理，代理间我做审查，迭代快。
2. **Inline Execution**：本会话用 executing-plans 批量执行，带检查点。

选哪种？另外提醒：执行前需先 `pip install pyarrow duckdb`（baostock 已装）。