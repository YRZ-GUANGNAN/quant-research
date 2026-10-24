# 量化交易安全自动化重构设计（2026-10-24）

> **已被取代：** 本文保留为历史审计材料。实施时只以
> `docs/superpowers/specs/2026-10-24-quant-safe-automation-plan-b-design.md`
> 和 `docs/superpowers/plans/2026-10-24-quant-safe-automation-plan-b.md` 为准。

- 状态：设计已批准，待实施
- 基线提交：`5caa85ce66347d80137ac51cf754e92c2fb77272`
- 适用范围：行情刷新、完成交易日判定、目标快照、Longbridge 模拟盘执行、运行后检查、相关文档与测试
- 当前安全状态：`QuantDailyRun`、`QuantUSOpen` 均为 `Disabled`
- 设计选择：单一 Python 编排器 + Longbridge SDK-only 模拟盘交易

## 1. 背景与已核实事实

本设计只记录截至 2026-10-24 对基线提交的只读核查结论。旧审计报告和迭代日志是历史证据，不作为当前实现状态的唯一真相源。

已核实问题如下：

1. `scripts/daily_run.ps1` 没有调用 `scripts/pre_trade_gate.py` 或 `scripts/build_target_snapshot.py`。行情刷新失败只会累计错误，后续两个 `--execute` 仍可能运行。
2. 当前模拟盘校验使用 SDK `.env` 凭证，而余额、持仓、挂单和真正下单主要使用 Longbridge CLI OAuth 会话。两条通道不能证明“被校验账户就是实际下单账户”。
3. 当前目标快照以部署日作为调仓锚点，没有复用研究侧 `(i - warmup) % rebalance_days == 0` 的调仓相位。
4. `scripts/pre_trade_gate.py` 用北京时间日期近似美股交易日，并允许数据日期晚于预期日期；Yahoo 盘中未完成 bar 可能通过门禁。
5. `scripts/lb_order_map.py` 在快照缺失、损坏或过期时会现场重新计算目标，执行路径是 fail-open。
6. `scripts/fetch_baostock_full.py` 每 50 只股票直接更新正式缓存；中途失败会留下新旧复权数据混合。BaoStock 缺失的 `name` 还可能覆盖正式缓存中的非空名称。
7. `scripts/us_open_run.ps1`、订单查询和 `scripts/post_daily_check.py` 仍有退出码或 JSON 校验不严格的路径。
8. `state/target_snapshot.json` 是被 Git 跟踪的运行时状态，不应继续随代码部署传播。
9. 当前 35 个测试通过，但没有覆盖上述门禁、快照、全量 staging、SDK 模拟账户、调度顺序及故障注入场景。

## 2. 目标

本次重构必须达到以下结果：

1. 用一条可测试、可审计、默认 dry-run 的 Python 流程替代分散的 PowerShell、CLI 和 SDK 状态管理。
2. 只承认交易所日历定义的最近已完成 session，未完成或未来 bar 不得进入策略输入。
3. 线上目标与研究侧调仓相位、模型输入和数据版本完全可追溯。
4. 执行侧只消费经过验证的不可变目标快照，不具备现场选股能力。
5. 数据、持仓、订单或账户身份无法确认时必须 fail-closed，提交次数必须为零。
6. 模拟盘校验、余额、持仓、订单查询、提交和对账必须复用同一个 `TradeContext`。
7. BaoStock 全量刷新失败时，正式缓存保持字节级不变。
8. 每轮运行产生一个不含敏感值的 manifest，可确定执行了什么、使用了什么版本以及在哪里失败。

## 3. 非目标

本轮明确不做：

1. 不连接或启用真实资金账户。
2. 不恢复任何 Windows 定时任务；任务恢复属于实现与验收全部通过后的独立操作。
3. 不在功能开发提交中重写 Git 历史；历史隐私清洗另行在 bare mirror 中执行。
4. 不承诺修复策略 alpha 衰减，也不因工程修复修改已冻结的研究结论。
5. 不把 `scripts/lb_oauth.py` 当作模拟交易解锁手段；它只能作为行情 OAuth 诊断工具。
6. 不在自动流程中换价追单、自动撤单或将未知提交结果视为成功。
7. 不在没有独立人工确认的情况下执行模拟盘 canary 或启用定时任务。

## 4. 安全不变量

以下约束使用 MUST 语义，实施后不得由配置静默绕过：

1. 默认模式必须是 `dry-run`；只有显式 `--mode execute` 才允许进入提交分支。
2. `dry-run` 中 `submit_order`、`replace_order`、`cancel_order` 调用次数必须严格为零。
3. execute 模式必须确认 `enable_papertrading=True`，并校验预登记的模拟盘 credential fingerprint；无法确认时退出非零。
4. 同一轮账户校验、余额、持仓、挂单、提交和对账必须使用同一个 SDK `TradeContext` 实例。
5. 任一行情刷新、完成 bar 截断、数据门禁、快照验证、账户查询或挂单查询失败，均不得调用提交接口。
6. 执行侧不得导入或调用选股计算；快照无效时必须退出，不得回退到实时重算。
7. 输入数据、快照和交易计划必须以 SHA-256 标识；仅用最大日期不足以表示数据版本。
8. 一个生成交易计划的 `run_id` 只能对应一个 `snapshot_hash` 和一个 `plan_hash`；`data-close` 等不生成计划的 run 只记录 snapshot/data hash。发生 hash 冲突时必须退出。
9. 提交超时且无法通过严格查询确认时，订单状态必须为 `unknown`，整轮运行非零退出，禁止自动重试。
10. 凭证、完整 access token、真实 `member_id`、订单号、精确账户资产和现金不得写入 Git、普通日志或 run manifest。
11. 两个定时任务在全部自动化测试、全链路 dry-run 和人工批准的限额 canary 完成前必须保持 `Disabled`。
12. Longbridge CLI 和 `LB_ACCOUNT_KIND=paper` 不得作为交易账户身份依据；执行路径不得调用 CLI，也不得使用未显式设置 `enable_papertrading=True` 的 context。
13. 每笔订单和仓位必须有明确的策略 ownership。未登记的人工仓位或挂单不得自动平仓、撤单或纳入可用资金，并应阻断相关 symbol 的自动执行。
14. 第一笔提交前必须完成整份计划的报价、lot/tick、市场状态、现金、FX、挂单、限额和 ownership 预检；缺少任一输入都不得静默跳过单笔。
15. dry-run 允许更新本地 staging、正式行情缓存、目标快照、计划和脱敏 manifest，但不得产生任何券商写操作。每类本地写入都必须使用原子发布或追加式记录。
16. execute 还必须通过本机未跟踪的 execution policy、预登记 credential fingerprint、部署提交校验和硬风险限额；仅指定命令行参数不足以解锁。

