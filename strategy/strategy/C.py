import pandas as pd
import numpy as np
import os
import glob
from concurrent.futures import ProcessPoolExecutor, as_completed

# ============ 参数 ============
DATA_DIR = "A股日K数据"
OUTPUT_TRADES  = "回测交易明细_60日.xlsx"
OUTPUT_STOCK   = "回测个股汇总_60日.xlsx"
OUTPUT_SUMMARY = "回测全市场汇总_60日.xlsx"

MA_PERIOD     = 60      # 均线周期
SLOPE_WINDOW  = 20      # 斜率窗口
BUFFER        = 0.01    # 触线缓冲：close <= MA * (1+BUFFER)
REV_WINDOW    = 10      # 最大持仓交易日
STOP_LOSS     = 0.95    # 止损线：跌破入场价 5%
COST          = 0.002   # 双边总成本 0.2%
ENTRY_OFFSET  = 1       # 信号后第几个交易日收盘价入场，0=当日，1=次日
MAX_WORKERS   = 8
MIN_TRADES    = 30      # 过滤汇总时最少交易数


def simulate_trade(close, ma, dates, signal_idx):
    """从 signal_idx 之后 ENTRY_OFFSET 天收盘价入场，模拟到出场。"""
    n = len(close)
    entry_idx = signal_idx + ENTRY_OFFSET
    if entry_idx >= n - 1:
        return None
    entry_price = close[entry_idx]
    if entry_price <= 0:
        return None
    entry_date = dates[entry_idx]

    exit_idx = None
    exit_reason = None

    for i in range(1, REV_WINDOW + 1):
        t = entry_idx + i
        if t >= n:
            break
        # 止盈：收盘价重新站上 MA
        if not np.isnan(ma[t]) and close[t] > ma[t]:
            exit_idx = t
            exit_reason = "止盈"
            break
        # 止损：跌破入场价 * STOP_LOSS
        if close[t] <= entry_price * STOP_LOSS:
            exit_idx = t
            exit_reason = "止损"
            break

    if exit_idx is None:
        exit_idx = min(entry_idx + REV_WINDOW, n - 1)
        exit_reason = "时间"

    exit_price = close[exit_idx]
    gross = (exit_price - entry_price) / entry_price
    net = gross - COST

    return {
        "入场日期": entry_date,
        "入场价": round(float(entry_price), 4),
        "出场日期": dates[exit_idx],
        "出场价": round(float(exit_price), 4),
        "持仓天数": exit_idx - entry_idx,
        "出场原因": exit_reason,
        "毛收益": gross,
        "净收益": net,
    }


def process_stock(file_path):
    stock_name = os.path.splitext(os.path.basename(file_path))[0]
    try:
        df = pd.read_excel(file_path, usecols=["date", "close"])
    except Exception:
        return None

    df = df.dropna().sort_values("date").reset_index(drop=True)
    if len(df) < MA_PERIOD + SLOPE_WINDOW + REV_WINDOW + 5:
        return None

    close = df["close"].astype(float).values
    dates = df["date"].astype(str).values
    n = len(close)

    ma = pd.Series(close).rolling(MA_PERIOD).mean().values
    slope = np.full(n, np.nan)
    slope[SLOPE_WINDOW:] = ma[SLOPE_WINDOW:] - ma[:-SLOPE_WINDOW]

    trades = []
    in_zone = False

    for t in range(MA_PERIOD + SLOPE_WINDOW, n - ENTRY_OFFSET - 1):
        if np.isnan(ma[t]) or np.isnan(slope[t]):
            continue
        # 下跌趋势
        if slope[t] >= 0:
            in_zone = False
            continue
        # 触线判定
        if close[t] <= ma[t] * (1 + BUFFER):
            if not in_zone:
                in_zone = True
                trade = simulate_trade(close, ma, dates, t)
                if trade:
                    trade["股票"] = stock_name
                    trades.append(trade)
        else:
            in_zone = False

    if not trades:
        return None

    tdf = pd.DataFrame(trades)
    summary = {
        "股票": stock_name,
        "交易数": len(tdf),
        "胜率": (tdf["净收益"] > 0).mean(),
        "平均净收益": tdf["净收益"].mean(),
        "中位净收益": tdf["净收益"].median(),
        "平均持仓天数": tdf["持仓天数"].mean(),
        "止盈比例": (tdf["出场原因"] == "止盈").mean(),
        "止损比例": (tdf["出场原因"] == "止损").mean(),
        "时间出场比例": (tdf["出场原因"] == "时间").mean(),
    }
    return {"summary": summary, "trades": tdf}


