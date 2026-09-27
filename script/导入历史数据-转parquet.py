# script/导入历史数据-转parquet.py
# -*- coding: utf-8 -*-
"""一次性：把 baostock 历史 CSV（按日分片）转换为平台 24 列 Parquet 并刷新断点表。

用法：python script/导入历史数据-转parquet.py
输出目录取 config/config.yaml 的 data_dir；转换逻辑在 app/services/csv_import.py。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 仓库根

from app.config import load_config                      # noqa: E402
from app.services import csv_import                      # noqa: E402
from app.services.task_service import refresh_freshness  # noqa: E402

DAILY_DIR = r"E:\资料\证券\A股日K日分组"
FACTOR_DIR = r"E:\资料\证券\复权因子每日"


def main() -> None:
    cfg = load_config()
    print(f"日K目录 : {DAILY_DIR}")
    print(f"因子目录: {FACTOR_DIR}")
    print(f"输出目录: {cfg.data_dir}")
    stats = csv_import.convert(DAILY_DIR, FACTOR_DIR, str(cfg.data_dir))
    print(f"转换完成: 股票 {stats['stocks']} 只, 总行数 {stats['rows']:,}, "
          f"日K分片文件 {stats['daily_files']} 个")
    refresh_freshness(cfg.data_dir)
    print("断点表 stock_freshness 已刷新，后续增量抓取将按每股最新日期续传。")


if __name__ == "__main__":
    main()
