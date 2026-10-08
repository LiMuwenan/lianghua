# -*- coding: utf-8 -*-
"""多因子选股：横截面打分排序，产出多期候选股票池 CSV。

设计依据：docs/superpowers/specs/2026-10-08-策略脚本-选股与回测-design.md §3-§4
运行约定：平台以「脚本所在目录」为工作目录运行临时副本，故定位仓库根用 Path.cwd() 向上查找。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

# ---- 顶层常量（可被平台参数注入覆盖；空串=沿用默认/自动）----
START_DATE = "2020-01-01"   # 区间起
END_DATE = ""               # 空=今天
REBALANCE = "M"             # M=每月最后交易日 / W=每周最后交易日
TOP_N = 20                  # 每期选股数
W_VALUE = 1.0
W_MOMENTUM = 1.0
W_VOL = 1.0
W_LIQUIDITY = 1.0
MIN_AMOUNT = 50_000_000.0   # 20 日均成交额下限（元）
MIN_LIST_DAYS = 120         # 上市最少交易日
DATA_DIR = ""               # 空=读 config.yaml

NEUTRAL = 0.5               # 缺失值中性分位
PANEL_COLS = ["date", "code", "qfq_close", "amount", "peTTM", "pbMRQ", "tradestatus", "isST"]


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    """取数值列；列不存在时返回等长 NaN 序列。"""
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce")
    return pd.Series([float("nan")] * len(df), index=df.index)


def _pct(s: pd.Series) -> pd.Series:
    """横截面分位（0~1），缺失记中性 0.5。"""
    return s.rank(pct=True).fillna(NEUTRAL)


def filter_universe(g: pd.DataFrame, min_amount: float, min_list_days: int) -> pd.DataFrame:
    """剔除指数/ST/非正常交易/上市不足/流动性不足。"""
    out = g[~g["code"].astype(str).str.match(r"^(sh000|sz399)")]
    out = out[pd.to_numeric(out["isST"], errors="coerce").fillna(0) != 1]
    out = out[pd.to_numeric(out["tradestatus"], errors="coerce").fillna(0) == 1]
    out = out[pd.to_numeric(out["cum_days"], errors="coerce").fillna(0) >= min_list_days]
    out = out[pd.to_numeric(out["amount20"], errors="coerce").fillna(0) >= min_amount]
    return out


def cross_section_scores(g: pd.DataFrame, weights: dict) -> pd.DataFrame:
    """对一个调仓日的横截面计算各因子分位得分与综合分（各因子统一为越大越好）。"""
    out = g.copy()
    # 估值：peTTM/pbMRQ 分位均值（<=0 视为缺失），低估值为好
    pe = _col(out, "peTTM")
    pb = _col(out, "pbMRQ")
    pe = pe.mask(pe <= 0)
    pb = pb.mask(pb <= 0)
    value_raw = pd.concat([_pct(pe), _pct(pb)], axis=1).mean(axis=1)
    out["value"] = 1.0 - value_raw
    # 动量：20/60 日收益分位均值，高动量为好
    mom_raw = pd.concat([_pct(_col(out, "ret20")), _pct(_col(out, "ret60"))], axis=1).mean(axis=1)
    out["momentum"] = mom_raw
    # 波动率：20 日收益标准差，低波动为好
    out["volatility"] = 1.0 - _pct(_col(out, "vol20"))
    # 流动性：20 日均成交额取 ln，高流动性为好
    amt = _col(out, "amount20")
    out["liquidity"] = _pct(np.log(amt.where(amt > 0)))

    w = {
        "value": float(weights.get("value", 0.0) or 0.0),
        "momentum": float(weights.get("momentum", 0.0) or 0.0),
        "volatility": float(weights.get("volatility", 0.0) or 0.0),
        "liquidity": float(weights.get("liquidity", 0.0) or 0.0),
    }
    num = pd.Series(0.0, index=out.index)
    den = 0.0
    for key, wi in w.items():
        if wi == 0.0:
            continue
        num = num + wi * out[key]
        den += abs(wi)
    out["score"] = (num / den) if den else 0.0
    return out


def pick_topn(scored: pd.DataFrame, top_n: int) -> pd.DataFrame:
    """按 score 降序取前 top_n，附加 rank（1=最优）。"""
    s = scored.sort_values("score", ascending=False).head(int(top_n)).copy()
    s["rank"] = range(1, len(s) + 1)
    return s


def compute_panel_factors(df: pd.DataFrame) -> pd.DataFrame:
    """按 code 分组计算滚动因子列：cum_days/ret20/ret60/vol20/amount20。"""
    d = df.sort_values(["code", "date"]).copy()
    grp = d.groupby("code", sort=False)
    d["cum_days"] = grp.cumcount() + 1
    d["ret20"] = grp["qfq_close"].transform(lambda s: s / s.shift(20) - 1)
    d["ret60"] = grp["qfq_close"].transform(lambda s: s / s.shift(60) - 1)
    d["vol20"] = grp["qfq_close"].transform(lambda s: s.pct_change(fill_method=None).rolling(20).std())
    d["amount20"] = grp["amount"].transform(lambda s: s.rolling(20).mean())
    return d


def rebalance_dates(dates, mode: str = "M") -> list[str]:
    """基于实际交易日生成调仓日：M=每月最后交易日，W=每周（ISO周）最后交易日。"""
    uniq = sorted({str(x) for x in dates})
    if not uniq:
        return []
    d = pd.Series(uniq)
    dt = pd.to_datetime(d)
    if str(mode).upper().startswith("W"):
        key = dt.dt.strftime("%G-%V")
    else:
        key = dt.dt.strftime("%Y-%m")
    tmp = pd.DataFrame({"date": d.values, "key": key.values})
    return [str(x) for x in tmp.groupby("key")["date"].max().sort_values().tolist()]


def select_pool(panel: pd.DataFrame, weights: dict, top_n: int, rebalance: str,
                min_amount: float, min_list_days: int, min_date: str = "") -> pd.DataFrame:
    """对每个调仓日：过滤 → 打分 → 取 TopN，返回汇总长表并按 (date, rank) 排序。"""
    reb = rebalance_dates(panel["date"].unique(), rebalance)
    if min_date:
        reb = [d for d in reb if d >= min_date]
    frames = []
    for day in reb:
        g = panel[panel["date"] == day]
        g = filter_universe(g, min_amount, min_list_days)
        if g.empty:
            continue
        picked = pick_topn(cross_section_scores(g, weights), top_n)
        picked["date"] = day
        frames.append(picked)
    cols = ["date", "code", "score", "rank", "value", "momentum", "volatility", "liquidity"]
    if not frames:
        return pd.DataFrame(columns=cols)
    out = pd.concat(frames, ignore_index=True)
    return out[cols].sort_values(["date", "rank"]).reset_index(drop=True)


def find_repo_root(start: Path) -> Path:
    """从 start 向上查找含 config/config.yaml 的目录作为仓库根。"""
    p = Path(start).resolve()
    for cand in [p, *p.parents]:
        if (cand / "config" / "config.yaml").is_file():
            return cand
    raise FileNotFoundError("未找到仓库根（需含 config/config.yaml）")


def load_paths(data_dir_override: str = "") -> tuple[Path, Path]:
    """返回 (data_dir, output_dir)。override 为空则读 config.yaml。"""
    import yaml

    root = find_repo_root(Path.cwd())
    with open(root / "config" / "config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    raw = (data_dir_override or "").strip() or str(cfg.get("data_dir", ""))
    dp = Path(raw).expanduser()
    data_dir = dp if dp.is_absolute() else (root / dp).resolve()
    op = Path(str(cfg.get("output_dir", "outputs"))).expanduser()
    output_dir = op if op.is_absolute() else (root / op).resolve()
    return data_dir, output_dir


def read_prices(data_dir: Path, min_date: str, max_date: str) -> pd.DataFrame:
    """DuckDB 读 Parquet 所需列；min_date/max_date 为 YYYY-MM-DD（可为空）。"""
    import duckdb

    glob = Path(data_dir).as_posix() + "/*.parquet"
    sql = f"SELECT {', '.join(PANEL_COLS)} FROM read_parquet('{glob}')"
    conds = []
    if min_date:
        conds.append(f"date >= '{min_date}'")
    if max_date:
        conds.append(f"date <= '{max_date}'")
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    return duckdb.connect().execute(sql).fetch_df()


def attach_names(root: Path) -> dict:
    """从 meta.db 的 stock 表取 code→name；库/表缺失则返回空 dict。"""
    db = Path(root) / "data" / "meta.db"
    if not db.is_file():
        return {}
    try:
        con = sqlite3.connect(str(db))
        try:
            return {str(c): (n or "") for c, n in con.execute("SELECT code, name FROM stock").fetchall()}
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return {}


def main() -> int:
    data_dir, output_dir = load_paths(DATA_DIR)
    if not data_dir.exists():
        print(f"[错误] 数据目录不存在: {data_dir}")
        return 1
    end = (END_DATE or "").strip() or pd.Timestamp.today().strftime("%Y-%m-%d")
    start = (START_DATE or "").strip()
    # 预留 400 天暖启动窗口，保证 60 日收益/20 日波动可见
    warm = (pd.Timestamp(start) - pd.Timedelta(days=400)).strftime("%Y-%m-%d") if start else ""
    print(f"[信息] 数据目录 {data_dir}；区间 {start or '全量'} ~ {end}；调仓 {REBALANCE}")
    raw = read_prices(data_dir, warm, end)
    if raw.empty:
        print("[错误] 未读到任何行情数据")
        return 1
    panel = compute_panel_factors(raw)
    weights = {"value": W_VALUE, "momentum": W_MOMENTUM, "volatility": W_VOL, "liquidity": W_LIQUIDITY}
    pool = select_pool(panel, weights, TOP_N, REBALANCE, MIN_AMOUNT, MIN_LIST_DAYS, min_date=start)
    if pool.empty:
        print("[错误] 无任何入选记录（可放宽 min_amount/min_list_days 或检查区间）")
        return 1
    names = attach_names(find_repo_root(Path.cwd()))
    pool["name"] = pool["code"].map(lambda c: names.get(str(c), ""))
    pool = pool[["date", "code", "name", "score", "rank", "value", "momentum", "volatility", "liquidity"]]
    sel_dir = output_dir / "selection"
    sel_dir.mkdir(parents=True, exist_ok=True)
    path = sel_dir / f"select_{start or 'all'}_{end}_{REBALANCE}.csv"
    pool.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[完成] 调仓日数 {pool['date'].nunique()}，总行数 {len(pool)}，输出 {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())