## 5. 总体架构

新增唯一自动化入口，并用 profile 区分数据准备和交易窗口：

```text
scripts/trade_cycle.py --profile data-close --markets cn,us,hk --mode dry-run
scripts/trade_cycle.py --profile hk-open --market hk --mode dry-run
scripts/trade_cycle.py --profile us-open --market us --mode dry-run
```

内部顺序按 profile 固定为两个阶段。`data-close`：

```text
获取进程锁
  → 创建 run manifest
  → 冻结各市场 expected_session
  → 刷新行情到隔离候选区
  → 删除未完成/未来 bar
  → 对候选 bundle 执行全部数据门禁
  → 原子发布通过门禁的 immutable generation
  → 切换 CURRENT 指针并读回校验
  → 判断研究侧调仓相位
  → 构建并验证目标快照候选
  → 原子发布目标快照
  → 完成 run manifest
  → 释放锁
```

`hk-open` / `us-open`：

```text
获取同一全局锁
  → 创建 run manifest
  → 校验部署 commit、execution policy 和 validated 快照
  → 校验交易 session、常规交易时段和计划 TTL
  → 创建同一个 SDK paper TradeContext
  → 先对同一 consumption_key 的历史 `working` intent 做有界只读 reconciliation
  → 查询并验证账户、ownership、持仓、现金、挂单和报价
  → 创建并原子记录唯一 immutable plan/intent
  → 整体风险预检
  → dry-run 输出，或逐笔 fail-stop 提交
  → 严格对账与 post-check
  → 完成 run manifest
  → 释放锁
```

建议新增或重构的职责边界：

| 模块 | 职责 |
|---|---|
| `quant/data/sessions.py` | 交易所日历、市场时区、最近完成 session、session 推进 |
| `quant/data/staging.py` | staging manifest、候选 bundle 校验、generation 组装、并发指纹检查和原子发布 |
| `quant/automation/targets.py` | 研究相位推导、快照构建、schema 与 hash 验证 |
| `quant/automation/plans.py` | 快照到订单计划的纯函数转换、限额校验、计划 hash |
| `quant/automation/ownership.py` | 策略持仓/订单登记、人工资产隔离和迁移校验 |
| `quant/automation/policy.py` | 本地 execute 开关、credential fingerprint、风险限额和部署提交校验 |
| `quant/automation/manifest.py` | `run_id`、阶段状态、输入输出 hash、脱敏错误摘要 |
| `quant/automation/lock.py` | Windows OS 单实例锁和脱敏诊断元数据 |
| `quant/broker/lb_paper.py` | 单一 paper `TradeContext`、credential fingerprint、查询、提交、严格对账 |
| `quant/automation/cycle.py` | 编排阶段、fail-closed 状态机、统一退出码 |
| `scripts/trade_cycle.py` | 参数解析和调用 `cycle.py`，不承载业务逻辑 |

旧脚本在迁移期可保留为兼容入口，但不得再独立提交订单。PowerShell 脚本最终只允许调用 `trade_cycle.py` 并原样传播退出码。

### 5.1 调度 profile

“刷新完成日 K”与“市场开放时提交”不是同一时刻，因此仍使用一个编排器，但拆成不可混淆的 profile：

| profile | 建议北京时间 | 允许动作 |
|---|---:|---|
| `data-close` | 16:45 | 刷新 CN/HK 完成 bar、刷新 US 最近已完成 bar、门禁与目标快照；可输出不可执行预览，禁止券商写入 |
| `hk-open` | 09:35 | 读取 validated HK 快照，查询当前账户/报价后生成唯一订单计划，再 execute 或 dry-run |
| `us-open` | 22:35 | 读取 validated US 快照，查询当前账户/报价后生成唯一订单计划，再 execute 或 dry-run |

`22:35` 同时晚于美股夏令时和冬令时常规开盘；不得继续使用只适合夏令时的 `21:35`。实际执行还必须同时满足交易所日历和 SDK 市场状态均为常规交易时段；两者不一致时阻断。

现有 `QuantDailyRun` 和 `QuantUSOpen` 不直接原地启用。实施计划应定义新 profile 与任务的明确映射，并在 dry-run 验证后才替换任务 action/trigger。需要新增或拆分任务时，所有新任务初始状态也必须为 `Disabled`。

### 5.2 审查副本与运行副本

代码只在审查副本开发和验收。部署到 `D:\3314\quant-research` 必须通过单一发布步骤完成，并记录已验收 commit。execute 模式启动前必须验证：

- 运行副本工作树干净；
- `HEAD` 等于 execution policy 中批准的 commit；
- 运行配置、快照和计划引用同一 commit；
- 不能因审查副本已修复就假定 D 盘任务已使用新代码。

两个或更多 profile 必须竞争同一个 OS 持有的全局文件锁。锁应在进程退出或崩溃时由操作系统释放；元数据文件只用于诊断，不作为锁本身，也不通过猜测 PID 自动删除锁。

## 6. 交易日历与完成 bar

### 6.1 日历来源

主实现拟固定为 `exchange_calendars==4.13.2`；只有在项目 Python 3.13 / pandas 3.1 CI 组合实测通过后才可写入锁定依赖并用于 execute：

| 市场 | 日历 | 本地时区 |
|---|---|---|
| CN | `XSHG` | `Asia/Shanghai` |
| HK | `XHKG` | `Asia/Hong_Kong` |
| US | `XNYS` | `America/New_York` |

依赖版本必须与 Python 3.13 和当前 pandas 版本在 CI 中实测。日历初始化或查询异常时，execute 模式必须失败；保守的 `ZoneInfo + 正常收盘时间 + 缓冲` 回退只可用于诊断或 dry-run，并必须在 manifest 标记 `degraded_calendar=true`，不能授权提交。

Yahoo 元数据可用于交叉核对交易所时区与 regular session，但不得成为唯一真相源。