def main():
    files = glob.glob(os.path.join(DATA_DIR, "*.xlsx"))
    print(f"共找到 {len(files)} 个文件")

    stock_summaries = []
    all_trades = []

    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(process_stock, f): f for f in files}
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                r = fut.result()
            except Exception as e:
                print(f"处理失败: {e}")
                continue
            if r:
                stock_summaries.append(r["summary"])
                all_trades.append(r["trades"])
            if i % 200 == 0:
                print(f"已处理 {i}/{len(files)}")

    if not all_trades:
        print("没有产生任何交易，请检查参数")
        return

    sdf = pd.DataFrame(stock_summaries)
    tdf = pd.concat(all_trades, ignore_index=True)

    # 排除指数
    mask_s = ~sdf["股票"].str.startswith(("sh000", "sz399"))
    mask_t = ~tdf["股票"].str.startswith(("sh000", "sz399"))
    sdf = sdf[mask_s].reset_index(drop=True)
    tdf = tdf[mask_t].reset_index(drop=True)

    # 全市场汇总
    overall = {
        "总交易数": len(tdf),
        "有效股票数": sdf["股票"].nunique(),
        "平均每股票交易数": sdf["交易数"].mean(),
        "整体胜率": (tdf["净收益"] > 0).mean(),
        "平均净收益": tdf["净收益"].mean(),
        "中位净收益": tdf["净收益"].median(),
        "平均毛收益": tdf["毛收益"].mean(),
        "平均持仓天数": tdf["持仓天数"].mean(),
        "止盈比例": (tdf["出场原因"] == "止盈").mean(),
        "止损比例": (tdf["出场原因"] == "止损").mean(),
        "时间出场比例": (tdf["出场原因"] == "时间").mean(),
    }

    print("\n========== 全市场汇总（全部股票） ==========")
    for k, v in overall.items():
        if isinstance(v, float):
            print(f"{k}: {v:.4f}")
        else:
            print(f"{k}: {v}")

    # 过滤汇总：每只股票交易数 >= MIN_TRADES
    sdf_valid = sdf[sdf["交易数"] >= MIN_TRADES]
    tdf_valid = tdf[tdf["股票"].isin(sdf_valid["股票"])]

    if len(tdf_valid) > 0:
        filtered = {
            "股票数(交易数>=" + str(MIN_TRADES) + ")": len(sdf_valid),
            "总交易数": len(tdf_valid),
            "整体胜率": (tdf_valid["净收益"] > 0).mean(),
            "平均净收益": tdf_valid["净收益"].mean(),
            "中位净收益": tdf_valid["净收益"].median(),
            "止盈比例": (tdf_valid["出场原因"] == "止盈").mean(),
            "止损比例": (tdf_valid["出场原因"] == "止损").mean(),
            "时间出场比例": (tdf_valid["出场原因"] == "时间").mean(),
        }
        print(f"\n========== 过滤后汇总（交易数>={MIN_TRADES}） ==========")
        for k, v in filtered.items():
            if isinstance(v, float):
                print(f"{k}: {v:.4f}")
            else:
                print(f"{k}: {v}")

    # 保存
    sdf.to_excel(OUTPUT_STOCK, index=False)
    tdf.to_excel(OUTPUT_TRADES, index=False)
    pd.DataFrame([overall]).to_excel(OUTPUT_SUMMARY, index=False)

    print(f"\n已输出：{OUTPUT_TRADES}")
    print(f"已输出：{OUTPUT_STOCK}")
    print(f"已输出：{OUTPUT_SUMMARY}")


if __name__ == "__main__":
    main()