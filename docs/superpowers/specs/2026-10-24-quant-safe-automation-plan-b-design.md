# 量化交易安全自动化方案 B 设计规范

- 状态：已批准，可按阶段离线实施
- 文档版本：`plan-b-v1`
- 审核基线：`286e9bacfd6e5d080bee3f003be51b02d39dccc6`
- 适用日期：2026-10-24
- 当前运行状态：`QuantDailyRun`、`QuantUSOpen` 均保持 `Disabled`
- 实施边界：执行代理只能产出离线候选；真实授权、运行副本发布、canary 和定时任务恢复仅由用户与 Codex lead 执行

本规范是完整 P0–P7 的唯一权威设计。它取代以下旧文档中的冲突内容：

- `docs/superpowers/specs/2026-10-24-quant-safe-automation-design.md`
- `docs/superpowers/plans/2026-10-24-quant-safe-automation-deepseek-handoff.md`
- `docs/superpowers/reviews/2026-10-24-lead-review.md` 中已经被本规范明确裁决的建议

“方案 B”在本文中指完整的安全自动化替换方案，不是 vendored 交易日历 fallback。日历主方案固定为已验证兼容的 `exchange_calendars==4.13.2`；本轮不实现 vendored 日历。

## 1. 已核实基线与问题

基线提交已经独立验证：远端 `main` 与本地审查副本均为 `286e9ba`，全套测试为 `42 passed`。这只能证明现有测试通过，不能证明安全自动交易链已经完成。

现有阻塞问题：

1. `scripts/daily_run.ps1` 在数据步骤失败后仍继续两个旧 `--execute` 入口。
2. 旧执行链用 SDK Legacy 凭证验证模拟盘，却用 Longbridge CLI OAuth 会话实际查询和提交，无法证明校验与下单是同一账户。
3. `scripts/lb_order_map.py` 在快照缺失、损坏或过期时现场重算目标，属于 fail-open。
4. Yahoo 盘中未完成 bar 可被当前美股门禁放行。
5. `fetch_baostock_full.py`、`fetch_tx.py`、`fetch_baostock_fallback.py`、`fetch_market.py`、`fetch_data.py` 等 producer 会在整轮成功前写正式 flat cache；部分失败可留下混合状态。
6. 当前分块测试只证明旧循环在 49/50/51/99/100/101 边界不再崩溃，没有证明 staging 或原子发布。
7. `post_daily_check.py` 依赖 CLI 且存在 WARN-only 路径，订单查询异常仍可能被误判为 PASS。
8. `.github/workflows/ci.yml` 的依赖安装 fallback 会掩盖 `requirements.txt` 安装失败。
9. `.gitignore` 的未锚定 `reports/` 会误伤 `docs/superpowers/reports/`。
10. 当前审查副本没有运行数据，不能由离线代理自行证明 US/HK 真实研究目标 golden。
11. CN `PhaseEnsembleStrategy(master_daily=True)` 是每日主目标加四个成员相位更新，不等价于单一每 35 个 session 调仓公式。
12. 仓库虽为 private，但历史和部分旧文档仍含账户、订单或资产信息；公开前必须另案清洗历史。

## 2. 目标与非目标

### 2.1 目标

方案 B 必须实现：

1. 单一 Python 编排器、结构化状态机和确定退出码。
2. 数据先写候选区，通过统一门禁后一次发布为 immutable generation。
3. 所有计算在一轮内绑定同一 `CURRENT.json` generation，不读取混合 flat cache。
4. US/HK 研究 schedule、目标快照、执行窗口和消费账本可追溯、可复现。
5. 执行端只消费 validated immutable snapshot，不导入选股实现。
6. Longbridge 只用同一份 Python SDK OAuth `Config(enable_papertrading=True)`；本轮恰好一个 `TradeContext` 负责账户/订单，恰好一个 `QuoteContext` 负责报价/市场状态。
7. dry-run 的券商写调用严格为零；任何身份、数据、状态或查询不确定均 fail-closed。
8. 每次运行产生脱敏 manifest，输入、输出和失败位置均可核验。
9. 所有开发与验收可在 `tmp_path`、固定时钟、fake source、fake broker 下完全离线完成。

### 2.2 非目标

本轮不做：

1. 不连接或启用真实资金账户。
2. 不解决策略 alpha 衰减，不改写已冻结研究结论。
3. 不让 DeepSeek 或其他执行代理读取 `.env`、OAuth token、账户、订单或 D 盘运行数据。
4. 不让执行代理递归浏览整仓、`docs/journal/**`、未列入阶段 allowlist 的分析/报告文件或 Git 历史/blob；当前 tracked 历史文档仍可能含真实账户、订单或资产值。每个阶段只能读取其批准 execution plan 的精确文件 allowlist，历史文档若仅需校验 banner，只能读取顶部指定行数。
5. 不由执行代理运行 OAuth、不访问 Longbridge/Yahoo/BaoStock、不修改 Windows 定时任务。
6. 不在本轮重写 Git 历史；在清洗完成前仓库必须保持 private。
7. 不实现 SG 执行，不实现 CN 券商执行；v1 broker execute 仅 US/HK。
8. 不实现 vendored 交易日历；只有未来 CI 给出不兼容证据时才另立设计。
9. 不自动换价追单、自动补单或把未知订单状态视为成功。

## 3. 不可绕过的安全不变量

以下条款均为 MUST：