### 6.2 cutoff 规则

1. 每轮开始时以可注入的 `now` 为每个市场计算一次 `expected_session`，整轮不可变化。
2. session 只有在交易所日历定义的收盘时间加安全缓冲后才算完成；安全缓冲默认 15 分钟。
3. 个股和基准指数必须使用相同 `expected_session`。
4. 抓取结果中 `date > expected_session` 的行必须在计算 `pct_chg`、因子和覆盖率之前删除。
5. 正式缓存最新日期必须严格等于 `expected_session`；早于它表示陈旧，晚于它表示含未来或未完成 bar，两者均失败。
6. 指数为空、指数日期不等于 `expected_session` 或个股/指数 cutoff 不一致均失败。

## 7. 数据门禁

每个市场至少验证：

- schema 和数据类型完整；
- `max(date) == expected_session`；
- `(code, date)` 无重复；
- 最新 session 股票覆盖率达到市场阈值；分母必须是本轮冻结的 eligible universe，不能使用历史缓存中所有曾出现过的代码；
- 基准指数非空且日期一致；
- OHLC 为有限正数并满足 `low <= open/close <= high`；
- `volume >= 0`，需要时验证 `amount >= 0`；
- 无不合法代码或市场后缀；
- 候选数据没有比已批准基线更短的非预期历史覆盖。
- 目标快照选中的证券必须 100% 具有合格数据；市场整体 90% 覆盖率不能替代目标证券完整性。

v1 的 universe、benchmark 和最低覆盖率固定为：

| 市场 | eligible universe | benchmark | 最低覆盖率 |
|---|---|---|---:|
| CN | BaoStock 历史时点沪深 300 成分 | `000300` | 90% |
| US | 版本化 `US_UNIVERSE` 静态名单 | `SPY` | 90% |
| HK | 版本化 `HK_UNIVERSE` 静态名单 | `^HSI` | 90% |

US/HK 静态名单的幸存者偏差必须继续披露；改变 universe 或 benchmark 会改变 version hash 并触发显式模型迁移。

门禁结果是结构化对象，不允许通过解析控制台文本决定是否继续。任一 `FAIL` 阻断快照发布和交易计划生成。

门禁必须针对隔离候选 bundle 运行，而不是先覆盖正式缓存再检查。一个 bundle 包含本轮请求市场的个股、基准、FX 和 manifest；所有请求市场全部通过后，才允许发布。

正式数据采用 immutable generation：`data/cache/generations/<dataset_id>/`。生成目录内文件全部落盘、`fsync` 并读回验证后，再原子替换很小的 `data/cache/CURRENT.json` 指针。所有读者先解析一次 `CURRENT.json`，整次计算只读取同一 generation。任一市场失败时不切换指针，旧 generation 保持完整可用；发布后门禁只做读回一致性检查，失败则回切上一指针并阻断后续阶段。

## 8. 研究相位一致的目标快照

### 8.1 调仓日推导

目标快照不得以部署日或系统日期作为新锚点。builder 必须使用与回测一致的：

- 同一 `Panel.dates`；
- 同一 `warmup`；
- 同一 `rebalance_days`；
- 同一稳定 `phase_anchor` 或策略起始 session；历史缓存起点变化不得造成整个调仓相位漂移；
- 同一市场 preset、股票池、因子代码和过滤参数；
- 同一条件 `(i - warmup) % rebalance_days == 0`。

对每个市场，从已截断到 `expected_session` 的面板中重放到最近一个合法研究调仓 session。builder 必须复用研究侧共享的 schedule/strategy 代码，部署层不得另写一份日期公式。非调仓日不生成新目标；若需要恢复缺失快照，只能恢复最近合法调仓 session 的研究目标，不能因此重新取得交易授权。

只有调仓 session 推进时才自动构建新候选。模型、preset、股票池或策略代码在周期内变化时应阻断执行并要求显式迁移，不能自动覆盖仍在周期内的旧目标。普通非调仓日快照应保持字节级不变。

迁移时为每个市场从基线研究面板一次性提取 `Panel.dates[warmup]` 作为 `phase_anchor`，写入版本化模型配置，此后不得再从当前缓存起点动态推导。共享 `RebalanceSchedule` 按交易所 session 序列计算相对 anchor 的 ordinal；ordinal 对 `rebalance_days` 取模为 0 才是调仓 session。未来 `next_rebalance_session` 也由同一交易所 session 序列计算，不要求未来日期已存在于 Panel。

迁移回归必须至少证明：基线数据下 US 最近调仓 session 为 `2026-10-20`、HK 为 `2026-10-07`，并把两市场成员列表和 canonical 权重 hash 固化为 golden fixture。任一不一致都视为策略口径变化，不能继续部署。

### 8.2 快照 schema

快照至少包含：

```json
{
  "schema_version": 1,
  "snapshot_id": "sha256:...",
  "status": "validated",
  "generated_at": "ISO-8601 UTC",
  "market": "us",
  "model_version": "sha256:...",
  "data_version": "sha256:...",
  "universe_version": "sha256:...",
  "calendar": {"name": "XNYS", "version": "..."},
  "phase_anchor": "YYYY-MM-DD",
  "input_cutoff": "2026-10-20",
  "input_slice_hash": "sha256:...",
  "rebalance_session": "2026-10-20",
  "next_rebalance_session": "2026-11-17",
  "eligible_execution_session": "2026-10-21",
  "execution_window_end": "2026-10-21T22:00:00-04:00",
  "source_data_receipt": "sha256:...",
  "warmup": 130,
  "rebalance_days": 20,
  "weights": {"AAPL.US": 0.05},
  "weights_sum": 0.95
}
```

`model_version` 必须覆盖模型 JSON、策略实现、preset、有效配置和股票池规则。`data_version`/`input_slice_hash` 必须覆盖生成该目标时截至 `rebalance_session` 的精确输入切片；同日历史复权重写必须改变 hash，但调仓日之后的正常每日新增数据不得让周期内快照自然失效。当前完整缓存的数据 manifest 另记在 run manifest 中，用于门禁和追溯。`snapshot_id` 只由 schema、market、model/data/universe version、phase、rebalance session、输入切片 hash 和 canonical weights 构成，不得包含 `generated_at`、`run_id` 或其他运行元数据；同一目标的恢复必须得到同一 `snapshot_id`。

