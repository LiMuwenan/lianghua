"""
任务A：改进基准率——用距离分档匹配消除位置效应
"""

import os
import glob
import time
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============ 可调参数 ============
DATA_DIR     = "A股日K数据"
OUT_FILE     = "结果_匹配基准.xlsx"
YEARS        = 10
BUFFER       = 0.01       # 1% 缓冲带
MA_PERIODS   = [5, 20, 60, 180]
SLOPE_WINDOW = 20
REV_WINDOW   = 10
MAX_WORKERS  = 8

# 距离分档参数
BUCKET_SIZE  = 0.005      # 0.5% 一档
MIN_BUCKET_N = 5          # 每档至少5个基准样本，否则合并相邻档
# ===================================

COMBOS = [
    ("5日",   5), ("20日", 20), ("60日", 60), ("180日", 180),
]


def load_and_prepare(fp):
    df = pd.read_excel(fp)
    if "date" not in df.columns or "close" not in df.columns:
        return None

    df = df.copy()
    df["date"]  = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["close"]).reset_index(drop=True)

    if len(df) < max(MA_PERIODS) + SLOPE_WINDOW + REV_WINDOW + 5:
        return None

    for p in MA_PERIODS:
        df[f"ma{p}"] = df["close"].rolling(p).mean()

    df["ma60_slope"] = df["ma60"] - df["ma60"].shift(SLOPE_WINDOW)

    df["trend"] = "unknown"
    df.loc[df["ma60_slope"] > 0, "trend"] = "up"
    df.loc[df["ma60_slope"] < 0, "trend"] = "down"

    end_date   = df["date"].max()
    start_date = end_date - pd.DateOffset(years=YEARS)
    df = df[df["date"] >= start_date].reset_index(drop=True)

    return df


def compute_stats_with_matched_baseline(df, p, trend_dir, rev_window, buffer,
                                        bucket_size, min_bucket_n):
    """
    返回：
      n_signal, n_reverse, signal_rate,
      matched_base_rate, matched_n,
      raw_base_rate, raw_base_n
    """
    ma_col     = f"ma{p}"
    trend_mask = (df["trend"] == trend_dir).values
    ma_arr     = df[ma_col].values
    close_arr  = df["close"].values

    n_total = len(df)

    # ---------- 预计算：每 i 在未来 N 日内是否反转 ----------
    future_rev = np.zeros(n_total, dtype=bool)
    for i in range(n_total):
        end_i = min(i + rev_window, n_total - 1)
        if end_i <= i:
            continue
        fut_close = close_arr[i + 1: end_i + 1]
        fut_ma    = ma_arr[i + 1: end_i + 1]
        valid     = ~np.isnan(fut_ma)
        if not valid.any():
            continue
        if trend_dir == "down":
            ok = (fut_close[valid] > fut_ma[valid]).any()
        else:
            ok = (fut_close[valid] < fut_ma[valid]).any()
        future_rev[i] = bool(ok)

    # ---------- 触线条件 ----------
    if trend_dir == "down":
        cond = (~np.isnan(ma_arr)) & (close_arr <= ma_arr * (1 + buffer))
    else:
        cond = (~np.isnan(ma_arr)) & (close_arr >= ma_arr * (1 - buffer))

    # ---------- 状态机：信号日索引 ----------
    signal_idx = []
    prev_in = False
    for i in range(n_total):
        cur_in = bool(cond[i]) and bool(trend_mask[i])
        if cur_in and not prev_in:
            signal_idx.append(i)
        prev_in = cur_in

    # ---------- 基准池：所有下跌趋势日 + 它们的距离档 ----------
    base_dists = []
    base_revs  = []
    for i in range(n_total - 1):
        if not trend_mask[i]:
            continue
        if np.isnan(ma_arr[i]):
            continue
        dist = (close_arr[i] - ma_arr[i]) / ma_arr[i]
        base_dists.append(dist)
        base_revs.append(future_rev[i])
    base_dists = np.array(base_dists)
    base_revs  = np.array(base_revs, dtype=bool)

    # ---------- 原始基准 ----------
    raw_base_n       = len(base_revs)
    raw_base_reverse = int(base_revs.sum()) if raw_base_n > 0 else 0
    raw_base_rate    = (raw_base_reverse / raw_base_n) if raw_base_n > 0 else np.nan

    # ---------- 匹配基准 ----------
    # 对每个信号日，在 base 池中找 dist 距离 < bucket_size/2 的样本
    matched_rates = []
    matched_total = 0
    for i in signal_idx:
        if np.isnan(ma_arr[i]):
            continue
        s_dist = (close_arr[i] - ma_arr[i]) / ma_arr[i]
        # 找同档位
        in_bucket = np.abs(base_dists - s_dist) < (bucket_size / 2)
        if in_bucket.sum() >= min_bucket_n:
            r = base_revs[in_bucket].mean()
            matched_rates.append(r)
            matched_total += in_bucket.sum()

    matched_base_rate = float(np.mean(matched_rates)) if matched_rates else np.nan

    # ---------- 信号结果 ----------
    n_signal  = len(signal_idx)
    n_reverse = int(sum(future_rev[i] for i in signal_idx)) if n_signal > 0 else 0
    signal_rate = (n_reverse / n_signal) if n_signal > 0 else np.nan

    return {
        "n_signal": n_signal,
        "n_reverse": n_reverse,
        "signal_rate": signal_rate,
        "raw_base_rate": raw_base_rate,
        "raw_base_n": raw_base_n,
        "matched_base_rate": matched_base_rate,
        "matched_n_avg": (matched_total / len(matched_rates)) if matched_rates else 0,
    }


