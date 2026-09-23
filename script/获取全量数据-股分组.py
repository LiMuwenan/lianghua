import baostock as bs
import pandas as pd
import os
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 配置区 =================
START_DATE = "2020-01-01"  # 新建文件时的起始日期（已有文件自动增量更新）
END_DATE = datetime.now().strftime("%Y-%m-%d")  # 数据结束日期（默认今天）
OUTPUT_DIR = "A股日K数据"  # Excel 文件保存目录
MAX_WORKERS = 1  # 并行线程数（建议 3~8，过高可能被限流）

# 完全跳过已存在文件（True=不做任何更新；False=增量更新，推荐）
SKIP_EXISTING = False

# ---------- 证券列表配置 ----------
STOCK_LIST_FILE = "A股全量证券代码.xlsx"  # 证券列表保存路径
REFRESH_STOCK_LIST = False  # True=重新获取证券列表并保存；False=优先读取本地文件
# ---------------------------------

# 大盘指数列表（可根据需要增减）
INDICES = [
    'sh.000001',  # 上证指数
    'sz.399001',  # 深证成指
    'sz.399006',  # 创业板指
    'sh.000300',  # 沪深300
    'sh.000905',  # 中证500
    'sh.000016',  # 上证50
    'sz.399005',  # 中小板指
]
INDICES = list(set(INDICES))  # 去重
# ==========================================

def format_duration(seconds):
    """将秒数格式化为易读的字符串"""
    if seconds < 60:
        return f"{seconds:.2f} 秒"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{int(m)} 分 {s:.2f} 秒"
    h, m = divmod(m, 60)
    return f"{int(h)} 时 {int(m)} 分 {s:.2f} 秒"

def login_baostock():
    """登录 Baostock，并返回登录结果"""
    lg = bs.login()
    if lg.error_code != '0':
        raise ConnectionError(f"Baostock 登录失败: {lg.error_msg}")
    print("Baostock 登录成功")
    return lg

def get_stock_list():
    """
    获取全量 A 股代码列表。
    - 若 REFRESH_STOCK_LIST=False 且本地文件存在，则直接从本地读取；
    - 否则在线获取并保存为 Excel。
    """
    # ---------- 优先读取本地文件 ----------
    if not REFRESH_STOCK_LIST and os.path.exists(STOCK_LIST_FILE):
        try:
            df = pd.read_excel(STOCK_LIST_FILE, dtype=str)
            if 'code' in df.columns:
                stock_codes = df['code'].dropna().tolist()
            else:
                stock_codes = df.iloc[:, 0].dropna().tolist()
            stock_codes = [c for c in stock_codes
                           if c.startswith(('sh.6', 'sz.0', 'sz.3', 'sh.0'))]
            if stock_codes:
                print(f"从本地文件读取到 {len(stock_codes)} 只 A 股股票：{os.path.abspath(STOCK_LIST_FILE)}")
                return stock_codes
            else:
                print("本地证券列表为空，将重新在线获取...")
        except Exception as e:
            print(f"读取本地证券列表失败：{e}，将重新在线获取...")
    # ---------------------------------------

    # ---------- 在线获取 ----------
    for i in range(7):
        day = (datetime.strptime(END_DATE, "%Y-%m-%d") - timedelta(days=i)).strftime("%Y-%m-%d")
        rs = bs.query_all_stock(day=day)
        if rs.error_code != '0':
            raise RuntimeError(f"获取股票列表失败: {rs.error_msg}")

        stock_codes = []
        while rs.next():
            code = rs.get_row_data()[0]
            if code.startswith(('sh.6', 'sz.0', 'sz.3', 'sh.0')):
                stock_codes.append(code)

        if stock_codes:
            print(f"使用日期 {day} 获取到 {len(stock_codes)} 只 A 股股票")
            df = pd.DataFrame({'code': stock_codes})
            df.to_excel(STOCK_LIST_FILE, index=False, engine='openpyxl')
            print(f"证券列表已保存至：{os.path.abspath(STOCK_LIST_FILE)}")
            return stock_codes

    raise RuntimeError("最近 7 天内未获取到股票列表，请检查网络或日期。")

def normalize_df(df):
    """统一 date 列为字符串 YYYY-MM-DD，并按日期去重排序"""
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df = df.dropna(subset=['date'])
    df['date'] = df['date'].dt.strftime('%Y-%m-%d')
    df = df.drop_duplicates(subset='date', keep='last')
    df = df.sort_values('date').reset_index(drop=True)
    return df

def load_existing(file_path):
    """
    读取本地已存在的 Excel 文件。
    返回 (last_date_str, df)，文件不存在或损坏时返回 (None, None)。
    """
    if not os.path.exists(file_path):
        return None, None
    try:
        df = pd.read_excel(file_path, engine='openpyxl')
        if df.empty or 'date' not in df.columns:
            return None, None
        df = normalize_df(df)
        if df.empty:
            return None, None
        return df['date'].max(), df
    except Exception as e:
        print(f"读取 {file_path} 失败：{e}")
        return None, None