### 8.3 Canonical hash 规范

所有 version、snapshot、plan 和 intent hash 使用 SHA-256，输入中包含 schema/version 标签，禁止直接 hash gzip 文件字节或依赖字典插入顺序。

表格规范化规则：

1. 列名和列顺序由对应 schema 固定，额外列导致 schema 失败。
2. 行按 schema 定义的主键升序排序，例如行情按 `(market, code, date)`。
3. 字符串先做 Unicode NFC，再编码 UTF-8，并使用长度前缀；缺失值使用独立 type tag，不能与空字符串混淆。
4. session/date 编码为 ISO `YYYY-MM-DD`；UTC 时间编码为带 `Z` 的 ISO-8601 微秒精度。
5. 整数使用有符号 64 位大端编码；浮点先转 IEEE-754 float64，再按大端字节编码。必需数值中的 NaN/Infinity 直接失败；允许缺失的字段使用 missing tag。
6. hash 输入先包含 schema name、schema version、列名和 dtype，再逐行编码，保证列或类型变化会改变 hash。

JSON 规范化规则：字符串 NFC；对象 key 按 Unicode code point 排序；无多余空白；禁止 NaN/Infinity；价格、权重、金额和 FX 在进入 canonical JSON 前按 schema 指定的 `Decimal` 精度转成定长十进制字符串。实现使用同一个 `canonical_json_bytes()`，并以 golden fixture 验证跨进程结果一致。

`model_version` 的文件集合至少包含：`models/model_v4.json`、`quant/config.py`、`quant/strategies/topk.py`、`quant/strategies/presets.py`、因子实现、共享调仓 schedule、市场 universe 和 execution-relevant model config。每个文件先按规范化相对路径排序，再 hash `path + NUL + content_sha256`；文件集合本身写入 snapshot manifest。

### 8.4 校验与发布

快照发布前必须验证：

- schema 和 `schema_version`；
- `snapshot_id` 可重算且一致；
- 市场、代码后缀和 session 合法；
- 权重均为有限非负数；
- 权重总和和单票上限符合模型配置；
- `model_version`、`data_version`、`universe_version` 与生成目标的调仓日输入切片一致；当前缓存新增 session 不影响周期内已批准目标；
- `rebalance_session` 是研究相位中的合法 session；
- `next_rebalance_session` 是同一面板相位的下一 session，不是普通工作日加法。

通过后使用唯一临时文件、`fsync` 和 `os.replace()` 原子发布。`validated` 表示自动 schema/hash/研究一致性校验已通过，不表示人工逐次批准；执行授权来自独立 execution policy。若构建时已错过该调仓目标的交易窗口，状态必须为 `validated_research_only`，只能用于研究和对账，不能被 open profile 执行。执行侧只读取 `status=validated` 且窗口有效的快照。快照文件移出 Git 跟踪并加入 `.gitignore`；仓库只保留脱敏 schema 示例。

### 8.5 一次性执行窗口

每个新 `snapshot_id` 必须绑定：

- `eligible_execution_session`：`rebalance_session` 之后的第一个常规开盘 session，而不是快照生成日之后的 session；这是不可顺延的 canonical window；
- `execution_window_end`：以该交易所本地时区表示的允许生成订单计划的最后时刻；默认只覆盖该 `eligible_execution_session` 的开盘后受控窗口；
- `source_data_receipt`：最近一次成功 `data-close` 的 dataset/run 摘要；
- `consumption_key`：`account_enrollment_id + market + snapshot_id + eligible_execution_session`。`account_enrollment_id` 是首次人工登记该策略专用模拟账户时生成的稳定本地 UUID，与 token 无关；同一账户 token 轮换不得生成新的 enrollment 或消费历史。

执行 ledger 以 `consumption_key` 为唯一键。新 run 启动时必须先读取 ledger：

- 已 `reconciled` 或明确记录 `noop_in_tolerance`：返回 no-op，不得再次生成订单；
- `working`、`partial` 或 `unknown`：阻断自动提交，进入后续对账或人工处理；
- 尚未消费且处于 `eligible_execution_session` 窗口：可以创建唯一 immutable plan；
- 已错过窗口，或快照在窗口之后才构建：标记 `expired_unconsumed`/`validated_research_only` 并阻断，不能每天用旧快照重新对齐或顺延窗口。

订单计划只在 execution profile 中、基于当时账户和报价生成。非研究调仓日的 open profile 只有在当天等于某个现有快照的 `eligible_execution_session` 时才可执行；否则只能做 dry-run/对账，不得生成新的 broker intent。相同 snapshot/session 即使换了新 `run_id`，也必须命中同一个 `consumption_key` 和稳定 intent，不能绕过一次性消费约束。

## 9. BaoStock staging 与原子发布

采用“分块 staging + manifest + 单次正式替换”：

```text
冻结 expected_codes、start、end_session、当前 generation/dataset SHA-256
  → 每 50 只写 data/staging/baostock/<run_id>/chunk-*.csv.gz
  → 原子更新 staging manifest
  → 全部成功后组装完整 candidate
  → schema/覆盖/OHLC/name/日期/重复行校验
  → 再次确认 CURRENT 和基线 dataset SHA-256 未变化
  → 组装新的 immutable generation
  → 单次原子替换 CURRENT.json
```

具体规则：

1. 任一必需代码失败、空响应或校验失败时不得发布，`CURRENT.json` 及其所指 generation 的 SHA-256、大小和 mtime 必须不变。
2. 每个 chunk 和 manifest 均使用唯一临时文件原子写入，允许安全恢复。
3. 重试必须有次数和退避上限；解码或连接异常可重建 BaoStock 会话，但不得无限重试。
4. `--limit` 默认强制 `--no-publish`，避免部分股票被误认为全量刷新。
5. candidate 的代码集合必须与冻结 inventory 完全一致。
6. `name` 是独立 symbol metadata，不是价格正确性的硬门禁。候选数据用旧缓存和历史成分股快照中的非空名称 coalesce，空值不得覆盖非空值；仍缺名称时记录明确告警并进入 symbol-master 补全清单，不得因此混入旧价格或伪造名称。
7. 不得通过混入旧价格行来补齐失败代码，否则会再次产生新旧复权混合。
8. 成功发布后生成包含 `dataset_id`、内容 hash、来源、复权方式、范围、代码数和校验结果的数据 manifest。