def process_one_stock(fp, buffer, rev_window, bucket_size, min_bucket_n):
    stock = os.path.splitext(os.path.basename(fp))[0]
    df = load_and_prepare(fp)
    if df is None or len(df) < rev_window + 2:
        return None

    row = {"股票": stock}

    for label, p in COMBOS:
        # 下跌趋势 → 反转向上
        s = compute_stats_with_matched_baseline(
            df, p, "down", rev_window, buffer, bucket_size, min_bucket_n)
        row[f"跌_{label}_样本"]         = s["n_signal"]
        row[f"跌_{label}_反转次数"]     = s["n_reverse"]
        row[f"跌_{label}_反转率"]       = round(s["signal_rate"], 4) if not np.isnan(s["signal_rate"]) else np.nan
        row[f"跌_{label}_原基准率"]     = round(s["raw_base_rate"], 4) if not np.isnan(s["raw_base_rate"]) else np.nan
        row[f"跌_{label}_原超额"]       = (round(s["signal_rate"] - s["raw_base_rate"], 4)
                                            if not (np.isnan(s["signal_rate"]) or np.isnan(s["raw_base_rate"])) else np.nan)
        row[f"跌_{label}_匹配基准率"]   = round(s["matched_base_rate"], 4) if not np.isnan(s["matched_base_rate"]) else np.nan
        row[f"跌_{label}_匹配超额"]     = (round(s["signal_rate"] - s["matched_base_rate"], 4)
                                            if not (np.isnan(s["signal_rate"]) or np.isnan(s["matched_base_rate"])) else np.nan)

        # 上涨趋势 → 反转向下
        s = compute_stats_with_matched_baseline(
            df, p, "up", rev_window, buffer, bucket_size, min_bucket_n)
        row[f"涨_{label}_样本"]         = s["n_signal"]
        row[f"涨_{label}_反转次数"]     = s["n_reverse"]
        row[f"涨_{label}_反转率"]       = round(s["signal_rate"], 4) if not np.isnan(s["signal_rate"]) else np.nan
        row[f"涨_{label}_原基准率"]     = round(s["raw_base_rate"], 4) if not np.isnan(s["raw_base_rate"]) else np.nan
        row[f"涨_{label}_原超额"]       = (round(s["signal_rate"] - s["raw_base_rate"], 4)
                                            if not (np.isnan(s["signal_rate"]) or np.isnan(s["raw_base_rate"])) else np.nan)
        row[f"涨_{label}_匹配基准率"]   = round(s["matched_base_rate"], 4) if not np.isnan(s["matched_base_rate"]) else np.nan
        row[f"涨_{label}_匹配超额"]     = (round(s["signal_rate"] - s["matched_base_rate"], 4)
                                            if not (np.isnan(s["signal_rate"]) or np.isnan(s["matched_base_rate"])) else np.nan)

    return row