1. 默认模式是 `--mode dry-run`；只有显式 `--mode execute` 才可进入提交分支。
2. dry-run 中 `submit_order`、`replace_order`、`cancel_order` 调用次数必须为 0。
3. execute 必须同时通过本地 execution policy、部署 commit、OAuth cache、paper guard、enrollment、数据、快照、计划、ownership、现金、报价、lot/tick、限额和挂单检查。
4. 余额、持仓、挂单、提交和订单对账必须复用本轮唯一一个 SDK `TradeContext`；报价、市场状态、lot/tick 必须复用本轮唯一一个 `QuoteContext`。二者由同一 OAuth paper `Config` 构造。
5. execute 调用图禁止 Longbridge CLI、`subprocess` broker write、Legacy API key 和 `Config.from_apikey`。
6. 快照无效时退出；不得现场选股或回退到旧 `state/target_snapshot.json`。
7. execute 只能读 immutable generation；不得回退到 legacy flat cache。
8. 第一笔订单提交前必须完成整份计划预检；不得逐笔遇错后继续剩余订单。
9. 任何提交超时或查询不确定都记为 `UNKNOWN`，禁止自动重试，整轮非零退出。
10. 同一 `consumption_key` 最多绑定一份 execute plan；plan 可含多条订单 intent，但每个稳定 `intent_id` 最多提交一次。终态、部分成功或 unknown 均不得自动再提交。
11. 未登记的人工仓位和挂单不能被自动平仓、撤单或计入可用现金，并阻断相关 symbol。
12. 错过唯一执行窗口必须记 `SKIPPED_MISSED_WINDOW`，永久不补单。
13. token、secret、真实 `member_id`、完整订单号和精确资产不得进入 Git、普通日志或 manifest。
14. 运行锁必须由 OS 持有；元数据文件不能替代锁，也不能通过猜测 PID 抢锁。
15. `policy`、`enrollment`、`ledger` 不是可再生产物，回滚或迁移时不得递归移除 `state/` 根目录。
16. `QuantDailyRun`、`QuantUSOpen` 和未来 `QuantFXClose`、`QuantHKOpen` 在用户再次批准前始终保持 `Disabled`。
17. 执行代理不得做递归文件枚举、全仓内容搜索或 Git 历史/blob 浏览；只能读取阶段施工单列出的精确路径和范围。测试与编译可运行，但不得为排障扩大读取范围；遇到 allowlist 外依赖时报告 `BLOCKED_FILE_SCOPE_MISSING`。

## 4. 总体架构

唯一入口：

```text
python scripts/trade_cycle.py --profile data-close --markets cn,us,hk --mode dry-run
python scripts/trade_cycle.py --profile fx-close --mode dry-run
python scripts/trade_cycle.py --profile hk-open --market hk --mode dry-run
python scripts/trade_cycle.py --profile us-open --market us --mode dry-run
```

open profile 的默认 `dry-run` 是纯离线预览，只接受测试/审核 fixture，不创建真实 SDK context；没有 fixture 时返回 policy block。只有用户/Codex lead 可使用 `--mode connected-dry-run` 做真实 SDK 只读检查，且 broker write 仍严格为 0。`execute` 才允许提交。data/fx profile 只接受 `dry-run`，其中 dry-run 仍可写本地 candidate/generation；`--no-publish` 才禁止切本地指针。

正式模块边界：

| 模块 | 唯一职责 |
|---|---|
| `quant/automation/canonical.py` | canonical JSON/表格编码和 SHA-256 |
| `quant/automation/atomic.py` | 临时文件、flush/fsync、读回、原子替换 |
| `quant/automation/status.py` | 状态、错误类型和退出码 |
| `quant/automation/lock.py` | Windows/Linux OS 单实例锁 |
| `quant/automation/manifest.py` | 脱敏 run manifest |
| `quant/automation/policy.py` | 本地 execute policy、批准 commit 和硬限额 |
| `quant/automation/ownership.py` | 策略仓位和订单 ownership |
| `quant/automation/ledger.py` | append-only intent/consumption/reconciliation 账本 |
| `quant/automation/targets.py` | immutable snapshot schema、验证和指针 |
| `quant/automation/plans.py` | 快照到订单计划的纯函数转换 |
| `quant/automation/cycle.py` | profile 状态机和 fail-closed 编排 |
| `quant/data/sessions.py` | CN/HK/US 完成 session 和常规交易窗口 |
| `quant/data/fx_sessions.py` | `FX_NY17/v1` roll、DST 和 expected receipt |
| `quant/data/generations.py` | candidate、generation、CURRENT 和 bootstrap |
| `quant/data/gates.py` | 结构化行情/指数/FX 门禁 |
| `quant/broker/lb_paper.py` | OAuth-only paper SDK adapter |
| `scripts/trade_cycle.py` | 参数解析和调用 `cycle.py`，不承载业务逻辑 |

执行模块不得导入 `quant.strategies`、`scripts.predict*` 或任何选股入口。目标构建可依赖研究模块，broker/plans 不可依赖研究实现。

## 5. 可配置根目录与状态布局

所有根目录必须可注入，测试只能写 `tmp_path`：

```text
<data_root>/
  candidates/<run_id>/
  datasets/<dataset_key>/generations/<generation_id>/
  CURRENT.json

<state_root>/
  policy/execution-policy.json
  enrollment/oauth-enrollment.json
  targets/<market>/<snapshot_id>.json
  targets/<market>/CURRENT.json
  plans/<run_id>/<plan_id>.json
  intents/<intent_id>.json
  ledger/events/<sequence>-<event_hash>.json
  ledger/HEAD.json
  locks/automation.lock.meta.json

<report_root>/
  runs/<run_id>.json
```

约束：

- generation、snapshot、plan、intent 一旦发布即不可覆盖。
- 顶层 `CURRENT.json` 是稳定 `dataset_key -> generation_id` 的小型原子映射；v1 key 为 `equity-cn`、`equity-us`、`equity-hk`、`fx-cny`。读者每轮只解析一次并固定其 hash；单市场或多市场更新都必须复制未变映射，并用一次 batch CAS 原子替换整个指针。
- ledger 采用 immutable event 文件加原子 `HEAD.json`，事件含 sequence、前一事件 hash 和本事件 hash。`event_hash` 按第 6.1.1 节的白名单投影计算，`HEAD` 元数据不参与。先原子落事件，再以 compare-and-swap 切 HEAD；崩溃留下但未被 HEAD 引用的 orphan event 不得成为有效状态。损坏、断链或并发 HEAD 变化时 execute 失败。
- `generated_at`、`run_id` 等运行元数据不得参与内容身份 hash。
- 生产根目录默认 untracked；测试不得依赖用户目录或 D 盘。

## 6. Canonical 与原子 IO 契约

### 6.1 Canonical JSON

`canonical_json_bytes()` 必须：

- 字符串 Unicode NFC；对象 key 按 Unicode code point 排序；UTF-8 编码；无多余空白。
- 禁止 NaN/Infinity。
- 价格、金额、权重和 FX 先按 schema 量化为定长 `Decimal` 字符串。
- 时间统一为带 `Z` 的 UTC ISO-8601 微秒格式；session 为 `YYYY-MM-DD`。
- hash 输入包含 schema name 和 schema version。

