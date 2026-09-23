import pandas as pd
import numpy as np

FILE = "结果_匹配基准.xlsx"
SHEET = "全部结果"

df = pd.read_excel(FILE, sheet_name=SHEET)

# 排除指数
df["股票"] = df["股票"].astype(str)
df = df[~df["股票"].str.startswith(("sh000", "sz399"))].copy()

prefixes = [
    "跌_5日", "涨_5日",
    "跌_20日", "涨_20日",
    "跌_60日", "涨_60日",
    "跌_180日", "涨_180日",
]

def summarize(prefix):
    cols = [
        "股票",
        f"{prefix}_样本",
        f"{prefix}_反转次数",
        f"{prefix}_反转率",
        f"{prefix}_原基准率",
        f"{prefix}_原超额",
        f"{prefix}_匹配基准率",
        f"{prefix}_匹配超额",
    ]
    d = df[cols].copy()
    d.columns = [
        "股票", "样本", "反转次数", "反转率",
        "原基准率", "原超额", "匹配基准率", "匹配超额"
    ]

    # 样本数 >= 30
    d = d[d["样本"] >= 30].copy()
    d = d.dropna(subset=["样本", "反转次数"])

    if len(d) == 0:
        return None

    total_n = d["样本"].sum()
    rev_rate = d["反转次数"].sum() / total_n

    def weighted_mean(col):
        x = d.dropna(subset=[col, "样本"])
        if len(x) == 0:
            return np.nan
        return (x[col] * x["样本"]).sum() / x["样本"].sum()

    orig_base_w = weighted_mean("原基准率")
    match_base_w = weighted_mean("匹配基准率")

    return {
        "方向": prefix.split("_")[0],
        "均线": prefix.split("_")[1],
        "有效股票数": len(d),
        "总样本": total_n,
        "平均样本": total_n / len(d),
        "反转率": rev_rate,
        "原基准率": orig_base_w,
        "原超额": rev_rate - orig_base_w,
        "匹配基准率": match_base_w,
        "匹配超额": rev_rate - match_base_w,
        "原超额>0占比": (d["原超额"] > 0).mean(),
        "匹配超额>0占比": (d["匹配超额"] > 0).mean(),
        "匹配基准非空股票数": d["匹配基准率"].notna().sum(),
    }

rows = []
for p in prefixes:
    r = summarize(p)
    if r:
        rows.append(r)

out = pd.DataFrame(rows)

# 格式化百分比列
pct_cols = ["反转率", "原基准率", "原超额", "匹配基准率", "匹配超额", "原超额>0占比", "匹配超额>0占比"]
for c in pct_cols:
    out[c] = out[c].apply(lambda x: f"{x:.2%}" if pd.notna(x) else "")

print(out.to_string(index=False))
out.to_excel("汇总对比_匹配基准.xlsx", index=False)
print("\n已输出：汇总对比_匹配基准.xlsx")