# 历史行情数据导入 Parquet 设计方案

> 版本：v0.1（待评审）
> 状态：待评审
> 定位：一次性将用户已备好的 baostock 历史 CSV 转换为平台 24 列 Parquet（按股票分文件），并修正现网实时摄取的复权因子口径，保证后续增量续传与历史序列一致。

---

## 1. 背景与目标

平台「获取数据」已跑通（逐交易日横扫全市场落 24 列 Parquet），但从 2005-01-01 起抓需极长时间。用户已通过既有 `script/` 脚本备好了 baostock 历史数据：

- 股票日K（不复权）：按日分片 CSV；
- 复权因子：按日分片 CSV。

**目标**：写一个一次性转换脚本，把上述 CSV 转为平台标准 24 列 Parquet（每股一文件，写入 `data_dir`），刷新 `stock_freshness` 断点表，使后续增量抓取无缝续传；同时修正现网 `ingest.py` 复权因子取值口径，避免断点处复权序列跳变。

**非目标**：不做平台「导入」功能按钮、不改动 `script/` 既有脚本、不复造数据层。

---

## 2. 输入数据（已实地核实）

| 项 | 值 |
|---|---|
| 日K目录 | `E:\资料\证券\A股日K日分组` |
| 日K文件 | 7938 个 `YYYY-MM-DD.csv`，2005-01-01 ~ 2026-09-25 |
| 日K列 | `date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST`（18 列），code 带 `sh.`/`sz.` 前缀 |
| 日K编码 | UTF-8 BOM；非交易日为**仅表头的空文件**（已处理标记，需跳过） |
| 因子目录 | `E:\资料\证券\复权因子每日` |
| 因子文件 | 5278 个 `YYYY-MM-DD.csv`，2005-01-04 ~ 2026-09-23 |
| 因子列 | `code,dividOperateDate,foreAdjustFactor,backAdjustFactor,adjustFactor`，code 带前缀 |
| 输出目录 | `config/config.yaml` 顶层键 `data_dir`（当前 `E:/资料/证券/lianghua/market`），平台摄取/扫描/断点均对齐该位置 |

---

## 3. 复权口径（标准计算方式，用户已确认）

**baostock 官方算法**：

```
前复权价 = 原始价 × foreAdjustFactor（前复权因子）
后复权价 = 原始价 × backAdjustFactor（后复权因子）
```

- **后复权因子**：自上市起累计倍数（每次除权除息：新因子 = 旧因子 × 本次 adjustFactor），抹平除权跳空；
- **前复权因子**：以最新除权日为基准归一（≈1.0），越早历史因子越小，把历史价格折算到当前水平；
- **关键**：因子仅在除权除息日（dividOperateDate）变化，其余交易日**沿用最近一次值** → 逐股前向填充（每交易日取「最近一次事件日 ≤ 当日」的因子），无除权历史 = 1.0；
- `volume/amount` 不乘因子（复权只调价格，不调量额）。

**现网问题**：`app/services/ingest.py` 在非除权日把因子默认计 1.0，导致复权价序列在非除权日等于原始价、序列断裂，属非标准口径，本次一并修正。

**验收锚点**：挑一只长期除权股票（如 600000），用 baostock `query_history_k_data_plus(..., qfq)` 与 `hfq` 拉官方复权价，与「原始 CSV 价 × 前向填充因子」逐日比对，须在浮点误差内完全一致。

---

## 4. 转换脚本设计（新增 `script/导入历史数据-转parquet.py` + 同名 `.manifest.yaml`）

一次性脚本，`python script/导入历史数据-转parquet.py` 手动运行，不进平台任务体系。顶部常量对齐既有脚本风格：

```python
DAILY_DIR = r'E:\资料\证券\A股日K日分组'
FACTOR_DIR = r'E:\资料\证券\复权因子每日'
# OUT_DIR 从 app.config.load_config().data_dir 读取，与平台单点对齐
```

### 4.1 三阶段执行（全程逐股处理，不落地中间文件）