### 6.1.1 身份投影（identity projection）

所有内容身份都必须先由该对象专用的、字段白名单式 `identity_projection_*()` 生成投影，再以带 schema name/version 的 `canonical_json_bytes()` 计算 SHA-256；禁止对完整持久化对象直接 hash，也禁止用“删除已知字段”的黑名单实现。投影不得包含待计算字段自身，否则形成自引用；不得包含 `run_id`、`generated_at`、`created_at`、`updated_at`、`written_at`、PID、hostname、临时/候选路径、文件名、日志位置等运行诊断元数据；不得包含可从投影重算的 count/sum/status cache、`client_request_id`、remark、指针 hash、manifest byte hash 等派生字段。用于承诺外部内容的叶子摘要（例如数据文件 canonical content SHA-256、文件 byte SHA-256、broker-read/quote evidence digest）是身份输入，不属于应排除的派生汇总；其嵌套 receipt 也必须先使用自己的稳定身份投影。

固定投影如下：

- `generation_id`：`schema_name/schema_version`、`dataset_key`、`code_commit`、稳定 source receipts、cutoff、universe receipt，以及按 `relative_path` 排序的文件承诺 `{relative_path, content_schema, canonical_content_sha256, byte_sha256, size}`。排除 `generation_id` 自身、`manifest_sha256` 和所有运行元数据/汇总字段。
- `snapshot_id`：`schema_name/schema_version`、market、model/universe/calendar identity、equity generation、input slice hash、phase anchor、rebalance/eligible session、execution window、target allocation、按 normalized symbol 排序的完整 weights、cash buffer weight。排除 `snapshot_id` 自身、lifecycle status、`weights_sum` 及所有运行/发布元数据。
- `plan_id`：`schema_name/schema_version`、`consumption_key`、enrollment identity hash、market/session、snapshot id、pinned generation identities、policy id/hash、broker-read 与 quote 叶子证据、稳定基准时点、`ttl_seconds`，以及排序后的 canonical order rows。排除 `plan_id` 自身、顶层 `pointer_hash`（仅作运行溯源）、`intent_id`、`client_request_id`、remark、`expires_at`（由稳定基准时点和 TTL 重算）、计划汇总/count 及运行元数据。
- `intent_id`：`schema_name/schema_version`、`consumption_key`、`plan_id`、稳定排序后的零基 ordinal 和该行 canonical order payload。排除 `intent_id` 自身、`client_request_id`、remark、broker order id、当前状态、重试/观测时间和运行元数据。
- `event_hash`：`schema_name/schema_version`、sequence、`prev_event_hash`、event type 和 canonical event body。排除 `event_hash` 自身、事件文件名、`HEAD.json` 的 hash/revision、写入时间、run/PID/host 及可重算摘要。

计算顺序固定为 generation/snapshot → consumption key → plan id → intent ids → ledger event hash；plan 投影不得含 intent id，避免 plan/intent 循环依赖。ID 命名的 immutable 对象若已存在必须复用原 bytes，不能为了更新运行元数据而覆盖；运行元数据写入 run manifest 或非权威 sidecar。读取时必须重新生成投影并核对持久化 ID。测试必须证明：修改任一排除字段不会改变 ID，修改任一身份字段会改变 ID，自身字段填入或篡改不会参与计算但会被读回校验拒绝。

### 6.2 Canonical 表格

表格 hash 必须包含固定列顺序、dtype、主键排序、缺失值独立 tag、int64/float64 明确字节序。禁止直接 hash gzip 文件字节或依赖 dict/DataFrame 当前顺序。

### 6.3 原子写

流程固定为：同目录临时文件 → 写入 → flush/fsync → 关闭 → 内容读回验证 → `os.replace()` → 父目录同步（平台支持时）。成功发布路径在全部 generation 安装完成后对顶层 `CURRENT.json` 做恰好一次 batch CAS；若该次切换后的读回校验失败，且 CAS 仍证明 CURRENT 指向本轮新 hash，允许最多一次有条件的补偿 CAS 回切旧指针。并发者已推进时禁止回切。异常清理只能删除本轮临时文件。

## 7. 日历、完成 bar 与执行窗口

### 7.1 交易所日历

锁定 `exchange_calendars==4.13.2`：

| 市场 | calendar | timezone |
|---|---|---|
| CN | `XSHG` | `Asia/Shanghai` |
| HK | `XHKG` | `Asia/Hong_Kong` |
| US | `XNYS` | `America/New_York` |

每轮以可注入且必须带时区的 `now_utc` 冻结 `expected_session`；naive datetime 直接拒绝。只有 calendar close 加 15 分钟后才算完成。初始化或查询失败时 data-close 和 open/execute 都退出 11，不发布任何指针；本方案没有 fallback。

`calendar_identity()` 不得读取当前时间或猜测“本轮相关 session”。调用方必须显式传入非空 `sessions`；实现将 session label 规范化为升序去重序列，并为每个 label 固定 `{session, open_utc, break_start_utc|null, break_end_utc|null, close_utc}` schedule receipt。identity projection 固定含 schema name/version、market、calendar name、`exchange_calendars` version、timezone、`close_buffer_minutes` 和上述有序 receipts。非法或非交易 session、空 sessions 或 naive 时间输入一律拒绝。data-close 传 `[expected_session]`；snapshot 构建和 open 重验均传 `[rebalance_session, eligible_execution_session]`，二者必须得到相同 identity。相同显式输入不得受墙钟影响；session 顺序不影响 hash，session、半日市、DST 或 close buffer 任一语义变化必须改变 hash。固定回归必须证明北京时间 `2026-10-23 21:35` 的 US `expected_session` 为 `2026-10-22`。

测试至少覆盖 DST 切换、周末、节假日、Black Friday 半日市以及 XSHG/XHKG 午休。个股和 benchmark 必须使用同一 session cutoff。

### 7.2 FX receipt 与独立刷新

FX 不跟随 CN/HK/US 股票日历，也不能用固定 `22:00Z` 忽略 DST。定义保守的版本化操作时钟 `FX_NY17/v1`：

