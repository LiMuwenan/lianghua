# -*- coding: utf-8 -*-
"""
baostock 增量数据更新工具（每天跑一次）

流程：
  1. 扫描「每日」目录，找出缺失的日期（不止最后一天，中间有缺口也能补）
  2. 抓取缺失日期数据 → 写入「每日/{date}.csv」
  3. 新数据按 code 分组 → 追加到「合并/{code}.csv」

使用前提：
  * 已用主脚本做过一次全量抓取 + 合并（合并目录里是 {code}.csv）
"""

import os
import time
import datetime
import logging
from pathlib import Path

import pandas as pd
import baostock as bs


# ========================= 配置区 =========================
START_DATE = '2026-09-21'
END_DATE   = datetime.date.today().strftime('%Y-%m-%d')

DAILY_DIR  = r'每日'
MERGED_DIR = r'合并'
LOG_FILE   = 'incremental.log'

FIELDS = [
    'date', 'code', 'open', 'high', 'low', 'close', 'preclose',
    'volume', 'amount', 'adjustflag', 'turn', 'tradestatus',
    'pctChg', 'peTTM', 'pbMRQ', 'psTTM', 'pcfNcfTTM', 'isST'
]

# ---- 追加时使用的 dtype（与主脚本保持一致）----
_FLOAT_COLS = ['open', 'high', 'low', 'close', 'preclose', 'volume', 'amount',
               'turn', 'pctChg', 'peTTM', 'pbMRQ', 'psTTM', 'pcfNcfTTM']
_INT_COLS   = ['adjustflag', 'tradestatus', 'isST']
DTYPES = {c: 'float32' for c in _FLOAT_COLS}
DTYPES.update({c: 'int32' for c in _INT_COLS})
# ==========================================================


# ------------------------- 日志 -------------------------
def init_logger():
    logger = logging.getLogger('bs_incr')
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter('[%(asctime)s] %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S')

    fh = logging.FileHandler(LOG_FILE, encoding='utf-8')
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger.info


log = init_logger()


# ------------------------- 工具 -------------------------
def ensure_dirs():
    os.makedirs(DAILY_DIR, exist_ok=True)
    os.makedirs(MERGED_DIR, exist_ok=True)


def date_range(start, end):
    d1 = datetime.datetime.strptime(start, '%Y-%m-%d').date()
    d2 = datetime.datetime.strptime(end,   '%Y-%m-%d').date()
    while d1 <= d2:
        yield d1.strftime('%Y-%m-%d')
        d1 += datetime.timedelta(days=1)


# ------------------------- 抓取 -------------------------
def fetch_one_day(date_str):
    rs = bs.query_daily_history_k_AStock(date=date_str)
    if rs.error_code != '0':
        raise RuntimeError(f'error_code={rs.error_code}, error_msg={rs.error_msg}')

    rows = []
    while rs.next():
        rows.append(rs.get_row_data())

    fields = getattr(rs, 'fields', None) or FIELDS
    return pd.DataFrame(rows, columns=fields)


def fetch_one_day_with_retry(date_str, max_retry=3, wait=2):
    last_err = None
    for attempt in range(1, max_retry + 1):
        try:
            return fetch_one_day(date_str)
        except Exception as e:
            last_err = e
            log(f'    {date_str} 第 {attempt}/{max_retry} 次抓取异常: {e}')
            if attempt < max_retry:
                time.sleep(wait)
    log(f'    {date_str} 重试全部失败: {last_err}')
    return None


# ------------------------- 缺失日期检测 -------------------------
def compute_missing_dates():
    """返回 [START_DATE, END_DATE] 区间内「每日」目录中不存在的日期列表"""
    existing = {f.stem for f in Path(DAILY_DIR).glob('*.csv')}
    all_dates = list(date_range(START_DATE, END_DATE))
    missing = [d for d in all_dates if d not in existing]

    if not existing:
        log(f'未发现任何本地日文件，将全量抓取 {len(missing)} 天')
    else:
        log(f'本地已有 {len(existing)} 天数据，缺失 {len(missing)} 天')
        if missing:
            log(f'  缺失区间：{missing[0]} ~ {missing[-1]}')
    return missing