def main():
    files = sorted(glob.glob(os.path.join(DATA_DIR, "*.xlsx")))
    if not files:
        print(f"未找到 {DATA_DIR}/*.xlsx")
        return

    total = len(files)
    print(f"共 {total} 个文件 | 并发 {MAX_WORKERS}")
    print("-" * 70)

    results = []
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(process_one_stock, fp, BUFFER, REV_WINDOW,
                            BUCKET_SIZE, MIN_BUCKET_N): fp
            for fp in files
        }
        for done, future in enumerate(as_completed(futures), 1):
            fp = futures[future]
            stock = os.path.splitext(os.path.basename(fp))[0]
            try:
                row = future.result()
                if row:
                    results.append(row)
                    if done % 100 == 0 or done == total:
                        print(f"[{done}/{total}] 完成 {stock}")
            except Exception as e:
                print(f"[{done}/{total}] 错误 {stock}: {e}")

    print("-" * 70)
    print(f"耗时 {time.time()-t0:.1f} 秒 | 成功 {len(results)}")

    if not results:
        return

    out = pd.DataFrame(results)
    cols = ["股票"] + [c for c in out.columns if c != "股票"]
    out = out[cols]

    # 排除指数
    def is_stock(code):
        c = str(code).lower()
        return not (c.startswith("sh000") or c.startswith("sz399"))

    out_stock = out[out["股票"].apply(is_stock)].copy()

    # ---------- 汇总对比 ----------
    summary_rows = []
    for label, _ in COMBOS:
        for prefix, name in [("跌", "下跌趋势→反转向上"), ("涨", "上涨趋势→反转向下")]:
            sample_col  = f"{prefix}_{label}_样本"
            rev_col     = f"{prefix}_{label}_反转率"
            raw_col     = f"{prefix}_{label}_原超额"
            mat_col     = f"{prefix}_{label}_匹配超额"

            if sample_col not in out_stock.columns:
                continue

            valid = out_stock[out_stock[sample_col] >= 30]
            if len(valid) == 0:
                continue

            summary_rows.append({
                "方向": name,
                "均线": label,
                "有效股票数": len(valid),
                "平均样本数": round(valid[sample_col].mean(), 1),
                "平均反转率": round(valid[rev_col].mean(), 4),
                "原超额均值": round(valid[raw_col].mean(), 4),
                "匹配超额均值": round(valid[mat_col].mean(), 4),
                "原超额>0占比": round((valid[raw_col] > 0).mean(), 4),
                "匹配超额>0占比": round((valid[mat_col] > 0).mean(), 4),
                "原超额>5%占比": round((valid[raw_col] > 0.05).mean(), 4),
                "匹配超额>5%占比": round((valid[mat_col] > 0.05).mean(), 4),
            })

    summary = pd.DataFrame(summary_rows)

    with pd.ExcelWriter(OUT_FILE, engine="openpyxl") as writer:
        out.to_excel(writer, sheet_name="全部结果", index=False)
        summary.to_excel(writer, sheet_name="汇总对比", index=False)

    print("\n===== 原基准 vs 匹配基准 =====")
    print(summary.to_string(index=False))
    print(f"\n结果已保存: {OUT_FILE}")


if __name__ == "__main__":
    main()