- roll 使用 `America/New_York` 当地周一至周五 `17:00`；夏令时对应 `21:00Z`，冬令时对应 `22:00Z`。
- roll 后 30 分钟才可形成 expected receipt；周末的 expected label 保持最近周五。
- 该时钟是安全刷新契约，不宣称覆盖所有全球 FX 假日。数据源在假日没有新 bar 时必须阻断交易，不允许工作日 fallback 或前向填充。
- v1 来源为 Yahoo `CNY=X`（规范化为 USD/CNY）和 `HKDCNY=X`（规范化为 HKD/CNY）。
- 每行必须保留 `source_symbol`、`base_currency`、`quote_currency`、`source_timestamp_utc`、`receipt_label`、`close` 和 canonical row hash。
- `source_timestamp_utc` 晚于冻结 cutoff、货币方向不符、值非有限正数、缺少 expected receipt 或 hash 不符均失败。
- `receipt_label` 必须由版本化 provider adapter 根据 Yahoo bar timestamp、timezone metadata 与已审核真实 fixture 映射到 NY17 roll；禁止直接把 payload UTC date、下载日期或 expected label 填进去。真实 fixture 未确认映射时状态为 `BLOCKED_FX_LABEL_MAPPING`，execute 保持关闭。

新增独立 `fx-close` profile，建议北京时间 06:45 运行，只刷新 `fx-cny` dataset。HK/US open profile 冻结并读取最新 immutable FX receipt，要求 receipt label 严格等于当时 `FX_NY17/v1` 的 expected label；不满足即退出 11。这样不会拿前一日 16:45 的股票 data-close 结果冒充次日 HK 开盘前的最新 FX。测试覆盖夏/冬令时、周末、无 bar 假日 fail-closed、source timestamp 超 cutoff，以及 06:45 北京时间在两种 DST 状态下均已越过 30 分钟 buffer。

### 7.3 一次性执行窗口

US/HK 快照的 `eligible_execution_session` 是 `rebalance_session` 后的下一个对应市场 session。允许窗口是交易所本地时区的闭区间 `[regular_open + 5 分钟, regular_open + 90 分钟]`，必须同时满足 calendar 和 QuoteContext 市场状态为 regular trading。HK auction、US pre-market/after-hours 和任何未知状态均拒绝；DST 只由 calendar 决定。

窗口前返回 `NOOP_NOT_OPEN`；窗口后返回 `SKIPPED_MISSED_WINDOW`。二者都不得提交，后者写入 consumption ledger，未来不得补单。90 分钟窗口使北京时间 22:35 在美股夏令时和冬令时均可落入同一规则，但最终判断始终基于交易所时区，不基于固定北京时间。

## 8. Immutable data generation

### 8.1 Pointer、manifest 与 pinned reader

顶层指针 schema 固定为：

```json
{
  "schema_version": 1,
  "revision": 7,
  "datasets": {
    "equity-us": {
      "generation_id": "sha256:...",
      "manifest_sha256": "sha256:..."
    },
    "fx-cny": {
      "generation_id": "sha256:...",
      "manifest_sha256": "sha256:..."
    }
  }
}
```

equity generation 至少包含 `bars.csv.gz`、`index_bars.csv.gz`、`universe.json`、可选 `members.csv.gz` 和 `manifest.json`；FX generation 包含 `rates.csv.gz` 与 `manifest.json`。CSV 列/换行/浮点格式和 gzip `mtime=0` 固定。manifest 至少记录 dataset key、schema、generation id、code commit、source receipts、cutoff、universe receipt、每个文件的 canonical content hash、字节 SHA-256 和 size。`generation_id` 严格按第 6.1.1 节的白名单投影产生，不含自身、manifest byte hash 或运行元数据。

发布者进入 candidate 前冻结 `expected_current_sha256`。切换前再次比较，不相等则退出 13；最终 generation 目录若已存在，只能在 manifest 与每个文件 hash/size 完全一致时复用，否则按 hash collision 退出 13。成功路径只做一次 batch CAS；切指针后读回失败时，只能在 CURRENT 仍等于本轮新 hash 时做至多一次补偿 CAS 回切旧指针；若已有更新者推进，禁止覆盖并退出 13。

新增 `GenerationView`：一轮开始只解析一次顶层 CURRENT，pin `pointer_hash` 和所需 dataset generation。`quant/data/store.py`、`quant/data/panel.py` 与自动化调用必须显式接收该 view；即使运行中另一进程切换 CURRENT，本轮仍只读原 generation。automation 的 data root 必须由 CLI/cycle 显式注入，不得读取 `quant/config.py` 的全局 `CACHE_DIR`。legacy flat reader 改成显式 `LegacyFlatStore`/research-only 路径，automation 和 execute 调用图不得调用。

### 8.2 Producer 契约

所有正式 producer 必须返回结构化结果或写入指定 candidate root，不得直接更新正式缓存。范围至少包括：

- `scripts/fetch_tx.py`
- `scripts/fetch_baostock_fallback.py`
- `scripts/fetch_baostock_full.py`
- `scripts/fetch_market.py`
- `scripts/fetch_data.py`
- `scripts/fetch_cn500.py`
- 仍会合并 shard 的 `scripts/fetch_parallel.py` 和相关调用
- `quant/data/store.py` 中现有全局 `save_*`/`upsert_*` 写入口

旧脚本若保留为兼容 CLI，默认只能 candidate/no-publish；不得在 canonical 调度之外切换 `CURRENT.json`。`fetch_market --market sg|crypto|all` 和 `fetch_cn500.py` 在 v1 明确为 research-only，默认 no-publish，不能写 v1 dataset pointer。异常、空结果或部分失败必须返回非零；`fetch_parallel.py` 任一 child 非零时不得 merge，并原样返回非零。

增量 candidate 必须从 pinned generation 合并，且 frozen inventory 的每个请求都有成功或明确的 IPO/停牌/非 eligible 结构化 receipt；网络、空响应或解析失败一律使整 bundle 失败，不能用 90% 覆盖率掩盖抓取失败。BaoStock 全量前复权刷新必须为 frozen eligible inventory 重建完整历史，禁止把成功的新历史与失败标的的旧历史拼接；任何未被预先排除的标的失败都不发布。

### 8.3 Candidate bundle