def fetch_missing_dates(dates):
    """抓取指定日期并写入「每日」，返回新抓到的非空 DataFrame 列表"""
    if not dates:
        return []

    new_dfs = []
    total = len(dates)
    for i, d in enumerate(dates, 1):
        path = os.path.join(DAILY_DIR, f'{d}.csv')

        # 二次防御：万一并发/重跑，文件已存在就跳过
        if os.path.exists(path):
            log(f'[{i}/{total}] {d} 本地已存在，跳过')
            continue

        log(f'[{i}/{total}] 抓取 {d} ...')
        t0 = time.time()
        df = fetch_one_day_with_retry(d)

        if df is None:
            log(f'  {d} 抓取失败，跳过（下次运行会重试）')
            continue

        if df.empty:
            # 节假日/停市：写一个只有表头的 csv 作为"已处理"标记
            pd.DataFrame(columns=FIELDS).to_csv(
                path, index=False, encoding='utf-8-sig')
            log(f'  {d} 无数据（可能节假日），耗时 {time.time()-t0:.2f}s')
        else:
            df.to_csv(path, index=False, encoding='utf-8-sig')
            new_dfs.append(df)
            log(f'  {d} 完成，{len(df)} 条，耗时 {time.time()-t0:.2f}s')

    return new_dfs


# ------------------------- 追加到每只股票文件（CSV） -------------------------
def _append_one_stock(code, grp):
    """
    把某只股票的新行追加到它的 {code}.csv。
    关键点：
      * 首次写（文件不存在）用 utf-8-sig（带 BOM，方便 Excel 直接打开）
      * 追加时用 utf-8（不带 BOM），否则每追加一次都会在文件中间插入 BOM
      * 追加必须 header=False，且列顺序与首次写出完全一致
    """
    path = os.path.join(MERGED_DIR, f'{code}.csv')

    if os.path.exists(path):
        grp.to_csv(path, mode='a', header=False, index=False,
                   encoding='utf-8')
    else:
        grp.to_csv(path, index=False, encoding='utf-8-sig')


def append_to_stock_files(new_dfs):
    """把新数据按 code 分组，逐只追加"""
    if not new_dfs:
        log('无新增数据可追加')
        return

    log('开始按 code 分组 ...')
    combined = pd.concat(new_dfs, ignore_index=True)

    # ---------- 关键修复 ----------
    # baostock 返回的字段都是字符串，空值/异常值会是 ''
    # 先统一转成数值（无法转的 → NaN），再 astype 成目标 dtype
    for c, t in DTYPES.items():
        if c not in combined.columns:
            continue
        try:
            combined[c] = pd.to_numeric(combined[c], errors='coerce').astype(t)
        except Exception as e:
            log(f'  列 {c} 转 {t} 失败（已跳过该列）: {e}')

    # date/code 保持字符串
    for c in ('date', 'code'):
        if c in combined.columns:
            combined[c] = combined[c].astype(str)
    # -----------------------------

    # 保证列顺序与 FIELDS 一致（防止 baostock 字段顺序未来变化）
    combined = combined[FIELDS]

    combined = combined.sort_values(['code', 'date'], kind='stable')

    total_codes = int(combined['code'].nunique())
    log(f'需更新 {total_codes} 只股票的文件，开始追加 ...')

    t0 = time.time()
    written = 0
    for code, grp in combined.groupby('code', sort=False, observed=True):
        written += 1
        try:
            _append_one_stock(code, grp)
        except Exception as e:
            log(f'  追加 {code} 失败: {e}')

        if written % 200 == 0 or written == total_codes:
            elapsed = time.time() - t0
            eta = elapsed / written * (total_codes - written)
            log(f'  追加进度 {written}/{total_codes}（{code}），'
                f'已用 {elapsed:.0f}s，预计还需 {eta:.0f}s')

    log(f'追加完成，总耗时 {time.time()-t0:.1f}s')


# ------------------------- 主流程 -------------------------
def main():
    ensure_dirs()
    log('#' * 60)
    log(f'增量更新启动  日期区间 {START_DATE} ~ {END_DATE}')

    dates = compute_missing_dates()
    if not dates:
        log('本地数据已是最新，无需抓取')
        log('#' * 60)
        return

    lg = bs.login()
    log(f'baostock 登录: error_code={lg.error_code}, '
        f'error_msg={lg.error_msg}')
    if lg.error_code != '0':
        log('baostock 登录失败，终止本次任务')
        return

    try:
        new_dfs = fetch_missing_dates(dates)
    finally:
        bs.logout()
        log('baostock 已登出')

    append_to_stock_files(new_dfs)

    log('任务结束')
    log('#' * 60)


if __name__ == '__main__':
    main()