## 10. 单一 Python 编排器

### 10.1 参数与模式

入口参数至少包括：

```text
--markets cn,us,hk
--mode dry-run|execute
--profile data-close|hk-open|us-open
--resume-run <existing-run-id, recovery-only>
--as-of <isolated replay only>
```

默认 `--mode dry-run`。不再同时保留会发生优先级歧义的 `--dry-run` 和 `--execute` 布尔参数。未知参数、互斥配置或 execute 环境缺失必须在任何外部写操作前失败。

`data-close` 只接受 `--mode dry-run`，这里的 dry-run 表示“无券商写入”，而不是本地文件完全只读；该 profile 可以原子发布经过验证的本地数据和目标快照。只有 `hk-open`、`us-open` 可以接受 `--mode execute`。

唯一合法组合为：

| profile | 市场参数 | mode |
|---|---|---|
| `data-close` | 正式发布固定为 `--markets cn,us,hk`；显式子集只允许 `--no-publish` 诊断 | 仅 `dry-run` |
| `hk-open` | 固定 `--market hk`，不接受 `--markets` | `dry-run` 或 `execute` |
| `us-open` | 固定 `--market us`，不接受 `--markets` | `dry-run` 或 `execute` |

CN broker execute、`hk-open --market us`、`us-open --markets us,hk` 等组合必须在创建 manifest 或写任何本地/外部状态前返回参数错误。

正常运行的 `run_id` 由程序生成 UUID4，调用者不能任意指定。`--resume-run` 只允许恢复已有私有 ledger 且参数/hash 完全一致的 run；不存在、已完成或内容冲突时拒绝，manifest 永不覆盖。

`--as-of` 与 `--mode execute`、正式 `CURRENT.json` 发布、真实 SDK context 均硬性互斥。历史重放只能使用临时数据根目录、fake broker 和 `--no-publish`，退出时不得改动正式缓存、快照、policy、ledger 或计划任务。

execute 模式还要求本机未跟踪的 `state/execution_policy.json` 明确 `enabled=true`，并包含批准的 credential fingerprint、部署 commit、允许市场和风险上限。文件缺失时等价于关闭。dry-run 可以创建或更新本地数据、快照、计划和 manifest，但必须在输出中清楚列出这些本地副作用。

### 10.2 锁与运行状态

- 整轮流程获取 Windows OS 持有的全局文件锁，防止所有调度 profile 与人工命令并发；进程退出或崩溃时由操作系统释放。
- 伴随的锁元数据记录 PID、主机、开始时间和 `run_id`，但不含敏感账户数据，也不作为互斥依据。
- 不通过读取 PID 或文件时间自动抢占所谓“陈旧锁”；获取锁失败即返回锁冲突。
- run manifest 使用阶段状态机；`data-close` 允许 `created → data_ready → gate_passed → snapshot_ready → completed`，open profile 继续 `snapshot_ready → plan_ready → executing → working/reconciled → completed`。任何异常写入 `failed_stage`，不得跳过前置阶段。
- 任一异常写入 `failed_stage`、脱敏错误类型和最终退出码；不记录 token、完整订单号或资产金额。

### 10.3 退出码

建议固定退出码：

| 退出码 | 含义 |
|---:|---|
| 0 | 本轮成功，或 dry-run 成功完成且零提交 |
| 2 | 参数或配置错误 |
| 3 | 锁冲突 |
| 10 | 行情刷新或 staging 失败 |
| 11 | 数据门禁失败 |
| 12 | 快照构建或验证失败 |
| 13 | 交易计划验证失败 |
| 20 | 模拟账户身份或查询失败 |
| 21 | 订单提交出现明确错误 |
| 22 | 提交结果未知或对账失败 |
| 30 | post-check 失败 |

PowerShell 兼容入口必须原样返回该退出码，不得累计错误后继续交易阶段。

### 10.4 运行终态

manifest 的最终状态必须明确区分：

| 状态 | 语义 | 退出结果 |
|---|---|---|
| `SUCCESS` | 所有预期阶段和严格对账完成 | 0 |
| `NOOP_NOT_DUE` | 不在调仓期，或组合已在明确容差内 | 0 |
| `SKIPPED_MARKET_CLOSED` | 日历确认市场休市，未提交 | 0；manifest 保留独立状态，调度器不得记为已执行交易 |
| `WORKING` | 每笔 intent 已取得唯一 acknowledgment，但至少一笔尚未终态 | 0；必须由只读 reconciliation 继续跟踪，调度监控按“未完成”告警，不等于成交成功 |
| `BLOCKED` | 第一笔提交前的校验失败，保证零提交 | 非零 |
| `PARTIAL` | 至少一笔已确认提交，后续出现明确失败 | 非零，立即停止 |
| `UNKNOWN` | 提交结果无法唯一确认 | 非零，立即停止且禁止自动重试 |

“零订单”只有在 `NOOP_NOT_DUE` 或有证据的容差内状态才算成功。缺报价、缺现金、市场状态未知或查询失败均属于 `BLOCKED`。

## 11. Longbridge SDK-only 模拟盘执行

### 11.1 凭证与账户身份

凭证只允许来自一个明确来源：仓库根目录本机 `.env`，或完整的一组受控进程环境变量：

- `LONGBRIDGE_APP_KEY`
- `LONGBRIDGE_APP_SECRET`
- 模拟盘专用 `LONGBRIDGE_ACCESS_TOKEN`

三项环境变量必须全部来自同一来源；若 `.env` 与进程环境同时提供且值冲突，必须失败，不允许静默覆盖或搜索多个 `.env`。不得要求用户在聊天、命令行历史或日志中粘贴 token。`Config.from_apikey(..., enable_papertrading=True)` 创建配置，并为整轮运行创建一个 `TradeContext`。

Longbridge SDK 5.2.2 当前不保证提供稳定账户 ID，因此 v1 明确校验 credential/environment，而不虚构“券商账户 ID”。canonical bytes 定义为 `UTF8(app_key) + NUL + UTF8(access_token) + NUL + UTF8("paper")`，`credential_fingerprint = SHA256(canonical_bytes)`；不包含 `app_secret`。token 轮换后必须由用户在同一个 `account_enrollment_id` 下人工登记新 fingerprint，并先完成只读 paper/schema 验证；不得创建新的 enrollment，不得清空或重置既有 consumption ledger。若无法证明新凭证属于同一策略专用账户，所有未完成/未过期快照立即失效，execute 保持关闭。完整 fingerprint 只保存在私有 execution policy，manifest 只记录前 12 个十六进制字符和 `paper=true`。

