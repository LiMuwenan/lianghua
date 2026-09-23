"""
A股：趋势 + 触线 + 反转 条件概率统计（多线程/多进程版）

逻辑：
  趋势：MA60 斜率
  触线：close 进入均线 1% 缓冲带
  反转：未来 N 日内 close 穿越目标均线
  基准：同一趋势下无条件反转率
  超额 = 信号反转率 - 基准率

输出：
  结果.xlsx  两个 sheet
    1) 下跌趋势_反转向上
    2) 上涨趋势_反转向下


一、逻辑总览（先看懂再跑）
1. 趋势判定（MA60 斜率）
text
MA60_slope = MA60[t] - MA60[t-20]
趋势 = 上涨  if MA60_slope > 0
       下跌  if MA60_slope < 0
2. 触线判定（1% 缓冲带）
场景	条件
下跌趋势中"接近/跌破"	close ≤ MA × (1 + 1%)
上涨趋势中"接近/突破"	close ≥ MA × (1 - 1%)
3. 状态机去重叠（关键！）
只在价格从区域外第一次进入区域的那天记一次样本，之后要等价格离开区域才能再次触发。这样每个样本都是独立事件。

4. 反转定义
下跌趋势触线 → 未来 10 日内 close 重新站上目标均线 = 成功

上涨趋势触线 → 未来 10 日内 close 重新跌破目标均线 = 成功

目标均线 = 组合中最长周期的那条（代表"真正反转"）

5. 对照组基准率
在同一趋势的所有日子里，发生同样反转的概率 → 这才是"没有信号"的基准。超额 = 信号率 − 基准率，只有超额为正才说明信号有效。


四、怎么读结果（重要）
拿到表之后，按这个顺序看：

第 1 步：看样本数
< 30 → 统计不可信，忽略

30 ~ 100 → 参考

> 100 → 可用

第 2 步：看超额，别看反转率
超额 = 信号反转率 − 基准率

超额 ≤ 0 → 信号无效（和随机没区别）

超额 0~3% → 弱

超额 3%~5% → 中等

超额 > 5% → 强信号

第 3 步：横向对比哪条均线最有效
如果发现"跌破 20 日线后反转率超额最大"，那么 MA20 就是你该用的触线参考。

第 4 步：选股
可以把所有股票的"20 日线超额"排序，取超额为正且样本 > 50 的股票作为策略标的池。

五、跑通后你可能想改的 3 个地方
想改什么	改哪个变量
反转窗口 N=5 / 20	REV_WINDOW = 5
缓冲带 0.5% / 2%	BUFFER = 0.005
趋势强弱阈值（要求斜率必须涨够多少）	在 load_and_prepare 里把 df["ma60_slope"] > 0 改成 > 0.02 * df["ma60"] 之类

六、下一步建议
跑完第一版后，把汇总结果（结果.xlsx 里的数据）发我，我帮你：

做全市场汇总：把每只股票的超额按组合取平均，看哪条均线整体最有效

做分层验证：按市值/行业分组，看信号是否稳健

做回测框架：把"触线 + 反转"转成真实持仓规则，算年化、回撤、夏普

多窗口扫描：N=5/10/20 三档一起跑，找最优点
"""

import os
import glob
import time
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed

# ================= 可调参数 =================
DATA_DIR     = "A股日K数据"
OUT_FILE     = "结果.xlsx"
YEARS        = 10        # 只用近 N 年数据
BUFFER       = 0.01      # 1% 缓冲带
MA_PERIODS   = [5, 20, 60, 180]
SLOPE_WINDOW = 20        # MA60 斜率回看天数
REV_WINDOW   = 10        # 反转判定窗口（未来 N 日）

MAX_WORKERS  = 12         # ★ 线程/进程数，自行设置
USE_PROCESS  = True     # ★ True=多进程（更快，但内存占用高）；False=多线程

# ===========================================

