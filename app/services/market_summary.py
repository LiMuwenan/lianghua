# -*- coding: utf-8 -*-
"""大盘汇总指标计算。

分两层：
- 纯函数层（本模块 compute_* / limit_threshold）：输入 DataFrame → 指标 dict，便于单测；
- IO 层（build_summary）：用 DuckDB 跨股读 parquet + index_store 读指数，拼装完整指标。

原则：数据缺失返回 None / 空 → 渲染层显示「数据缺失」，不造假。
"""
import math
from pathlib import Path

import pandas as pd

# 涨跌停幅度（近似口径，单位 %）
MAIN_BOARD = 10.0
STAR_GEM = 20.0
ST = 5.0
BEIJING = 30.0
TOL = 0.1   # 阈值容差：pctChg 达到 (阈值 ± 容差) 即判定

INDEX_NAMES = {
    "sh000001": "上证综指", "sz399001": "深证成指", "sz399006": "创业板指",
    "sh000688": "科创50", "sh000016": "上证50", "sh000300": "沪深300",
    "sh000905": "中证500", "sh000852": "中证1000",
}


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "1.0", "true", "yes")


def limit_threshold(code: str, is_st: bool) -> float:
    """涨跌停幅度（近似）：ST 5%，北交所 30%，科创/创业板 20%，其余 10%。"""
    if is_st:
        return ST
    c = str(code)
    market, num = (c[:2], c[2:]) if c[:2] in ("sh", "sz", "bj") else ("", c)
    if market == "bj" or num.startswith(("4", "8")):
        return BEIJING
    if num.startswith(("688", "689", "300", "301")):
        return STAR_GEM
    return MAIN_BOARD


def compute_breadth(df: pd.DataFrame) -> dict:
    """涨跌/平盘/涨停/跌停/停牌家数（剔停牌后统计涨跌）。"""
    cols = {"up": None, "down": None, "flat": None,
            "limit_up": None, "limit_down": None, "suspended": None, "total": 0}
    if df is None or df.empty:
        return cols

    st_num = pd.to_numeric(df.get("tradestatus"), errors="coerce")
    vol = pd.to_numeric(df.get("volume"), errors="coerce").fillna(0)
    suspended_mask = (st_num != 1) | (vol == 0)
    live = df[~suspended_mask]
    pct = pd.to_numeric(live["pctChg"], errors="coerce")

    limit_up = limit_down = 0
    for code, p, isst in zip(live["code"], pct, live.get("isST", pd.Series([0] * len(live)))):
        if p is None or (isinstance(p, float) and math.isnan(p)):
            continue
        th = limit_threshold(code, _truthy(isst))
        if p >= th - TOL:
            limit_up += 1
        elif p <= -(th - TOL):
            limit_down += 1

    return {
        "up": int((pct > 0).sum()),
        "down": int((pct < 0).sum()),
        "flat": int((pct == 0).sum()),
        "limit_up": int(limit_up),
        "limit_down": int(limit_down),
        "suspended": int(suspended_mask.sum()),
        "total": int(len(live)),
    }


def _num_sum(df: pd.DataFrame, col: str):
    if df is None or df.empty or col not in df.columns:
        return None
    s = pd.to_numeric(df[col], errors="coerce")
    return None if s.dropna().empty else float(s.sum())


def compute_volume(df: pd.DataFrame, prev_amount, ma5_amount) -> dict:
    """全市场成交额/量、平均换手率；对比前一日与近 5 日均值给出放量/缩量。"""
    if df is None or df.empty:
        return {"amount": None, "volume": None, "turn_avg": None,
                "vs_prev_pct": None, "vs_ma5_pct": None, "state": None}
    amount = _num_sum(df, "amount")
    volume = _num_sum(df, "volume")
    turn = pd.to_numeric(df.get("turn"), errors="coerce")
    turn_avg = None if turn.dropna().empty else float(turn.mean())

    vs_prev = (amount / prev_amount - 1) * 100 if (amount is not None and prev_amount) else None
    vs_ma5 = (amount / ma5_amount - 1) * 100 if (amount is not None and ma5_amount) else None
    state = None
    if vs_ma5 is not None:
        state = "放量" if vs_ma5 > 0 else "缩量"
    return {"amount": amount, "volume": volume, "turn_avg": turn_avg,
            "vs_prev_pct": vs_prev, "vs_ma5_pct": vs_ma5, "state": state}