同一个 `Config.from_apikey(..., enable_papertrading=True)` 对象创建整轮唯一 `TradeContext` 和 `QuoteContext`。账户、持仓、订单、提交和对账只走 `TradeContext`；实时报价、market status、`static_info.lot_size` 和 tick 所需元数据只走该 `QuoteContext`。任一 SDK 查询失败或 schema 异常均阻断，不得回退 CLI 或默认值。

创建 context 后还必须调用 `account_balance()` 并验证 SDK 响应 schema、币种条目和 paper context 可用；这证明该 credential 能访问模拟环境，但不得表述为 SDK 返回了并不存在的稳定 account ID。若未来 SDK 提供稳定 ID，只有经过版本验证和迁移设计后才能升级 fingerprint schema。

v1 只支持“该模拟账户专用于本策略”，不支持与人工交易共享现金。execution policy 必须由用户显式登记 `account_purpose=quant-research-paper-exclusive`；该账户全部币种现金才可作为策略现金，且不建立容易漂移的虚拟现金账本。若账户还要保留人工现金或其他策略，本版本 execute 必须保持禁用。

现有模拟盘持仓不得自动全部归属于策略。迁移时要生成一次待审核 ownership 清单，用户明确确认后才写入本机未跟踪 registry；用户也可以先在券商端人工清理。未登记仓位、人工挂单或归属冲突会阻断整个账户 execute，自动流程不得撤销、平仓或挪用这些资产。

### 11.2 计划和限额

执行前必须将快照与当前模拟盘持仓转换为唯一计划，并验证：

- `run_id`、`snapshot_hash`、`plan_hash` 一致；
- symbol、side、quantity、order type、limit price、lot size 和 tick size 合法；
- 单笔金额、单市场总金额、订单数量和总换手不超过硬上限；
- 当日此前已经提交的策略订单必须计入单日累计金额和笔数，重复运行不能重置限额；
- 可用现金和币种购买力足够；
- 未决订单查询成功且不存在冲突；
- 计划未过期，且快照与行情版本未变化。

代码内缺失 policy 的默认是 `enabled=false`、允许订单数为 0、允许 notional 为 0；没有本机 policy 时不可能 execute。以下仅是用户创建 canary policy 时的推荐初值，不是代码默认值：`max_quote_age_seconds=30`、`plan_ttl_seconds=120`、`max_slippage_bps=50`。扩大限额属于显式配置变更，不得由行情或账户规模自动放大。

execution policy 必须显式提供以下带单位字段，缺少任一字段即 schema 失败：`max_order_cny`、`max_run_cny`、`max_day_cny`、`max_orders_run`、`max_orders_day`、`rebalance_tolerance_bps`、`min_cash_buffer_bps`、`min_order_cny`、`max_quote_age_seconds`、`plan_ttl_seconds`、`max_slippage_bps`。金额均为 CNY 等值，bps 相对于对应 sleeve NAV。缺失 policy 时代码级 `enabled`、订单数和金额上限均为 0；`max_quote_age_seconds`、`plan_ttl_seconds`、`max_slippage_bps` 只有在用户显式创建 canary policy 时才采用推荐值，不能被缺省填充。

初始版本仅允许 long-only 现货、`LO`、`Day` 和常规交易时段；禁止做空、融资、期权、市场单、盘前盘后和自动换汇。交易 policy 还必须明确单笔/单轮/单日金额与订单数上限。

lot size、tick size、报价或市场状态查询失败时必须阻断，不能回退到港股默认 100 股，也不能因缺报价静默丢弃单笔。现金不足时按预先定义的统一比例缩放全部买单并重新做 lot 舍入和计划 hash；不得逐笔按排序丢单。若缩放后违反最小订单或组合容差，则整轮阻断。未成交卖单的预计回款不得计入买入购买力。

组合预算以已登记为策略专用的模拟账户净资产为基础，跨币种统一到 CNY 口径。移除硬编码 `USD_HKD=7.80`，改用经过门禁、带时间戳和内容 hash 的 `USDCNY`、`HKDCNY` 数据；汇率 session 必须等于 FX 日历最近已完成 session，否则阻断。所有金额风险上限也统一换算为 CNY 等值。有效现金缓冲取模型配置和 policy `min_cash_buffer_bps` 中更保守者；rebalance tolerance、小额/零碎股舍入和 partial fill 规则必须作为计划配置参与 `plan_hash`。partial fill 不追价、不自动补单，保留为 `working` 直至终态或转人工处理。

### 11.3 幂等与对账

- 第一笔提交前，先在私有 execution ledger 中原子记录整份计划及每笔 immutable intent。
- 稳定 `intent_id` 不包含 `run_id` 或 token fingerprint，而是对 `account_enrollment_id + paper + execution_session + snapshot_id + symbol + side + quantity + order_type + limit_price + TIF` 的 canonical 值做 SHA-256。不同 run 或 token 轮换重试同一 snapshot/session 时得到相同 `intent_id`。token fingerprint 只用于当前凭证门禁。
- 订单 remark 使用不含敏感值的确定性短标识，例如 `qr:<intent-id-prefix>:<line-id>`。
- 每个新 run 都必须先按 ledger 的 `intent_id` 对账，再按 remark、symbol、side、quantity、order type、价格和 execution session 做 broker 唯一匹配。
- 只有唯一匹配且处于 SDK 明确定义的有效状态时，才可判为已经提交。
- 旧单、反向单、拒单、已撤单或多重匹配均不能作为本轮成功证据。
- 单笔提交异常后必须先严格查询；查询失败则立即将本轮置为 `unknown` 并停止后续提交。
- 一笔明确拒绝后也必须 fail-stop；禁止当前实现中的三级提价重试。
- 每笔提交前重新验证 plan TTL 与报价年龄；任一过期立即停止剩余订单。重新报价必须生成新 plan/hash/intent，并需要未消费 snapshot 的显式恢复流程，不能沿用旧 hash。

同步提交验收与异步成交对账分离：