一个股票 bundle 包含本轮请求市场的个股、benchmark、universe receipt 和 source manifest；FX candidate 由独立 `fx-close` bundle 产生。`data-close` 在抓取前只冻结一次 `expected_current_sha256`，为本轮全部 requested markets 完成 candidate、门禁和 immutable generation 组装；只有全部市场 PASS 后，才基于冻结的旧 CURRENT 复制未变映射、一次性应用全部 `dataset_key -> generation ref` 更新，并对顶层 `CURRENT.json` 执行恰好一次 CAS。禁止按市场循环切指针，也禁止一个市场成功后让另一个市场失败。任一 candidate、gate、install、readback 或 CAS 失败时，旧 CURRENT 和它引用的全部 generation bytes 必须不变；CAS 前已物化的 content-addressed generation 最多只能成为未被任何指针引用的 orphan，不得被视为已发布，也不得为清理 orphan 覆盖并发更新者。

门禁至少验证：

- 固定 schema/dtype、主键唯一、代码格式和市场后缀。
- `max(date) == expected_session`，未来/未完成 bar 在计算因子前剔除。
- OHLC 有限且为正，`low <= open/close <= high`，`volume >= 0`。
- benchmark 非空且与个股 cutoff 一致。
- 覆盖率分母是本轮冻结的 eligible universe；CN/US/HK 最低 90%。
- 已存在 validated snapshot 或准备 open execution 时，快照目标证券覆盖率必须 100%。首次 legacy bootstrap 尚无 snapshot 时只执行 data-only base gate；在 snapshot 建成并通过该门禁前禁止 execute。
- 历史长度不能无解释缩短。
- `fx-cny` dataset 满足第 7.2 节全部规则；股票 bundle 不把旧 FX 一起复制成“新鲜”。

v1 的 universe 与 benchmark 固定如下；任何变化都必须改变 version hash 并经过显式模型迁移：

| 市场 | eligible universe | benchmark | 最低覆盖率 |
|---|---|---|---:|
| CN | 对应历史时点的沪深 300 成分 | `000300` | 90% |
| US | 版本化 `US_UNIVERSE` 静态名单 | `SPY` | 90% |
| HK | 版本化 `HK_UNIVERSE` 静态名单 | `^HSI` | 90% |

US/HK 静态名单的幸存者偏差必须继续披露，不能把工程门禁 PASS 表述为研究无偏。

### 8.4 BaoStock 原子性

49/50/51/99/100/101 只股票和第 50 只故障都必须测试。第 50 只失败、chunk 写入中断、candidate 校验失败或并发 `CURRENT.json` 变化时，旧 CURRENT、它引用的 generation 目录及正式旧文件的 bytes/hash/size/mtime_ns 必须全部不变；允许留下未引用的 content-addressed orphan，但绝不能覆盖或删除并发引用内容。成功时只能切换一次指针，且名称字段不能被空值覆盖。

### 8.5 Legacy bootstrap

提供一次性 source-preserving bootstrap：只读 legacy flat cache → 复制到 candidate → 运行 data-only base gate → 创建初始 equity generation → 原子写 `CURRENT.json`，源文件保持 bytes/hash/mtime 不变。由于初始阶段尚无 validated snapshot，目标证券 100% 门禁暂不可运行；该 generation 标为 `BOOTSTRAPPED_UNEXECUTABLE`，绝不能授权 execute。FX 不从 legacy flat cache 假造，必须由 `fx-close` 生成。

P3 获得 lead 审核的 golden、生成 validated snapshot 并对目标证券运行 100% 覆盖门禁后，相关 equity generation 才可被 execute 引用。bootstrap 失败时不产生有效指针；切换后所有 canonical 读者和 execute 禁止回退 flat cache。

## 9. 研究 schedule 与目标快照

### 9.1 市场范围

- US/HK：进入 v1 immutable snapshot 和 broker execute。
- CN：继续研究用途，必须保留 `PhaseEnsembleStrategy(master_daily=True)` 与四个成员相位的现有语义；不强行套用单一 rebalance 公式，不进入 v1 broker execute。

### 9.2 Shared schedule

从研究实现抽出共享、可纯函数测试的 schedule。US/HK 的 `phase_anchor`、`warmup`、`rebalance_days`、universe、preset、因子、排序和过滤必须与回测一致。批准值存入版本化 `models/execution_v1.json` 并纳入 `model_version`；不得从当前 Panel 起点动态猜 anchor。缺少 lead 审核的 anchor 时只能返回 `BLOCKED_GOLDEN_MISSING`，不得发布 unverified execute snapshot。

审查副本没有真实 data/cache，因此执行代理只实现 fixture exporter、schema 和 synthetic tests。exporter 必须按研究 `weight_func` 重放并记录 market、源文件/generation fingerprint、code commit、model/universe/calendar identity、phase anchor、warmup、rebalance days、preset、rebalance/eligible session、完整 canonical weights 和 hash；不得用最后一天直接调用 `target_weights()` 冒充回测口径。真实 US/HK golden 必须由 Codex lead 从只读运行数据导出、脱敏并审核后加入。旧设计中的 US `2026-10-20`、HK `2026-10-07` 只能作为待核对候选，不能在没有真实数据的情况下伪造 PASS。

CN 需要独立 golden 差分，证明重构前后每日主目标和成员更新日一致；在该证据到位前 CN schedule 状态为 `BLOCKED_GOLDEN_MISSING`，但不影响“CN 不可执行”的安全状态。

### 9.3 Snapshot

每市场使用 immutable 文件和原子 `CURRENT.json` 指针。schema 至少含：

```json
{
  "schema_version": 1,
  "snapshot_id": "sha256:...",
  "status": "validated",
  "market": "us",
  "model_version": "sha256:...",
  "equity_generation": "sha256:...",
  "input_slice_hash": "sha256:...",
  "universe_version": "sha256:...",
  "calendar_identity": "sha256:...",
  "phase_anchor": "YYYY-MM-DD",
  "rebalance_session": "YYYY-MM-DD",
  "eligible_execution_session": "YYYY-MM-DD",
  "execution_window": {"open_offset_min": 5, "close_offset_min": 90},
  "target_allocation": "0.30000000",
  "weights": {"AAPL.US": "0.28500000"},
  "weights_sum": "0.28500000",
  "cash_buffer_weight": "0.01500000"
}
```

示例只展示一个 symbol，不代表实际持仓数。实现必须 canonical 求和并满足所有权重非负、`weights_sum + cash_buffer_weight == target_allocation` 且 `target_allocation <= 1`；禁止硬编码示例数值。`snapshot_id` 严格按第 6.1.1 节的白名单投影产生，排除自身、lifecycle status、`weights_sum` 和全部运行/发布元数据。非调仓日 snapshot bytes/mtime 必须不变。模型、universe 或代码版本在周期内改变时阻断 execute，必须显式迁移，不能覆盖旧快照。