COMBOS = [
    ("5日",         [5]),
    ("20日",        [20]),
    ("60日",        [60]),
    ("180日",       [180]),
]


# ============================================================
# 单只股票处理
# ============================================================

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


def compute_signal_stats(df, periods, trend_dir, rev_window, buffer):
    cond = pd.Series(True, index=df.index)
    for p in periods:
        ma = df[f"ma{p}"]
        c  = df["close"]
        if trend_dir == "down":
            cond &= ma.notna() & (c <= ma * (1 + buffer))
        else:
            cond &= ma.notna() & (c >= ma * (1 - buffer))

    trend_mask = df["trend"] == trend_dir

    target_p   = max(periods)
    target_col = f"ma{target_p}"

    future_rev = np.zeros(len(df), dtype=bool)
    for i in range(len(df)):
        end_i = min(i + rev_window, len(df) - 1)
        if end_i <= i:
            continue
        fut = df.iloc[i + 1: end_i + 1]
        if trend_dir == "down":
            ok = (fut["close"] > fut[target_col]).any()
        else:
            ok = (fut["close"] < fut[target_col]).any()
        future_rev[i] = bool(ok)

    signal_idx = []
    prev_in    = False
    cond_arr   = cond.values
    trend_arr  = trend_mask.values
    for i in range(len(df)):
        cur_in = bool(cond_arr[i]) and bool(trend_arr[i])
        if cur_in and not prev_in:
            signal_idx.append(i)
        prev_in = cur_in

    n_signal  = len(signal_idx)
    n_reverse = int(sum(future_rev[i] for i in signal_idx))

    base_idx        = [i for i in df.index[trend_mask] if i < len(df) - 1]
    n_base          = len(base_idx)
    n_base_reverse  = int(sum(future_rev[i] for i in base_idx))

    return n_signal, n_reverse, n_base, n_base_reverse


def process_one_stock(fp, buffer, rev_window):
    stock = os.path.splitext(os.path.basename(fp))[0]
    df = load_and_prepare(fp)
    if df is None or len(df) < rev_window + 2:
        return None

    row = {"股票": stock}

    for name, periods in COMBOS:
        ns, nr, nb, nbr = compute_signal_stats(df, periods, "down", rev_window, buffer)
        sig_rate  = nr / ns if ns > 0 else np.nan
        base_rate = nbr / nb if nb > 0 else np.nan
        row[f"跌_{name}_样本"]   = ns
        row[f"跌_{name}_反转率"] = round(sig_rate, 4)  if not np.isnan(sig_rate)  else np.nan
        row[f"跌_{name}_基准率"] = round(base_rate, 4) if not np.isnan(base_rate) else np.nan
        row[f"跌_{name}_超额"]   = (
            round(sig_rate - base_rate, 4)
            if not (np.isnan(sig_rate) or np.isnan(base_rate)) else np.nan
        )

    for name, periods in COMBOS:
        ns, nr, nb, nbr = compute_signal_stats(df, periods, "up", rev_window, buffer)
        sig_rate  = nr / ns if ns > 0 else np.nan
        base_rate = nbr / nb if nb > 0 else np.nan
        row[f"涨_{name}_样本"]   = ns
        row[f"涨_{name}_反转率"] = round(sig_rate, 4)  if not np.isnan(sig_rate)  else np.nan
        row[f"涨_{name}_基准率"] = round(base_rate, 4) if not np.isnan(base_rate) else np.nan
        row[f"涨_{name}_超额"]   = (
            round(sig_rate - base_rate, 4)
            if not (np.isnan(sig_rate) or np.isnan(base_rate)) else np.nan
        )

    return row


# ============================================================
# 主流程
# ============================================================