def fetch_one_stock(code):
    """
    获取单只股票或指数的日 K 数据（增量更新）。
    返回 (代码, 状态, 行数/信息, 耗时秒数)
    """
    t_start = time.time()

    try:
        safe_name = code.replace('.', '')
        file_path = os.path.join(OUTPUT_DIR, f"{safe_name}.xlsx")

        # ---------- 是否完全跳过 ----------
        if SKIP_EXISTING and os.path.exists(file_path):
            return (code, "已存在", 0, time.time() - t_start)
        # ---------------------------------

        # ---------- 判断本地文件状态，决定起始日期 ----------
        last_date, existing_df = load_existing(file_path)

        if last_date is None:
            # 文件不存在 / 损坏 / 为空 → 全量抓取
            start_date = START_DATE
            existing_df = None
        elif last_date >= END_DATE:
            # 已是最新
            return (code, "已最新", last_date, time.time() - t_start)
        else:
            # 增量：从最后一天的下一天开始
            start_date = (datetime.strptime(last_date, '%Y-%m-%d')
                          + timedelta(days=1)).strftime('%Y-%m-%d')
        # -------------------------------------------------

        is_index = code in INDICES
        if is_index:
            fields = "date,code,open,high,low,close,preclose,volume,amount,pctChg"
        else:
            fields = "date,code,open,high,low,close,volume,amount,turn,pctChg"

        rs = bs.query_history_k_data_plus(
            code,
            fields,
            start_date=start_date,
            end_date=END_DATE,
            frequency="d",
            adjustflag="3"  # 3=不复权；2=前复权；1=后复权
        )

        if rs.error_code != '0':
            return (code, "查询失败", rs.error_msg, time.time() - t_start)

        data_list = []
        while rs.next():
            data_list.append(rs.get_row_data())

        if not data_list:
            # 文件存在但没抓到新数据（如近期停牌、周末）
            if existing_df is not None:
                return (code, "无新数据", last_date, time.time() - t_start)
            return (code, "无数据", 0, time.time() - t_start)

        df_new = pd.DataFrame(data_list, columns=rs.fields)

        # ---------- 合并旧数据 + 新数据 ----------
        if existing_df is not None and not existing_df.empty:
            df_all = pd.concat([existing_df, df_new], ignore_index=True)
            df_all = normalize_df(df_all)
        else:
            df_all = normalize_df(df_new)
        # -----------------------------------------

        df_all.to_excel(file_path, index=False, engine='openpyxl')

        return (code, "成功", len(df_new), time.time() - t_start)

    except Exception as e:
        return (code, "异常", str(e), time.time() - t_start)

def main():
    program_start = time.time()
    program_start_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"程序启动时间：{program_start_str}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    login_baostock()

    try:
        stock_list = get_stock_list()
        all_codes = list(set(stock_list + INDICES))
        print(f"总计需要下载 {len(all_codes)} 个标的（股票 + 指数）")

        print(f"开始下载数据，日期范围：{START_DATE} ~ {END_DATE}")
        print(f"输出目录：{os.path.abspath(OUTPUT_DIR)}")
        print(f"并行线程数：{MAX_WORKERS}")
        print(f"完全跳过已存在文件：{'是' if SKIP_EXISTING else '否（自动增量更新）'}\n")

        success_count = 0      # 本次有新增数据
        skip_count = 0         # 已是最新
        no_new_count = 0       # 文件存在但无新数据
        fail_list = []
        total_fetch_time = 0.0
        fetched_count = 0

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_code = {
                executor.submit(fetch_one_stock, code): code
                for code in all_codes
            }

            for i, future in enumerate(as_completed(future_to_code), 1):
                code, status, result, elapsed = future.result()

                if status == "成功":
                    success_count += 1
                    total_fetch_time += elapsed
                    fetched_count += 1
                    print(f"[{i}/{len(all_codes)}] {code} 新增 {result} 行，耗时 {format_duration(elapsed)}")
                elif status == "已最新":
                    skip_count += 1
                    print(f"[{i}/{len(all_codes)}] 已最新 {skip_count} 个...")
                elif status == "无新数据":
                    no_new_count += 1
                    print(f"[{i}/{len(all_codes)}] 无新数据 {no_new_count} 个（最新日期 {result}）...")
                elif status == "已存在":
                    skip_count += 1
                    print(f"[{i}/{len(all_codes)}] 已跳过 {skip_count} 个...")
                else:
                    fail_list.append((code, status, result))
                    fetched_count += 1
                    print(f"[{i}/{len(all_codes)}] {code} {status}: {result}，耗时 {format_duration(elapsed)}")

                if i % 100 == 0:
                    time.sleep(2)

        program_end = time.time()
        total_elapsed = program_end - program_start

        print("\n" + "=" * 50)
        print(f"下载完成！新增：{success_count} 个，已最新：{skip_count} 个，"
              f"无新数据：{no_new_count} 个，失败：{len(fail_list)} 个")
        if fetched_count > 0:
            avg_time = total_fetch_time / fetched_count
            print(f"实际发起请求 {fetched_count} 个，平均单个耗时：{format_duration(avg_time)}")
        if fail_list:
            print("\n失败列表：")
            for code, status, msg in fail_list:
                print(f"  {code} - {status}: {msg}")

        print("\n" + "=" * 50)
        print(f"程序结束时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"总耗时：{format_duration(total_elapsed)}")

    finally:
        bs.logout()
        print("\nBaostock 连接已关闭")

if __name__ == "__main__":
    main()