1. **因子预处理**：读全部因子文件（跳过空文件）→ concat（总量小，内存可忽略）→ 去 code 前缀 → 按 code 分组 → 每股得到按 `dividOperateDate` 升序的事件表 `[(date, fore, back), ...]`；
2. **日K读取**：按既有 `获取全量数据-日分组.py` 的 dtype 优化（float32/int32/category）逐文件读入 → 跳过空文件 → concat → 去 `adjustflag` 列（平台 24 列不含该列）。峰值内存 ~3GB，与既有合并脚本同档、在已验证过的 10GB 预算内；
3. **逐股写盘**：按 code 分组 →
   - 按日期升序排序、去重（keep=last）；
   - 前向填充因子（上述口径）→ 计算 qfq/hfq OHLC；
   - 复用 `app/services/parquet_store.write_stock` 写 24 列 `{code}.parquet`（列序、类型与现网完全一致）。

### 4.2 收尾：刷新断点表

复用 `app/services/task_service._refresh_freshness` 的扫描逻辑（或等价实现）：扫描输出目录全部 parquet，按股 upsert `stock_freshness`（latest_date / row_count）。此后平台增量抓取从每股最新日期续传、历史日期不再重抓。

### 4.3 处理边界

- code 规范化：`sh.600000` / `sz.000651` → 6 位裸代码（与摄取 `_row_to_stock_df` 一致）；
- 空文件（非交易日/无除权日）跳过；
- 因子事件日期不在该股交易日（停牌等）→ 前向填充自然顺延到下一交易日；
- 有因子事件但无日K的股票 / 反之 → 以日K为准，无因子历史计 1.0；
- 损坏文件：跳过并计入日志统计，不中断整体。

---

## 5. 现网摄取修正（`app/services/ingest.py`）

**改动点**（`run_fetch` 内因子取值逻辑）：

- 现：`fac = factors.get(code) or {"qfq_factor": 1.0, "hfq_factor": 1.0}`（非除权日 = 1.0）；
- 改：维护 `last_factor[code]` 状态 —— 启动时从该股既有 parquet 末行读取最近因子作为初始值（与转换出的历史无缝衔接；新股默认 1.0）；循环中除权日更新、非除权日沿用。

**注意**：
- `_row_to_stock_df` / `compute_adjusted_prices` 的乘法公式不变（本就是标准口径）；
- 现有 pytest（11 项）若有断言旧「非除权日=1.0」行为者，同步调整后保持全绿。

---

## 6. 执行步骤与验证（AGENTS.md 自测口径）

**运行**：停掉 8000 端口后端实例（避免 SQLite 并发写）→ `python script/导入历史数据-转parquet.py` → 重新启动后端。

**验证清单**：
1. 脚本输出：写入股票数、总行数、每股最新日期分布；与源 CSV 行数抽样核对；
2. 抽查 600000：前向填充连续、qfq/hfq 价 = 原始价 × 因子 算术正确；
3. **与 baostock 官方 qfq/hfq 数据逐日比对（验收锚点，见 §3）**；
4. 平台触发一次增量抓取（start_date=今天）：历史日期全部跳过、只补最新，断点处复权序列无跳变；
5. `pytest` 全绿。

---

## 7. 风险与注意事项

- **内存**：转换峰值 ~3GB，与既有全量合并脚本同档；运行时避免与其他重任务并发，符合项目内存红线约定；
- **SQLite 并发**：脚本直接写 `data/meta.db`，建议停后端运行；SQLAlchemy 默认 busy timeout 兜底；
- **因子归一基准**：前复权因子以最新除权日为基准归一（非最新交易日），此为 baostock 既有口径，与官方 qfq 查询结果一致即可，不额外做再归一；
- **git 规矩**：只提交新增 `.py` + `.manifest.yaml` + 本 spec；`config.yaml` 若含未提交改动，另行评估，不混入本次提交。

---

*（本文档随迭代补充；实施计划由 writing-plans 产出。）*