def main():
    files = sorted(glob.glob(os.path.join(DATA_DIR, "*.xlsx")))
    if not files:
        print(f"未找到 {DATA_DIR}/*.xlsx")
        return

    total = len(files)
    print(f"共发现 {total} 个股票文件")
    print(f"并发模式: {'多进程' if USE_PROCESS else '多线程'} | 并发数: {MAX_WORKERS}")
    print("-" * 60)

    Executor = ProcessPoolExecutor if USE_PROCESS else ThreadPoolExecutor
    results  = []
    failed   = []

    t0 = time.time()

    # ★ 核心：提交所有任务到线程池/进程池，边完成边收集
    with Executor(max_workers=MAX_WORKERS) as executor:
        future_to_file = {
            executor.submit(process_one_stock, fp, BUFFER, REV_WINDOW): fp
            for fp in files
        }

        for done_cnt, future in enumerate(as_completed(future_to_file), 1):
            fp    = future_to_file[future]
            stock = os.path.splitext(os.path.basename(fp))[0]
            try:
                row = future.result()
                if row is None:
                    print(f"[{done_cnt:>4}/{total}] 跳过  {stock}")
                    failed.append((stock, "数据不足"))
                else:
                    results.append(row)
                    print(f"[{done_cnt:>4}/{total}] 完成  {stock}")
            except Exception as e:
                print(f"[{done_cnt:>4}/{total}] 错误  {stock}: {e}")
                failed.append((stock, str(e)))

    elapsed = time.time() - t0
    print("-" * 60)
    print(f"处理完毕，耗时 {elapsed:.1f} 秒")
    print(f"成功: {len(results)}  失败/跳过: {len(failed)}")

    if not results:
        print("没有任何结果输出")
        return

    # ---------- 汇总 ----------
    out = pd.DataFrame(results)

    stock_col = ["股票"]
    down_cols = stock_col + [c for c in out.columns if c.startswith("跌_")]
    up_cols   = stock_col + [c for c in out.columns if c.startswith("涨_")]

    with pd.ExcelWriter(OUT_FILE, engine="openpyxl") as writer:
        out[down_cols].to_excel(writer, sheet_name="下跌趋势_反转向上", index=False)
        out[up_cols].to_excel(writer, sheet_name="上涨趋势_反转向下", index=False)

        # 额外加一个"失败清单" sheet，方便排查
        if failed:
            pd.DataFrame(failed, columns=["股票", "原因"]).to_excel(
                writer, sheet_name="失败清单", index=False
            )

    print(f"\n结果已保存: {OUT_FILE}")
    print("  Sheet1: 下跌趋势_反转向上")
    print("  Sheet2: 上涨趋势_反转向下")
    if failed:
        print("  Sheet3: 失败清单")


"""
全市场汇总：读取结果.xlsx，输出各组合的汇总统计
三、下一步建议
1. 立即可以做的
运行上面脚本，得到精确的全市场汇总表

看"超额>0占比"这一列：如果某组合 > 55%，说明信号在多数股票上都有效

2. 值得深挖的方向
方向一：加入趋势强度过滤

现在只用了 MA60 斜率正负，太粗糙

可以改成：斜率 > 某个阈值 才算"强趋势"

预期：强趋势中的触线信号超额会更大

方向二：加入波动率过滤

高波动股票的触线信号可能更有效（或更无效）

用 ATR/价格 分档统计

方向三：做真实回测

现在的"反转率"只是统计，没有考虑：

持仓周期

止损止盈

交易成本

建议选 60 日线 + 20 日反转窗口，做一次简单回测

方向四：行业/市值分层

把股票按行业、市值分组，看信号是否稳健

有些信号可能只在大盘股有效，有些只在小盘股有效
"""
INPUT = "结果.xlsx"
OUTPUT = "汇总分析.xlsx"

COMBOS2 = ["5日", "20日", "60日", "180日"]


