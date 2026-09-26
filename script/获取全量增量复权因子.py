# -*- coding: utf-8 -*-
"""
baostock 复权因子历史数据获取 & 合并工具
"""

import os
import time
import datetime
import logging
from pathlib import Path

import pandas as pd
import baostock as bs


# ========================= 配置区 =========================
START_DATE = '2005-01-01'
END_DATE = datetime.date.today().strftime('%Y-%m-%d')

DAILY_DIR = r'F:\量化数据\复权因子每日'
MERGED_DIR = r'F:\量化数据\复权因子合并'
MERGED_FILE = '复权因子.csv'      # ★ 合并后的总文件（单文件）
MERGED_STATE_FILE = '.merged_files.txt'     # ★ 记录已合并过的每日文件名
LOG_FILE = 'adjust_progress.log'

FETCH_DAILY = True          # ★ 开关：True=抓取每日；False=跳过抓取直接合并
USE_TRADE_CALENDAR = True   # True=只遍历交易日；False=按自然日遍历（原框架写法）

# query_daily_adjust_factor 的返回字段
FIELDS = ['code', 'dividOperateDate',
          'foreAdjustFactor', 'backAdjustFactor', 'adjustFactor']

# 复权因子数据量很小（全市场也就几万条），dtype 只做轻度优化
DTYPES = {
    'code': 'category',
    'dividOperateDate': 'category',
    'foreAdjustFactor': 'float64',
    'backAdjustFactor': 'float64',
    'adjustFactor': 'float64',
}
# ==========================================================


# ------------------------- 日志 -------------------------
def init_logger():
    logger = logging.getLogger('bs_adjust')
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
    d2 = datetime.datetime.strptime(end, '%Y-%m-%d').date()
    while d1 <= d2:
        yield d1.strftime('%Y-%m-%d')
        d1 += datetime.timedelta(days=1)


def get_trade_dates(start, end):
    """用 baostock 交易日历过滤，返回日期字符串列表。"""
    rs = bs.query_trade_dates(start_date=start, end_date=end)
    if rs.error_code != '0':
        raise RuntimeError(f'query_trade_dates 失败: {rs.error_msg}')
    dates = []
    while rs.next():
        row = rs.get_row_data()      # [calendar_date, is_trading_day]
        if row[1] == '1':
            dates.append(row[0])
    return dates


# ------------------------- 抓取 -------------------------
def fetch_one_day(date_str):
    rs = bs.query_daily_adjust_factor(date=date_str)
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


def fetch_daily_all():
    log('================ 复权因子每日数据抓取开始 ================')

    if USE_TRADE_CALENDAR:
        try:
            dates = get_trade_dates(START_DATE, END_DATE)
        except Exception as e:
            log(f'获取交易日历失败，回退到自然日遍历: {e}')
            dates = list(date_range(START_DATE, END_DATE))
    else:
        dates = list(date_range(START_DATE, END_DATE))

    total = len(dates)
    log(f'日期范围 {START_DATE} ~ {END_DATE}，共 {total} 天')

    for i, d in enumerate(dates, 1):
        path = os.path.join(DAILY_DIR, f'{d}.csv')

        if os.path.exists(path):
            log(f'[{i}/{total}] {d} 本地已存在，跳过')
            continue

        log(f'[{i}/{total}] 开始抓取 {d} ...')
        t0 = time.time()
        df = fetch_one_day_with_retry(d)

        if df is None:
            log(f'  {d} 抓取失败，跳过（下次运行会重试）')
            continue

        if df.empty:
            # 该日无除权除息事件：写空表头文件作为"已处理"标记
            pd.DataFrame(columns=FIELDS).to_csv(
                path, index=False, encoding='utf-8-sig')
            log(f'  {d} 无数据，耗时 {time.time()-t0:.2f}s')
        else:
            df.to_csv(path, index=False, encoding='utf-8-sig')
            log(f'  {d} 完成，{len(df)} 条，耗时 {time.time()-t0:.2f}s')

    log('================ 复权因子每日数据抓取结束 ================')


# ------------------------- 合并 -------------------------
def load_merged_set():
    """读取已合并的每日文件名集合。"""
    if not os.path.exists(MERGED_STATE_FILE):
        return set()
    try:
        with open(MERGED_STATE_FILE, 'r', encoding='utf-8') as f:
            return {line.strip() for line in f if line.strip()}
    except Exception as e:
        log(f'读取合并记录失败: {e}，按空记录处理')
        return set()


