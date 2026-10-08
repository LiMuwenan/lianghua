# -*- coding: utf-8 -*-
"""组合调仓回测：读选股池 → 等权调仓 → 输出净值/回撤序列与指标 JSON。

设计依据：docs/superpowers/specs/2026-10-08-策略脚本-选股与回测-design.md §5
运行约定：平台以「脚本所在目录」为工作目录运行临时副本，故定位仓库根用 Path.cwd() 向上查找。
口径：收盘价调仓 + 双边成本的简化模型，不含涨跌停/停牌/滑点。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# ---- 顶层常量（可被平台参数注入覆盖；空串=沿用默认/自动）----
POOL_FILE = ""              # 选股池 CSV；空=自动取 outputs/selection 下最新一个
HOLD_N = 20                 # 每期持有数（≤ 该期池大小，超出按池大小）
COST = 0.002                # 双边交易成本（合计）
WEIGHT_MODE = "equal"       # 权重模式（本期仅 equal）
BENCHMARK = "market_equal"  # 基准：市场等权；或填一个指数 code（如 sh000300）
INIT_CAPITAL = 1_000_000.0  # 初始资金
DATA_DIR = ""               # 空=读 config.yaml

TRADING_DAYS = 252


def _turnover(cur: list, prev: list) -> float:
    """对称换手率 = |对称差| / (|本期| + |上期|)。"""
    a, b = set(cur), set(prev)
    denom = len(a) + len(b)
    return len(a ^ b) / denom if denom else 0.0


def _period_return(codes: list, c_prev: pd.Series, c_now: pd.Series) -> float:
    """等权持仓自 c_prev 到 c_now 的期收益；缺价/非正价跳过，全缺返回 0。"""
    vals = []
    for code in codes:
        if code in c_prev.index and code in c_now.index:
            p0, p1 = c_prev[code], c_now[code]
            if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                vals.append(p1 / p0 - 1.0)
    return float(np.mean(vals)) if vals else 0.0


def compute_nav(close: pd.DataFrame, holdings: dict, rebalance_days: list,
                hold_n: int, cost: float) -> pd.Series:
    """逐日净值序列；起点（首个调仓日）= 1.0，首期按全额建仓计一次成本。

    close: index=交易日字符串（升序），columns=code，values=qfq_close。
    holdings: {调仓日: [code...]}（池已按 rank 排序，本函数取前 hold_n）。
    """
    t0 = rebalance_days[0]
    days = [d for d in close.index if d >= t0]  # 末次调仓后继续持有到数据末日
    reb_set = set(rebalance_days)
    nav = pd.Series(index=days, dtype=float)
    nav[t0] = 1.0
    cur = list(holdings.get(t0, []))[:hold_n]
    prev_day = t0
    first = True
    for t in days:
        if t == t0:
            continue
        mult = (1.0 - cost) if first else 1.0   # 首期全额建仓成本
        first = False
        r = _period_return(cur, close.loc[prev_day], close.loc[t])
        if t in reb_set:
            new = list(holdings.get(t, []))[:hold_n]
            mult *= (1.0 - cost * _turnover(new, cur))
            cur = new
        nav[t] = nav[prev_day] * mult * (1.0 + r)
        prev_day = t
    return nav


def drawdown(nav: pd.Series) -> pd.Series:
    """回撤序列 = nav / cummax(nav) - 1（≤ 0）。"""
    return nav / nav.cummax() - 1.0


def benchmark_nav(close: pd.DataFrame, days: list) -> pd.Series:
    """市场等权基准净值：全市场日收益等权平均后累积，起点=1.0。"""
    sub = close.loc[[d for d in days if d in close.index]]
    rets = sub.pct_change(fill_method=None).mean(axis=1).fillna(0.0)
    return (1.0 + rets).cumprod()


def compute_metrics(nav: pd.Series, bench: pd.Series, holdings: dict, rebalance_days: list,
                    cost: float, hold_n: int, benchmark_name: str, run_tag: str,
                    init_capital: float, rebalance_label: str = "") -> dict:
    """由净值/基准序列与持仓生成指标字典（字段对齐设计文档 §5.3）。"""
    rets = nav.pct_change(fill_method=None).dropna()
    n = len(nav)
    final_nav = float(nav.iloc[-1])
    total_return = final_nav - 1.0
    annual = float(final_nav ** (TRADING_DAYS / n) - 1.0) if n > 1 and final_nav > 0 else 0.0
    dd = drawdown(nav)
    max_dd = float(dd.min()) if len(dd) else 0.0
    std = float(rets.std()) if len(rets) > 1 else 0.0
    sharpe = float(rets.mean() / std * np.sqrt(TRADING_DAYS)) if std > 0 else 0.0
    reb_nav = nav.reindex(rebalance_days).dropna()
    prets = reb_nav.pct_change(fill_method=None).dropna()
    win_rate = float((prets > 0).mean()) if len(prets) else 0.0
    tos = [1.0]  # 首期全额建仓
    for i in range(1, len(rebalance_days)):
        prev_codes = list(holdings.get(rebalance_days[i - 1], []))[:hold_n]
        cur_codes = list(holdings.get(rebalance_days[i], []))[:hold_n]
        tos.append(_turnover(cur_codes, prev_codes))
    avg_turnover = float(np.mean(tos)) if tos else 0.0
    bench_total = float(bench.iloc[-1] - 1.0) if len(bench) else 0.0
    return {
        "run_tag": run_tag,
        "start": str(nav.index[0]),
        "end": str(nav.index[-1]),
        "rebalance": rebalance_label,
        "hold_n": hold_n,
        "cost": cost,
        "benchmark": benchmark_name,
        "init_capital": init_capital,
        "final_nav": round(final_nav, 6),
        "total_return": round(total_return, 6),
        "annual_return": round(annual, 6),
        "max_drawdown": round(max_dd, 6),
        "sharpe": round(sharpe, 4),
        "win_rate_period": round(win_rate, 4),
        "avg_turnover": round(avg_turnover, 4),
        "benchmark_total_return": round(bench_total, 6),
        "excess_return": round(total_return - bench_total, 6),
        "n_periods": len(rebalance_days),
    }


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


def resolve_pool(pool_file: str, output_dir: Path) -> Path | None:
    """确定选股池路径：给定则用之；否则取 outputs/selection 下最新 select_*.csv。"""
    if (pool_file or "").strip():
        p = Path(pool_file.strip()).expanduser()
        return p if p.is_absolute() else (Path.cwd() / p)
    sel_dir = output_dir / "selection"
    if not sel_dir.is_dir():
        return None
    cands = sorted(sel_dir.glob("select_*.csv"), key=lambda x: x.stat().st_mtime)
    return cands[-1] if cands else None


def load_pool(path: Path) -> dict:
    """读选股池 CSV → {date: [code...]}（保持 rank 顺序、同股去重）。"""
    df = pd.read_csv(path, dtype=str)
    df.columns = [c.lstrip("\ufeff") for c in df.columns]
    out = {}
    for d, sub in df.groupby("date"):
        out[str(d)] = list(dict.fromkeys(sub["code"].astype(str).tolist()))
    return out


def read_close(data_dir: Path, min_date: str, max_date: str, codes=None) -> pd.DataFrame:
    """DuckDB 读日期/code/qfq_close 长表；codes 给出时按 code 过滤。"""
    import duckdb

    glob = Path(data_dir).as_posix() + "/*.parquet"
    sql = f"SELECT date, code, qfq_close FROM read_parquet('{glob}')"
    conds = []
    if min_date:
        conds.append(f"date >= '{min_date}'")
    if max_date:
        conds.append(f"date <= '{max_date}'")
    if codes:
        quoted = ", ".join("'" + str(c) + "'" for c in codes)
        conds.append(f"code IN ({quoted})")
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    return duckdb.connect().execute(sql).fetch_df()


def main() -> int:
    data_dir, output_dir = load_paths(DATA_DIR)
    if not data_dir.exists():
        print(f"[错误] 数据目录不存在: {data_dir}")
        return 1
    pool_path = resolve_pool(POOL_FILE, output_dir)
    if pool_path is None or not pool_path.is_file():
        print("[错误] 未找到选股池 CSV（请先在策略页运行「多因子选股」，或指定 pool_file）")
        return 1
    holdings = load_pool(pool_path)
    reb = sorted(holdings.keys())
    if not reb:
        print("[错误] 选股池为空")
        return 1
    print(f"[信息] 选股池 {pool_path}；调仓日 {len(reb)} 个；区间 {reb[0]} ~ {reb[-1]}")
    # 读取全市场（供 market_equal 基准）且不上封顶，末次调仓后持有到最新交易日
    raw = read_close(data_dir, reb[0], "")
    if raw.empty:
        print("[错误] 未读到任何行情数据")
        return 1
    close = raw.pivot(index="date", columns="code", values="qfq_close").sort_index()
    reb = [d for d in reb if d in close.index]
    if not reb:
        print("[错误] 选股池调仓日与行情交易日无交集")
        return 1
    nav = compute_nav(close, holdings, reb, HOLD_N, COST)
    bench = benchmark_nav(close, list(nav.index))
    tag = f"{pool_path.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    label = pool_path.stem.rsplit("_", 1)[-1] if "_" in pool_path.stem else ""
    mets = compute_metrics(nav, bench, holdings, reb, COST, HOLD_N, BENCHMARK, tag,
                           INIT_CAPITAL, rebalance_label=label)
    out_dir = output_dir / "backtest" / f"bt_{tag}"
    out_dir.mkdir(parents=True, exist_ok=True)
    table = pd.DataFrame({
        "date": nav.index,
        "nav": nav.values,
        "benchmark_nav": bench.reindex(nav.index).values,
        "drawdown": drawdown(nav).values,
    })
    table.to_csv(out_dir / "nav.csv", index=False, encoding="utf-8-sig")
    with open(out_dir / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(mets, f, ensure_ascii=False, indent=2)
    print(f"[完成] 期数 {mets['n_periods']}；期末净值 {mets['final_nav']:.4f}；"
          f"累计 {mets['total_return']:.2%}；年化 {mets['annual_return']:.2%}；"
          f"最大回撤 {mets['max_drawdown']:.2%}；夏普 {mets['sharpe']:.2f}；"
          f"超额 {mets['excess_return']:.2%}")
    print(f"[输出] {out_dir / 'nav.csv'} | {out_dir / 'metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())