def summarize_sheet(df, prefix):
    """对单个sheet做汇总"""
    rows = []
    for combo in COMBOS2:
        sample_col  = f"{prefix}_{combo}_样本"
        rev_col     = f"{prefix}_{combo}_反转率"
        base_col    = f"{prefix}_{combo}_基准率"
        excess_col  = f"{prefix}_{combo}_超额"

        if sample_col not in df.columns:
            continue

        # 只保留样本数 >= 30 的股票（过滤噪声）
        valid = df[df[sample_col] >= 30].copy()

        n_stocks = len(valid)
        if n_stocks == 0:
            rows.append({
                "组合": combo, "有效股票数": 0,
                "平均样本数": np.nan,
                "平均反转率": np.nan, "平均基准率": np.nan, "平均超额": np.nan,
                "超额>0占比": np.nan, "超额>5%占比": np.nan,
            })
            continue

        # 简单平均
        avg_sample  = valid[sample_col].mean()
        avg_rev     = valid[rev_col].mean()
        avg_base    = valid[base_col].mean()
        avg_excess  = valid[excess_col].mean()

        # 样本加权平均
        w = valid[sample_col]
        w_avg_rev    = np.average(valid[rev_col], weights=w)
        w_avg_base   = np.average(valid[base_col], weights=w)
        w_avg_excess = np.average(valid[excess_col], weights=w)

        # 超额为正的股票占比
        pos_ratio   = (valid[excess_col] > 0).mean()
        pos5_ratio  = (valid[excess_col] > 0.05).mean()

        rows.append({
            "组合": combo,
            "有效股票数": n_stocks,
            "平均样本数": round(avg_sample, 1),
            "平均反转率": round(avg_rev, 4),
            "平均基准率": round(avg_base, 4),
            "平均超额": round(avg_excess, 4),
            "加权反转率": round(w_avg_rev, 4),
            "加权基准率": round(w_avg_base, 4),
            "加权超额": round(w_avg_excess, 4),
            "超额>0占比": round(pos_ratio, 4),
            "超额>5%占比": round(pos5_ratio, 4),
        })

    return pd.DataFrame(rows)

def agg():
    xls = pd.ExcelFile(INPUT)

    down = pd.read_excel(xls, sheet_name="下跌趋势_反转向上")
    up = pd.read_excel(xls, sheet_name="上涨趋势_反转向下")

    # 排除指数（sh000xxx、sz399xxx），只保留个股
    def is_stock(code):
        c = str(code).lower()
        if c.startswith("sh000") or c.startswith("sz399"):
            return False
        return True

    down_stock = down[down["股票"].apply(is_stock)].copy()
    up_stock = up[up["股票"].apply(is_stock)].copy()

    print(f"下跌趋势：全样本 {len(down)} 只，个股 {len(down_stock)} 只")
    print(f"上涨趋势：全样本 {len(up)} 只，个股 {len(up_stock)} 只")

    sum_down = summarize_sheet(down_stock, "跌")
    sum_up = summarize_sheet(up_stock, "涨")

    print("\n===== 下跌趋势_反转向上 =====")
    print(sum_down.to_string(index=False))
    print("\n===== 上涨趋势_反转向下 =====")
    print(sum_up.to_string(index=False))

    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        sum_down.to_excel(writer, sheet_name="汇总_下跌反转", index=False)
        sum_up.to_excel(writer, sheet_name="汇总_上涨反转", index=False)

        # 额外：按超额排序的个股榜单
        for prefix, df, name in [
            ("跌", down_stock, "个股_下跌反转"),
            ("涨", up_stock, "个股_上涨反转"),
        ]:
            for combo in COMBOS2:
                excess_col = f"{prefix}_{combo}_超额"
                sample_col = f"{prefix}_{combo}_样本"
                if excess_col not in df.columns:
                    continue
                valid = df[df[sample_col] >= 30].copy()
                valid = valid.sort_values(excess_col, ascending=False)
                valid[["股票", sample_col, excess_col]].head(50).to_excel(
                    writer, sheet_name=f"{name}_{combo}_Top50", index=False
                )

    print(f"\n汇总已保存: {OUTPUT}")


if __name__ == "__main__":
    # main()
    agg()