def _percentile(series, value):
    """value 在 series 中的分位（%）：<= value 的比例。series 可含 None/NaN。"""
    if value is None:
        return None
    vals = [float(x) for x in (series or [])
            if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if not vals:
        return None
    return round(sum(1 for x in vals if x <= value) / len(vals) * 100, 2)


def compute_valuation(df: pd.DataFrame, hist_pe, hist_pb) -> dict:
    """PE/PB 中位数 + 近一年分位。"""
    if df is None or df.empty:
        return {"pe_median": None, "pb_median": None, "pe_pct_1y": None, "pb_pct_1y": None}
    pe = pd.to_numeric(df.get("peTTM"), errors="coerce")
    pb = pd.to_numeric(df.get("pbMRQ"), errors="coerce")
    pe_med = None if pe.dropna().empty else float(pe.median())
    pb_med = None if pb.dropna().empty else float(pb.median())
    return {"pe_median": pe_med, "pb_median": pb_med,
            "pe_pct_1y": _percentile(hist_pe, pe_med),
            "pb_pct_1y": _percentile(hist_pb, pb_med)}


def compute_industry(df: pd.DataFrame, industry_map: dict, top_n: int = 5) -> dict:
    """按行业分组求平均涨跌幅，返回领涨/领跌 TOP n。industry_map: {code: industry}。"""
    if df is None or df.empty:
        return {"top": [], "bottom": []}
    d = df.copy()
    d["industry"] = d["code"].map(lambda c: (industry_map or {}).get(c, "") or "")
    d = d[d["industry"] != ""]
    d["pctChg"] = pd.to_numeric(d["pctChg"], errors="coerce")
    d = d[d["pctChg"].notna()]
    if d.empty:
        return {"top": [], "bottom": []}
    g = d.groupby("industry")["pctChg"].agg(["mean", "count"]).sort_values("mean", ascending=False)

    def _items(frame):
        return [{"industry": str(i), "avg_pct": round(float(r["mean"]), 2),
                 "count": int(r["count"])} for i, r in frame.iterrows()]

    return {"top": _items(g.head(top_n)), "bottom": _items(g.tail(top_n).iloc[::-1])}


def compute_hot(df: pd.DataFrame, name_map: dict = None, top_n: int = 10) -> dict:
    """涨/跌幅榜 TOP n（剔停牌），附股票名称。"""
    if df is None or df.empty:
        return {"gainers": [], "losers": []}
    st_num = pd.to_numeric(df.get("tradestatus"), errors="coerce")
    vol = pd.to_numeric(df.get("volume"), errors="coerce").fillna(0)
    live = df[(st_num == 1) & (vol > 0)].copy()
    live["pctChg"] = pd.to_numeric(live["pctChg"], errors="coerce")
    live = live[live["pctChg"].notna()]

    def _items(frame):
        return [{"code": str(r["code"]), "name": (name_map or {}).get(r["code"], ""),
                 "pctChg": round(float(r["pctChg"]), 2)} for _, r in frame.iterrows()]

    g = live.sort_values("pctChg", ascending=False)
    return {"gainers": _items(g.head(top_n)), "losers": _items(g.tail(top_n).iloc[::-1])}


def make_conclusion(index_rows, breadth, volume) -> str:
    """一句话结论：由指数涨跌 + 涨跌家数比 + 放量/缩量规则生成。"""
    parts = []
    sh = next((r for r in (index_rows or []) if str(r["code"]).endswith("000001")), None)
    if sh and sh.get("pctChg") is not None:
        parts.append(f"上证综指{'+' if sh['pctChg'] >= 0 else ''}{sh['pctChg']:.2f}%")
    if breadth and breadth.get("up") is not None:
        parts.append(f"上涨{breadth['up']}家/下跌{breadth['down']}家")
    if volume and volume.get("state"):
        parts.append(volume["state"])
    return "，".join(parts) + "。" if parts else "数据缺失，暂无法生成结论。"


# ---------- IO 层 ----------

def _parquet_glob(data_dir) -> str:
    return Path(data_dir).as_posix().rstrip("/") + "/*.parquet"


def _has_parquet(data_dir) -> bool:
    d = Path(data_dir)
    return d.is_dir() and any(d.glob("*.parquet"))


def _read_cross_section(data_dir, trade_date: str) -> pd.DataFrame:
    """读全市场当日的横截面（DuckDB 谓词下推）。无文件 → 空表。"""
    cols = "code,pctChg,tradestatus,volume,amount,turn,peTTM,pbMRQ,isST"
    if not _has_parquet(data_dir):
        return pd.DataFrame(columns=cols.split(","))
    import duckdb
    con = duckdb.connect()
    try:
        q = (f"SELECT {cols} FROM read_parquet('{_parquet_glob(data_dir)}') "
             f"WHERE date = '{trade_date}'")
        return con.execute(q).df()
    finally:
        con.close()


def _daily_amount_series(data_dir, trade_date: str, n: int = 6) -> list:
    """按日聚合的全市场成交额，返回 [(date, amount)] 降序（含当日）。"""
    if not _has_parquet(data_dir):
        return []
    import duckdb
    con = duckdb.connect()
    try:
        q = (f"SELECT date, SUM(amount) AS amount FROM "
             f"read_parquet('{_parquet_glob(data_dir)}') WHERE date <= '{trade_date}' "
             f"GROUP BY date ORDER BY date DESC LIMIT {n}")
        return [(str(d), float(a) if a is not None else 0.0) for d, a in con.execute(q).fetchall()]
    finally:
        con.close()


def _daily_median_series(data_dir, trade_date: str, col: str, days: int = 250) -> list:
    """按日的某列全市场中位数序列（近 days 日，含当日），用于算近一年分位。"""
    if not _has_parquet(data_dir):
        return []
    import duckdb
    con = duckdb.connect()
    try:
        q = (f"SELECT MEDIAN({col}) AS m FROM read_parquet('{_parquet_glob(data_dir)}') "
             f"WHERE date <= '{trade_date}' GROUP BY date ORDER BY date DESC LIMIT {days}")
        return [float(m) if m is not None else None for (m,) in con.execute(q).fetchall()]
    finally:
        con.close()


def _index_rows(index_dir, code: str, trade_date: str):
    """取某指数当日行 → dict（无数据返回 None）。"""
    from . import index_store
    df = index_store.read_index(index_dir, code)
    if df.empty:
        return None
    df = df.copy()
    df["date"] = df["date"].astype(str)
    sub = df[df["date"] == trade_date]
    if sub.empty:
        return None
    r = sub.iloc[0]
    norm = index_store.normalize(code)
    return {
        "code": norm, "name": INDEX_NAMES.get(norm, norm),
        "close": None if pd.isna(r["close"]) else float(r["close"]),
        "pctChg": None if pd.isna(r["pctChg"]) else round(float(r["pctChg"]), 2),
        "amount": None if pd.isna(r["amount"]) else float(r["amount"]),
    }


def build_summary(data_dir, index_dir, trade_date: str, index_codes,
                  industry_map: dict, name_map: dict) -> dict:
    """拼装完整指标 dict。data_dir/index_dir 为目录路径。"""
    missing = []

    cross = _read_cross_section(data_dir, trade_date)
    breadth = compute_breadth(cross)
    if breadth["total"] == 0:
        missing.append("市场宽度")

    series = _daily_amount_series(data_dir, trade_date, n=6)
    prev_amount = series[1][1] if len(series) > 1 else None
    ma5 = [a for _, a in series[1:6]]
    ma5_amount = (sum(ma5) / len(ma5)) if ma5 else None
    volume = compute_volume(cross, prev_amount, ma5_amount)
    if volume["amount"] is None:
        missing.append("量能")

    hist_pe = _daily_median_series(data_dir, trade_date, "peTTM", 250)
    hist_pb = _daily_median_series(data_dir, trade_date, "pbMRQ", 250)
    valuation = compute_valuation(cross, hist_pe, hist_pb)
    if valuation["pe_median"] is None:
        missing.append("估值")

    industry = compute_industry(cross, industry_map)
    if not industry["top"] and not industry["bottom"]:
        missing.append("行业")

    hot = compute_hot(cross, name_map)
    if not hot["gainers"] and not hot["losers"]:
        missing.append("个股热度")

    rows = []
    for code in index_codes:
        r = _index_rows(index_dir, code, trade_date)
        if r is not None:
            rows.append(r)
    index_block = {"available": bool(rows), "rows": rows}
    if not rows:
        missing.append("核心指数")

    return {
        "trade_date": trade_date,
        "index": index_block,
        "breadth": breadth,
        "volume": volume,
        "valuation": valuation,
        "industry": industry,
        "hot": hot,
        "conclusion": make_conclusion(rows, breadth, volume),
        "missing": sorted(set(missing)),
    }