## 10. Policy、ownership、计划与 ledger

### 10.1 Execution policy

本机未跟踪 policy 的 `policy_id` 是 canonical policy schema/version 的 SHA-256，不得使用文件名或 run id。必填字段固定为：批准 commit、允许市场、`max_order_cny`、`max_market_cny`、`max_run_cny`、`max_day_cny`、`max_orders_run`、`max_orders_day`、`rebalance_tolerance_bps`、`min_cash_buffer_bps`、`min_order_cny`、`max_quote_age_seconds`、`plan_ttl_seconds`、`max_slippage_bps`、允许订单类型、允许窗口、enrollment id、过期时间。缺字段默认禁用/0，命令行只能收紧不能放宽。

enrollment 使用本地随机 UUID 与用户确认记录，不把 token、member id 或账户金额写入文件，也不声称它能从 token 密码学绑定券商账户。longbridge 5.2.2 的 `AccountBalance` 没有可依赖的稳定 account id，因此禁止从余额响应伪造账户身份。`client_id`、resolved cache path 或人工登记 generation 变化时 enrollment 立即失效并要求用户重新确认；SDK 在同一路径正常刷新 token 不根据文件 hash/mtime 自动换 enrollment id。enrollment 的任何变化都不得改变 consumption key、清空 ledger、解除 missed/unknown/partial 状态或产生第二次执行机会。

### 10.2 Ownership

每个可操作仓位和挂单必须有策略 ownership receipt。迁移时只登记用户明确批准的模拟盘资产；未登记资产只读可见但不可操作。相关 symbol 出现人工仓位或未知挂单时，整份计划阻断。

### 10.3 Plan

open profile 开始时只解析一次顶层 CURRENT，pin `{pointer_hash, equity-<market> generation, fx-cny generation}`。snapshot 引用其历史 `equity_generation`，计划同时记录 snapshot equity generation、当前 gate generation、FX generation 和 pointer hash；运行中 CURRENT 变化不影响本轮。

`plans.py` 是纯函数：validated snapshot + pinned generation view + broker read snapshot + policy → immutable plan。先计算整份计划，再统一验证：

- 报价新鲜度、regular session、currency、lot、tick。
- 按 USD/HKD 分币种现金，不把人工仓位释放价值计入预算。
- FX 使用 pinned `fx-cny` generation 的 USD/CNY、HKD/CNY receipt。
- 已有 owned 仓位、owned working orders 和计划数量的净额关系。
- 所有限额按 CNY 等值以 schema 指定 Decimal 精度保守舍入；外币 exposure 使用不利方向 FX。验证单笔、单市场、本轮、当日累计名义金额、订单数、现金 buffer、rebalance tolerance 和滑点上限。
- v1 只允许 `LO + Day + Buy/Sell`；未知 SDK enum 或 schema 失败。

`plan_id` 按第 6.1.1 节对整份排序后计划的白名单投影计算，包含 consumption key、enrollment identity hash、policy hash、quote evidence、generation identities、稳定基准时点、TTL 和 canonical order rows，但不含 `plan_id`、`intent_id`、`client_request_id`、remark 或汇总字段。先算 plan id，再为每个排序后订单行计算 `intent_id`，从而避免 plan/intent 循环依赖。每个订单行的 canonical payload 至少含 enrollment identity hash、`paper`、execution session、snapshot id、normalized symbol、side、quantity、order type、limit price 和 TIF，不含 run id、token 或可变 policy 字段。US symbol 规范为大写 `.US`，HK 内部规范为四位数字零填充 `.HK`，broker 输入输出匹配前统一规范化。

`client_request_id = "qr-" + intent_id 前 24 个 hex`，remark 使用固定 ASCII 前缀与截断 hash，长度和字符集由测试锁定。计划生成后、绑定 consumption key 前必须刷新并核对 quote；若价格/时间越界则丢弃 preview 并重新计算。key 绑定后 plan 不可更换；每笔提交前只重验 session、quote freshness 和 drift，任何变化超过 policy 立即 fail-stop，剩余 intent 标记未尝试，禁止用同一 key 生成新 plan。

### 10.4 Consumption ledger

`consumption_key = sha256_id(canonical_json_bytes({"environment":"paper","market":market,"snapshot_id":snapshot_id,"eligible_execution_session":eligible_execution_session}, schema_name="automation.consumption_key", schema_version=1))`。schema name/version 只通过 `canonical_json_bytes` 的 keyword-only 参数注入，不在 value 中重复。`enrollment_id`、enrollment generation/attestation、client id/cache path、policy、commit 和 token receipt 一律不参与该 key；否则重新登记、轮换凭证或修改 policy 就能为同一目标和窗口制造第二次执行机会。enrollment identity 必须进入 plan identity、执行门禁和首次绑定事件，确保已绑定计划不能换 enrollment 提交，但它的变化绝不能改变 key、清空 ledger、解除 `SKIPPED_MISSED_WINDOW` 或允许第二份 plan。一个 key 只能原子绑定一份 execute `plan_id`；已绑定、已消费、unknown、partial 或 missed 后，即使重新 enrollment 也只能返回既有状态。合法迁移只能产生经审核的新 snapshot，或走未来单独设计的 append-only 人工裁决流程，禁止删除或改写旧 ledger。

dry-run 只写 preview plan 和 run manifest，不 reserve key、不创建 authoritative intent，也不写消费终态；同一窗口随后仍可执行一次。execute 在第一笔 broker write 前用一个原子 ledger event 绑定 key、整份 plan 和全部 `PREPARED` intent，随后为每个订单追加状态：

```text
PREPARED -> SUBMITTING -> WORKING -> PARTIALLY_FILLED -> FILLED
                         |          -> CANCELED / EXPIRED / REJECTED / UNKNOWN
                         -> FILLED / REJECTED / UNKNOWN
```