1. 同步阶段要求每笔取得唯一 broker acknowledgment/order ID，或被唯一查询确认。未确认即 `UNKNOWN`。
2. acknowledgment 后合法的 `Submitted`/`New`/`PartFilled` 记录为 `working`，不伪装成最终成交，也不立即再次提交。
3. reconciliation 在有界轮询内更新 `working` 状态；达到轮询上限仍 working 时保存状态并由后续只读 reconciliation 继续，禁止创建新 intent。
4. `Filled`、`Cancelled`、`Expired`、`Rejected` 等终态按计划规则结算；发生部分成交或部分订单失败时整体标记 `PARTIAL`，不自动追单。
5. post-check 只查询和验证当前 `consumption_key`/intent 集合，不受历史拒单污染。查询失败、响应 schema 不符、order ID 缺失或无法唯一对应时返回非零。

私有 execution ledger 位于 Gitignored 且限制本机 ACL 的目录，可以保存恢复所必需的完整 broker order ID、intent 和状态历史。普通日志、公开报告及 run manifest 只能记录 hash/ID 前缀和汇总，不得包含完整订单号或资产金额。ledger 采用每 run 独立追加记录与原子索引，不能覆盖单一 `lb_order_map_result.json`。

## 12. 失败处理矩阵

| 失败点 | 是否发布新数据 | 是否发布新快照 | 是否允许提交 | 结果 |
|---|---:|---:|---:|---|
| 日历不可用 | 否 | 否 | 否 | 非零退出 |
| 任一行情抓取失败 | 否 | 否 | 否 | 非零退出 |
| Yahoo 含未完成 bar | 截断后再验证 | 仅门禁通过后 | 仅全部通过后 | 记录截断数量 |
| 指数为空或日期不一致 | 可保留旧正式数据 | 否 | 否 | 非零退出 |
| BaoStock 任一代码失败 | 否 | 否 | 否 | 保留 staging，正式缓存不变 |
| 快照缺失、损坏或版本不匹配 | 不影响已发布数据 | 否 | 否 | 非零退出 |
| 非研究调仓 session且不是现有快照的 `eligible_execution_session` | 不变 | 快照字节级不变 | 仅允许 dry-run/对账 | `NOOP_NOT_DUE` |
| 现有快照的 `eligible_execution_session` | 不变 | 不生成新快照 | 可按一次性 `consumption_key` 执行 | `SUCCESS`/`WORKING` |
| 日历确认休市 | 不变 | 不变 | 否 | `SKIPPED_MARKET_CLOSED`，退出 0 并保留独立状态 |
| 日历显示开市但 SDK 状态未知/冲突 | 不变 | 不变 | 否 | `BLOCKED`，非零退出 |
| 账户身份不明或非 paper | 不变 | 不变 | 否 | 非零退出 |
| 持仓、现金或挂单查询失败 | 不变 | 不变 | 否 | 非零退出 |
| 提交明确拒绝 | 不变 | 不变 | 停止后续提交 | `error`，非零退出 |
| 提交已确认但订单仍 `Submitted`/`PartFilled` | 不变 | 不变 | 不创建新 intent | `WORKING`，有界轮询后由后续 reconciliation 继续 |
| 提交超时且查询失败 | 不变 | 不变 | 停止后续提交与重试 | `unknown`，非零退出 |
| post-check schema/对账失败 | 不变 | 不变 | 不再提交 | 非零退出 |

## 13. 测试与验收

### 13.1 自动化测试

至少新增：

- `tests/test_market_sessions.py`
- `tests/test_yahoo_cutoff.py`
- `tests/test_pre_trade_gate.py`
- `tests/test_baostock_staging.py`
- `tests/test_store_atomic.py`
- `tests/test_target_snapshot.py`
- `tests/test_trade_plan.py`
- `tests/test_lb_paper_execution.py`
- `tests/test_trade_cycle.py`
- `tests/test_post_daily_check.py`

必须包含以下固定场景：

1. 北京时间 `2026-10-23 21:35` 的 US cutoff 是 `2026-10-22`；只有纽约市场收盘并过缓冲后才推进到 `2026-10-23`。
2. 夏令时、冬令时、周末、美国节假日、Black Friday 半日市、港股和 A 股节假日。
3. Yahoo payload 含盘中当日行时，个股和指数均在入库前截断。
4. 门禁对 `< expected_session`、`> expected_session`、指数为空、指数与个股日期不同全部失败。
5. BaoStock 49、50、51、99、100、101 只边界；flush 分别无空尾批次且最终只发布一次。
6. 第 50 只失败、chunk 写入中断、candidate 校验失败、正式文件并发变化时，正式文件 hash 和 mtime 不变。
7. 非调仓日快照字节级不变；调仓日当天只重建一次；市场节假日不按普通工作日推进。
8. 模型代码、preset、股票池或同日复权数据变化时，对应 version hash 变化。
9. 快照缺字段、hash 错误、NaN 权重、权重越界或过期时，提交调用次数为零。
10. 查询超时、提交超时、重复订单、多重匹配、反向订单和拒单的故障注入。
11. 两个并发进程只有一个获取锁。
12. dry-run 全链路中所有 SDK 写接口调用次数为零。
13. malformed SDK 响应、报价过期、lot/tick 缺失、现金不足、外部挂单、未登记人工仓位、partial fill、cancelled 和 expired 状态。
14. 单笔、单轮、单日累计限额，以及重复运行不能绕过日限额。
15. 运行副本 commit 与批准 commit 不一致时，execute 零提交并失败。
16. 日志、manifest、测试快照和 Git tracked 文件的 secret/privacy 扫描。
17. `data-close`、`hk-open`、`us-open` 在 DST、节假日和市场状态冲突下的固定时间测试。

测试不得访问真实网络、正式缓存或真实券商账户；使用 `tmp_path`、固定时钟和 fake client/context。

### 13.2 分层验收

按以下顺序推进，任何一层失败都停止：