def save_merged_set(merged_set):
    """保存已合并的每日文件名集合。"""
    try:
        with open(MERGED_STATE_FILE, 'w', encoding='utf-8') as f:
            for name in sorted(merged_set):
                f.write(name + '\n')
    except Exception as e:
        log(f'保存合并记录失败: {e}')


def merge_all():
    """
    将所有每日文件合并到单个总文件中：
      - 已经合并过的每日文件不再重复处理；
      - 新数据以追加模式写入总文件；
      - 若总文件不存在或为空，则重置记录，从头合并。
    """
    log('================ 合并流程开始 ================')

    daily_files = sorted(Path(DAILY_DIR).glob('*.csv'))
    if not daily_files:
        log(f'"{DAILY_DIR}" 目录下没有 csv 文件，合并结束')
        return

    merged_path = os.path.join(MERGED_DIR, MERGED_FILE)
    file_exists = os.path.exists(merged_path) and os.path.getsize(merged_path) > 0

    # 读取已合并记录；若总文件被删除/清空，记录同步重置
    merged_set = load_merged_set()
    if not file_exists and merged_set:
        log('总文件不存在或为空，重置已合并记录')
        merged_set = set()

    # 过滤出尚未合并的每日文件
    pending = [f for f in daily_files if f.name not in merged_set]
    if not pending:
        log('没有需要合并的新文件，合并结束')
        return

    log(f'每日文件共 {len(daily_files)} 个，其中 {len(pending)} 个待合并')

    # ---------- 阶段 1：读入待合并文件 ----------
    t_all = time.time()
    all_dfs = []
    newly_merged = []

    for i, f in enumerate(pending, 1):
        try:
            df = pd.read_csv(f, dtype=DTYPES)
        except Exception as e:
            log(f'  读取 {f.name} 失败: {e}')
            continue

        if df.empty:
            # 空文件也记入已合并，避免每次都重新检查
            newly_merged.append(f.name)
            continue

        all_dfs.append(df)
        newly_merged.append(f.name)

        if i % 500 == 0 or i == len(pending):
            log(f'  读入进度 {i}/{len(pending)}（最新：{f.stem}），'
                f'已用 {time.time()-t_all:.1f}s')

    # ---------- 阶段 2：追加写入总文件 ----------
    if all_dfs:
        t0 = time.time()
        new_data = pd.concat(all_dfs, ignore_index=True)
        del all_dfs

        if file_exists:
            # 追加：不再写表头；用 utf-8 避免重复写入 BOM
            new_data.to_csv(merged_path, mode='a', index=False,
                            header=False, encoding='utf-8')
            log(f'追加 {len(new_data):,} 行到 {merged_path}，'
                f'耗时 {time.time()-t0:.1f}s')
        else:
            # 首次写入：带表头 + BOM（方便 Excel 直接打开）
            new_data.to_csv(merged_path, mode='w', index=False,
                            header=True, encoding='utf-8-sig')
            log(f'创建总文件 {merged_path}，写入 {len(new_data):,} 行，'
                f'耗时 {time.time()-t0:.1f}s')
    else:
        log('本次没有非空数据需要写入')

    # ---------- 阶段 3：更新合并记录 ----------
    merged_set |= set(newly_merged)
    save_merged_set(merged_set)
    log(f'已合并记录更新，累计 {len(merged_set)} 个文件')

    log(f'合并流程结束，总耗时 {time.time()-t_all:.1f}s')
    log('================ 合并流程结束 ================')

# ------------------------- 主流程 -------------------------
def main():
    ensure_dirs()
    log('#' * 60)
    log(f'任务启动  FETCH_DAILY={FETCH_DAILY}  '
        f'日期区间 {START_DATE} ~ {END_DATE}')

    if FETCH_DAILY:
        lg = bs.login()
        log(f'baostock 登录: error_code={lg.error_code}, '
            f'error_msg={lg.error_msg}')

        if lg.error_code != '0':
            log('baostock 登录失败，终止本次任务')
            return

        try:
            fetch_daily_all()
        finally:
            bs.logout()
            log('baostock 已登出')
    else:
        log('FETCH_DAILY=False，跳过每日数据抓取，直接进入合并')

    merge_all()
    log('任务结束')
    log('#' * 60)


if __name__ == '__main__':
    main()