必须在调用 SDK 前持久化 `SUBMITTING`。同一未中断 run 内，只有当前 intent 已唯一核验为 `WORKING` 或 `FILLED`、全部门禁仍通过时，才可按原 plan 顺序尝试下一 intent。只要任一 intent 到过 `SUBMITTING` 后 run 崩溃、退出或进入 `UNKNOWN`、`REJECTED`、`CANCELED`、`EXPIRED`、`PARTIALLY_FILLED`/run 级 `PARTIAL_EXECUTION`，任何后续 run 都只能对整份 plan 做有界只读 reconciliation；其他仍为 `PREPARED` 的 sibling 必须永久转为 `ABORTED_UNSUBMITTED`，不得自动提交。只有整份 plan 从未有任何 intent 进入 `SUBMITTING` 时，后续 run 才可在同 plan、TTL 未过期且门禁重验通过后开始首笔；TTL 过期则全部未提交 intent 永久记 `EXPIRED_UNSUBMITTED`。不能换 plan 或 enrollment 绕过 key。

## 11. Longbridge OAuth-only paper adapter

生产 adapter 必须：

1. 使用 `OAuthBuilder` 和 `Config.from_oauth(oauth, enable_papertrading=True)`。
2. 调度器先通过可注入 probe 检查 SDK 5.2.2 的 token cache；默认缓存位于 `~/.longbridge/openapi/tokens/<client_id>`。生产 enrollment identity 固定为 `client_id + resolved cache path + 用户登记的 enrollment_generation/attestation`，不使用 token 文件 hash/mtime，也不声称密码学账户绑定；该 identity 进入 plan 和门禁，但不得进入或改变 consumption key。
3. 即使缓存存在，也必须按 longbridge 5.2.2 的 positional-only 签名调用 `OAuthBuilder.build(reject_interactive_callback)`；callback 一旦收到 URL 就立即抛出 `AUTH_REQUIRED`。缓存缺失、失效或过期都不能打开浏览器或等待人工回调。
4. 不使用 `refresh_access_token`、Legacy API key、Longbridge CLI 或现有 `lb_bridge.py` 执行链；旧 CLI 的 `order buy/sell/cancel/replace` 命令均不属于 canonical adapter 的可用接口。P0 必须删除 Legacy `lb_sdk.trade_context()`/`paper_config()` 公开 factory；仅保留不泄露 context 的精确 allowlist 只读 wrapper，任何非 allowlist trade method 都在读取凭证或导入 SDK 前阻断。
5. 一轮恰好创建一个 `TradeContext` 和一个 `QuoteContext`，二者使用同一 config；前者传给全部账户/订单操作，后者传给报价/市场状态/证券元数据操作。longbridge 5.2.2 未保证 context 有 `close()`，adapter 生命周期只释放引用；若未来能力探测到显式 close，可幂等调用，测试不得要求不存在的 SDK API。
6. 不复用当前 `lb_sdk.submit_order` 的 `SubmitOrderOptions(**kwargs)` 兼容代码；按 longbridge 5.2.2 的 `TradeContext.submit_order(symbol, order_type, side, submitted_quantity, time_in_force, submitted_price=..., remark=..., client_request_id=...)` 实际签名传参。
7. 缺 trade permission、paper userinfo 校验失败、响应 schema 缺字段或 order id 不可验证时 fail-closed。
8. TradeContext 只读面固定为 `account_balance`、`stock_positions`、当前 session 的 `today_orders/history_orders` 和必要的 `order_detail`；必须完整处理分页、严格 schema 和 session 过滤。QuoteContext 只读面固定为 `quote`、`static_info`、`trading_session`。任一未知字段/enum、漏页或查询异常均 fail-closed。
9. post-check 必须集成在 `cycle.py` 同一进程并复用同一 TradeContext；独立 `post_daily_check.py` 只能读取本地 manifest/ledger，不再查询券商。
10. 订单匹配优先 exact `client_request_id`；若响应不提供该字段，只允许 exact deterministic remark + normalized symbol + side + quantity + order type + price + execution session。零匹配、多匹配、缺 order id 或 schema 错误均退出 22。状态映射固定为：`New/NotReported -> WORKING`；`PartialFilled -> PARTIALLY_FILLED`；`Filled -> FILLED`，并在 post-check 唯一核验后记录 `RECONCILED`；`Canceled/Expired/Rejected` 为终态失败。`PARTIALLY_FILLED` 一旦出现，本 run 不再提交 sibling，后续 run 只能只读 reconciliation，并把其他 `PREPARED` sibling 永久转为 `ABORTED_UNSUBMITTED`。历史其他 session 的订单不得参与匹配。旧 `order buy/sell/cancel/replace` 命令无论是否带 execute token、是否声称仅作 preview 都必须由 legacy guard 前置阻断；只允许普通 `order` 列表查询，以及完全基于该列表在本地计算和显示的 cancel-intent preview，canonical adapter 不提供撤单写入口。

离线实现只能用 fake OAuth/fake context。真实 OAuth 与只读 paper 探针由用户/Codex lead 在后续单独执行。

## 12. Profiles、状态和退出码

每轮 `run_id` 是随机 UUID，仅用于关联日志，不参与 generation/snapshot/plan/intent 身份。manifest 在取得锁后立即以 `STARTED` 原子创建，固定 schema：

```json
{
  "schema_name": "automation.run_manifest",
  "schema_version": 1,
  "run_id": "uuid",
  "profile": "us-open",
  "mode": "dry-run",
  "requested_markets": ["us"],
  "code_commit": "git-sha",
  "started_at": "UTC",
  "finished_at": null,
  "pinned": {"pointer_sha256": "sha256:...", "datasets": {}},
  "snapshot_id": null,
  "plan_id": null,
  "stages": [],
  "final_status": "STARTED",
  "exit_code": null,
  "broker_write_count": 0
}
```

stage record 固定含 stage、status、started/finished、input/output hashes 和脱敏 error code，不含原始异常中的凭证或账户值。每次更新都走同目录 temp+fsync+replace；最终 manifest 的状态必须与退出码一致。崩溃遗留 `STARTED` 在下轮标记 `ABORTED_PREVIOUS_RUN`，不能静默覆盖。

### 12.1 `data-close`

```text
OS lock -> manifest -> freeze clocks/sessions -> candidate fetch
-> cutoff -> structured gate -> generation publish/readback
-> shared research schedule -> snapshot candidate/validation/publish
-> final manifest -> unlock
```

`--mode dry-run` 只表示券商写为零，不代表本地数据禁止发布。离线测试必须额外使用临时根目录、fake source；需要只验证而不切指针时使用明确 `--no-publish`。