1. 静态检查和全部自动化测试通过。
2. 临时目录中的故障注入和原子性测试通过。
3. 使用 fake broker 和隔离数据根目录分别运行三个 profile 的离线全链路 dry-run，确认 plan hash 可复现且写接口调用为零。
4. 本机人工配置模拟盘专用 Legacy Access Token，登记 credential fingerprint，并只做 `TradeContext`、余额、持仓和挂单的只读 schema 验证。
5. 在三个 profile 的目标时间窗口运行 connected dry-run；使用真实本地缓存和真实 SDK 只读查询，确认所有券商写调用仍为零。
6. 用户独立确认 canary 的 symbol、最大数量/金额、订单类型、是否允许成交及成功/停止条件后，先提交一笔明确不期望成交的远端限价单，验证 acknowledgment、唯一查询和人工撤单后的 reconciliation；自动系统本身不新增通用撤单能力。
7. 第一次 canary 通过并再次获用户批准后，再用目标内 US 标的一股或用户明确的小额上限验证成交与持仓对账。
8. 两阶段 canary 的同步 acknowledgment 和后续 reconciliation 全部通过后，先恢复一个定时任务观察至少一个完整周期。
9. 再次人工确认后才恢复另一个任务。

“测试通过”“dry-run 通过”“canary 通过”“任务恢复”必须分别报告，不能合并为一个完成状态。

## 14. 文档、指标与隐私

实施阶段同步完成：

1. `models/model_v4.json` 仅替换正式人民币口径字段，保留本地货币指标；增加指标来源、数据截止日期和报告 hash。
2. README 重写正式人民币结果，移除已作废结论作为正文真相源，并明确系统只连接 Longbridge 模拟盘环境。
3. 修订审计文档中“`daily_run` 已接入”“OAuth 可解锁交易”等与代码不符的描述；历史记录保留并标注历史快照。
4. 当前树中的 `member_id`、订单号、精确资产和现金做普通提交脱敏。
5. `state/target_snapshot.json`、run manifest、订单结果和 staging 数据移出 Git 跟踪并加入 `.gitignore`；提交脱敏示例文件。
6. 功能稳定后，独立使用 `git filter-repo` 清洗全部分支和标签，再检查 GitHub Actions artifacts、Release、Issue 和 PR 中的外部副本。

仓库在历史清洗和公开前检查完成前必须保持私有。

## 15. 迁移与回滚

### 15.1 迁移

1. 先实现纯函数和 fake 驱动测试，不接触定时任务。
2. 引入 session、staging、snapshot、plan、manifest 和 SDK paper adapter。
3. 新编排器稳定后，将 PowerShell 脚本改成只调用编排器。
4. 旧 `lb_order_map.py`、`lb_rebalance.py` 的提交入口先禁用，再在兼容期结束后删除或改为只读计划工具。
5. 运行时状态迁移到 Git 忽略目录；旧 tracked 快照仅用于迁移校验，不直接批准执行。

现有自动化功能的 v1 去留必须明确：

| 现有功能 | v1 处理 |
|---|---|
| `scripts/run_paper.py` | 保留为手动、本地 A 股研究模拟工具；从自动交易调度链路移除，不影响 SDK broker ownership/ledger |
| `scripts/paper_live.py` | 标记 deprecated 并从调度链路禁用；不得再用 Longbridge 实时报价直接修改本地 paper state |
| `scripts/lb_daily_report.py` | 由新的 SDK、`consumption_key` 和私有 ledger 报告替代；旧 CLI 报告入口不得进入新调度 |
| `scripts/lb_order_map.py` / `scripts/lb_rebalance.py` | 先禁用提交入口，只保留迁移比对；验收后删除或变成无 broker write 的诊断工具 |
| SG sleeve | 保留研究和行情能力，v1 自动 broker execute 明确不支持；不得被 `both` 等旧参数隐式下单 |
| CN broker execute | 不在 v1 范围；仅保留本地 research paper，不连接真实或模拟券商执行 |

### 15.2 回滚

- 代码回滚不自动恢复旧交易入口；定时任务继续保持 `Disabled`。
- 数据发布前保留正式文件 fingerprint 和受控备份引用；只有校验后的上一个完整版本可回滚。
- 快照回滚必须同时满足其 `model_version`、`data_version` 和 `universe_version` 仍与输入一致，否则只能重新构建。
- 出现任何 `unknown` 订单时，禁止通过重跑代码“修复”；必须先人工核对模拟盘订单状态。

## 16. 完成定义

只有同时满足以下条件，本次实现才可标记完成：

- 本设计中的安全不变量都有对应自动化测试；
- 全部旧测试和新增测试通过；
- 数据 staging 故障注入证明失败时正式缓存不变；
- 目标快照与研究侧调仓相位复算一致；
- dry-run 证明券商写调用严格为零；
- 同一 SDK 模拟盘上下文的只读账户验证通过；
- README、模型指标、审计状态和隐私当前树完成修订；
- 两个 Windows 定时任务仍为 `Disabled`；
- 尚未执行的 canary、任务恢复和历史重写明确报告为待办，而不是被描述成已完成。

## 17. 已知剩余风险

即使本设计全部实现，仍存在以下外部风险：

- Yahoo、BaoStock 或 Longbridge 服务异常和字段变化；
- 交易所临时停市、特殊结算安排或日历库更新滞后；
- 模拟盘撮合与真实市场不同，canary 不能证明真实资金执行表现；
- 策略 alpha 衰减、滑点和成交容量属于研究风险，工程护栏不能消除；
- 历史敏感信息在 GitHub 外部副本中可能仍需人工清理。

这些风险必须在运行报告中保持可见，不能通过降级为 fail-open 来掩盖。

## 18. 补充约定（lead 评审 2026-10-24）

1. **错过执行窗口**：若某快照的 `eligible_execution_session` 当天机器未运行，
   记为 `SKIPPED_MISSED_WINDOW`，**绝不自动补单**（补跑会引入未来相位状态）；
   下一个可执行时点必须属于下一个调仓周期。
2. **日历 fallback**：若 `exchange_calendars` 与 pandas 3.1 / numpy 2.4 / Py3.13 不兼容，
   允许 vendored 交易日历表（XSHG / XHKG / XNYS，2024–2027）作为 Plan B；
   execute 路径不得使用「工作日近似」。
3. **账户身份**：`account_enrollment_id` 是首次人工登记模拟账户时生成的本地 UUID，
   与 token 无关；token 轮换不得产生新 enrollment 或重置消费历史。
4. **Python 版本**：最低支持 3.12（代码使用了 3.12+ 的嵌套 f-string）；
   `requires-python >= 3.12` 写入 pyproject，CI 使用 3.13。