### 12.2 `fx-close`

```text
OS lock -> manifest -> freeze NY17 expected receipt/cutoff
-> FX candidate fetch -> provider timestamp mapping -> structured gate
-> publish only fx-cny mapping/readback -> final manifest -> unlock
```

真实 provider fixture 尚未由 lead 审核时，production 状态保持 `BLOCKED_FX_LABEL_MAPPING`；synthetic E2E 只能证明控制流，不能解除该阻塞。

### 12.3 `hk-open` / `us-open`

```text
OS lock -> manifest -> policy/commit/generation/snapshot validation
-> session/window -> token cache -> one OAuth paper Config
-> one TradeContext + one QuoteContext
-> bounded reconciliation -> broker reads -> ownership -> immutable plan/intent
-> whole-plan preflight -> connected read-only or fail-stop submit
-> strict reconciliation/post-check -> final manifest -> unlock
```

普通 `--mode dry-run` 在创建 OAuth config 前结束，只用显式、已哈希的 fixture 输出 preview。只有显式 `--mode connected-dry-run` 时，open profile 才创建真实双 context 并完成只读查询/post-check 模拟，manifest 状态记为 `CONNECTED_DRY_RUN_COMPLETE`，broker write 调用严格为 0。生产 CLI 不暴露 fake 注入选项；单元/E2E 可在内部 factory 边界注入 fake 来覆盖该状态机。`connected-dry-run` 不得用于 data profile；只有 `--mode execute` 可绑定 consumption key 并提交。

### 12.4 退出码

| code | 含义 |
|---:|---|
| 0 | `SUCCESS_RECONCILED`、`DRY_RUN_COMPLETE`、`CONNECTED_DRY_RUN_COMPLETE` 或预期 no-op；manifest 必须区分具体状态 |
| 3 | `ALREADY_RUNNING` |
| 10 | 配置或参数错误 |
| 11 | 日历、抓取、数据或门禁失败 |
| 12 | snapshot 或 plan 无效 |
| 13 | hash、指针、ledger、本地状态完整性失败，或 `SKIPPED_MISSED_WINDOW` 告警 |
| 20 | policy、enrollment、OAuth cache、paper guard 或权限阻断 |
| 21 | broker 只读查询、ownership 或提交前预检失败，且尚未提交 |
| 22 | 已进入写阶段后的 partial/unknown/reconciliation 失败 |
| 30 | 未分类内部错误 |

预期 no-op 包括 `NOOP_NOT_DUE`、`NOOP_NOT_OPEN`、`SKIPPED_MARKET_CLOSED`、`NOOP_ALREADY_CONSUMED`，统一返回 0，避免 Windows Task Scheduler 把正常休市记为失败。`SKIPPED_MISSED_WINDOW` 是需告警事件，返回 13。

PowerShell wrapper 必须只调用一次 Python 入口，并原样返回上述 code；argparse/参数错误也要规范化为 10，不泄漏默认退出码 2。

## 13. 隐私、研究证据与文档

1. `.gitignore` 使用根锚定 `/state/`、`/logs/`、`/reports/`、`/snapshots/`、`/data/cache/`，不得屏蔽 `docs/superpowers/reports/`。
2. 被 `26e0c87` 一并删除的研究 JSON 只能由 Codex lead 在隔离位置审查和脱敏，并以 SHA-256 allowlist 交付安全证据包；执行代理不得浏览可能含账户/订单/资产值的历史报告。获批文件再迁入 `artifacts/research/`，broker/post-check 运行报告继续 untracked。
3. `README.md`、`models/model_v4.json`、`docs/HANDOFF.md`、`docs/longbridge-setup.md` 只能写有证据的正式指标和当前认证架构。证据缺失时明确 `PENDING`，禁止编造数字。
4. 当前树做 secret/account/order/asset 扫描；历史清洗另案，完成前禁止公开仓库。

## 14. CI 与测试

CI 必须：

- 删除 `pip install -r requirements.txt || ...` fallback。
- 锁定 `exchange_calendars==4.13.2`。
- 至少覆盖 Python 3.12/3.13；OS lock 至少有一个 Windows job。
- 执行 `python -m compileall -q quant scripts`、`python -m pytest -q`、`git diff --check`。
- 所有网络和 broker 接口均 fake；CI 不需要 secret。

故障注入至少覆盖：

- candidate 下载/解析/门禁/发布失败，CURRENT 不变。
- pointer 并发变化、hash 不符、临时文件残留。
- DST、半日市、节假日、未完成/未来 bar、陈旧 FX。
- snapshot 缺失/损坏/过期、非调仓日不重写、错过窗口不补单。
- 未登记仓位、未知挂单、现金不足、lot/tick 错误、报价陈旧。
- OAuth cache 缺失、paper guard 失败、trade permission 缺失。
- submit 前失败调用数为零；submit 超时后不重试；post-check 查询失败非零。
- 日志和 manifest 不出现 canary secret、member id、完整 order id 或精确资产。

## 15. 阶段门禁与完成定义

实施顺序固定为 P0 → P1 → P2 → P3 → P4 → P5 → P6 → P7。每阶段使用独立提交，运行定向测试、全套测试、compileall 和 `git diff --check`，然后停止等待 Codex 审核。审核未通过不得进入下一阶段。总路线图只定义阶段契约；当前唯一批准的逐步施工单是 `docs/superpowers/plans/2026-10-24-quant-safe-automation-plan-b-p0.md`。P1–P7 在对应的 checkbox/TDD execution plan 经 Codex 提交并批准前统一返回 `BLOCKED_PHASE_PLAN_MISSING`，执行代理不得自行补全。

DeepSeek 最多可以声明：`OFFLINE_IMPLEMENTATION_CANDIDATE`。

以下状态只有用户/Codex lead 可在额外证据后声明：

- `GOLDEN_VERIFIED`
- `PAPER_READ_READY`
- `PAPER_EXECUTE_READY`
- `CANARY_PASSED`
- `SCHEDULER_ENABLED`

在 P7 离线候选通过后仍必须依次完成：真实 golden 审核、发布到运行副本、真实 OAuth 只读探针、人工批准的限额 canary、再次人工批准后创建/修改/启用任务。任一步失败，任务继续 `Disabled`。
