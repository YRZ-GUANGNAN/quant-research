# 方案 B 安全自动化 Master Rollout Plan

> **For agentic workers:** 本文是 P0–P7 的总路线图和验收契约，不是允许一次性执行全部阶段的逐行施工单。任何阶段都必须先有 Codex 批准的独立 execution plan；当前唯一批准的施工单是 `docs/superpowers/plans/2026-10-24-quant-safe-automation-plan-b-p0.md`。任何失败立即停止，不得越过阶段门禁。

**Goal:** 在不启用定时任务、不接触真实凭证或运行数据的前提下，分阶段构建默认离线、数据原子发布、目标可追溯且模拟盘执行 fail-closed 的统一自动化链。

**Architecture:** 用单一 `trade_cycle.py` 编排 data、FX 与 open profiles；数据通过 candidate/generation/CURRENT 原子发布，执行只消费 validated immutable snapshot。Longbridge 路径只允许同一 OAuth paper config 构造的一对 `TradeContext`/`QuoteContext`，并以 policy、ownership、ledger 和严格 reconciliation 约束。

**Tech Stack:** Python 3.12/3.13、pytest、PowerShell、`exchange_calendars==4.13.2`、longbridge 5.2.2、Git。

---

> 执行对象：DeepSeek 或其他实现代理
>
> 权威设计：`docs/superpowers/specs/2026-10-24-quant-safe-automation-plan-b-design.md`
>
> 已审核代码基线：`286e9bacfd6e5d080bee3f003be51b02d39dccc6`
>
> 批准起点：Git tag `plan-b-approved-20261024`
>
> 初次授权范围：**只做 P0，提交后停止，等待 Codex 审核**
>
> P0 逐步施工单：`docs/superpowers/plans/2026-10-24-quant-safe-automation-plan-b-p0.md`

## 0. 执行协议

### 0.1 开工前检查

依次执行并记录输出：

```powershell
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
git rev-parse "plan-b-approved-20261024^{commit}"
git diff --name-only 286e9bacfd6e5d080bee3f003be51b02d39dccc6..HEAD
python --version
python -m pytest -q
```

只有满足以下条件才能开工：

1. 工作树干净。
2. `HEAD == origin/main == plan-b-approved-20261024^{commit}`。
3. `286e9ba..HEAD` 只有方案 B 文档和旧文档的 superseded 提示，没有代码改动。
4. 基线全套测试通过；若依赖缺失，只报告准确错误，不使用 fallback 安装掩盖失败。

任何条件不满足都停止，不要 rebase、reset、stash、覆盖或删除用户文件。

### 0.2 分支与提交

从批准 tag 创建本地 `deepseek/plan-b` 工作分支。执行代理不得 fetch/pull/push、不得 force push，也不得合并到 `main`。每个下列提交边界都是独立本地 commit；每阶段结束必须停止等待 Codex 审核，由用户/Codex 决定后续远端操作。

提交信息默认使用任务书给出的中文文案。不要把多个阶段压成一个提交，也不要用后续提交修饰前一阶段的虚假 PASS。

### 0.3 绝对禁止

执行代理在 P0–P7 全程不得：

- 读取 `.env`、OAuth token、API key、真实账户/订单/资产文件。
- 调用 Longbridge、Yahoo、BaoStock、腾讯、Git remote、包索引或其他网络服务。
- 运行 `scripts/lb_oauth.py` 或创建真实 `TradeContext`/`QuoteContext`。
- 读取、刷新或修改 `D:\3314\quant-research` 及其数据。
- 查询、创建、修改或启用 Windows 定时任务。
- 执行任何真实或模拟券商写操作。
- 将仓库改为 public、重写 Git 历史、force push。
- 递归移除 `state/` 根目录、ledger、policy 或 enrollment。
- 递归枚举或搜索整仓、运行 `git grep`，或浏览 `docs/journal/**`、未获批分析/报告文件及 Git 历史/blob。

所有测试只能使用 `tmp_path`、fake source、fake OAuth/context、固定时钟和 synthetic fixture。若实现需要真实证据，返回明确 `BLOCKED_*`，不要伪造 PASS。

### 0.3.1 P0 精确读取 allowlist

首次授权只允许人工直接读取以下路径；未列出的源码、文档、报告和 Git 对象均禁止打开。`pytest`、`compileall` 和被测 Python import 可以按命令正常读取其依赖，但不得以排障为由人工扩大浏览范围：

- 完整读取三份方案 B 权威文档：
  - `docs/superpowers/specs/2026-10-24-quant-safe-automation-plan-b-design.md`
  - `docs/superpowers/plans/2026-10-24-quant-safe-automation-plan-b.md`
  - `docs/superpowers/plans/2026-10-24-quant-safe-automation-plan-b-p0.md`
- 完整读取 P0 输入文件：`.gitignore`、`quant/broker/lb_sdk.py`、`quant/broker/lb_bridge.py`、`scripts/lb_order_map.py`、`scripts/lb_rebalance.py`、`scripts/lb_fix_hkd.py`、`scripts/lb_reconcile.py`、`scripts/daily_run.ps1`、`scripts/us_open_run.ps1`。
- 创建后可完整读取 P0 输出文件：`quant/broker/legacy_guard.py`、`tests/test_repository_contracts.py`、`tests/test_legacy_execute_killswitch.py`、`tests/test_legacy_wrappers.py`。
- 以下五份历史文档只能顺序读取顶部 12 行以验证 superseded banner，禁止一次性读取全文：`docs/analysis/external-audit-2026-10.md`、`docs/longbridge-setup.md`、`docs/superpowers/specs/2026-10-24-quant-safe-automation-design.md`、`docs/superpowers/plans/2026-10-24-quant-safe-automation-deepseek-handoff.md`、`docs/superpowers/reviews/2026-10-24-lead-review.md`。

若施工必须人工查看 allowlist 外文件，立即报告 `BLOCKED_FILE_SCOPE_MISSING` 并停止；不得自行补充 allowlist。

### 0.4 每个提交的固定验证

每个提交前都运行：

```powershell
python -m pytest -q
python -m compileall -q quant scripts
git diff --check
```

每个独立 execution plan 还必须给出无占位符的定向测试命令、预期失败和预期通过结果。每条命令都在交付报告中记录命令、退出码和结果。失败时停止；不得继续后续任务。

### 0.5 阶段施工单门禁

本文 P1–P7 只定义范围、依赖、接口方向和阶段出口。进入任一阶段前，Codex 必须另行提交并批准符合以下条件的逐步 execution plan：使用 checkbox；每步只做一个动作；先写失败测试并记录预期失败，再写最小实现、运行定向测试、运行固定验证并提交；列出精确 Read/Create/Modify/Test 路径，其中 Read 必须逐文件注明完整可读或允许的行范围，目录/glob 不构成授权；同时列出关键接口/schema、命令、退出码和 Expected 输出。缺少对应施工单时返回 `BLOCKED_PHASE_PLAN_MISSING` 并停止，不得按本总路线图自行补全实现细节。

---

## P0：契约统一与旧执行入口止血

**当前唯一获准实施的阶段。P0 完成后必须停止。**

### P0-A：仓库契约和忽略规则

**目标文件：**

- `.gitignore`
- `tests/test_repository_contracts.py`
- 验证旧设计/交接/评审文档以及 `docs/longbridge-setup.md`、`docs/analysis/external-audit-2026-10.md` 已有醒目的 historical/superseded 指针；若批准 tag 中缺失，只补 banner，不改方案 B 权威内容

**实现要求：**

1. 整理重复 ignore 条目，使用根锚定：`/state/`、`/logs/`、`/reports/`、`/snapshots/`、`/data/cache/`。
2. 保留 `.env`、Python cache、虚拟环境等现有安全忽略。
3. 自动测试证明 `docs/superpowers/reports/probe.md` 不被忽略，而 `/reports/probe.json` 被忽略。
4. 文档契约测试只在两份权威方案 B 文档中验证：metadata 的代码基线严格等于完整 `286e9bacfd6e5d080bee3f003be51b02d39dccc6`；CLI 模式只使用 `--mode dry-run|connected-dry-run|execute`；state 保留规则和 OAuth SDK-only 规则存在且无肯定式 Legacy/CLI 执行建议。五份旧文档只校验顶部 historical/superseded banner，不扫描其历史正文来决定当前契约。
5. 不删除历史研究产物；安全研究报告迁移留到 P6。

**验证：**

```powershell
python -m pytest tests/test_repository_contracts.py -q
```

**提交：** `chore: 统一方案B仓库安全契约`

### P0-B：旧 broker write kill-switch

**目标文件：**

- `scripts/lb_order_map.py`
- `scripts/lb_rebalance.py`
- `scripts/lb_fix_hkd.py`
- `scripts/lb_reconcile.py`
- `quant/broker/lb_sdk.py`
- `quant/broker/lb_bridge.py`
- `quant/broker/legacy_guard.py`
- `tests/test_legacy_execute_killswitch.py`

**实现要求：**

1. 上述旧 CLI 入口收到任何 execute/撤单/实际写入意图时，在网络、CLI、SDK context、行情或账户调用前返回退出码 20；必须覆盖 `--execute`、`--execute=...` 以及 argparse 可能接受的 `--e`/`--ex`/`--exec` 前缀。
2. `lb_order_map.py`、`lb_rebalance.py`、`lb_fix_hkd.py` 的 `--execute` 明确输出 `BLOCKED_LEGACY_EXECUTE`。
3. `lb_reconcile.py` 默认只读 dry-run；旧 CLI cancel 路径不可达，任何请求写入都返回 20。
4. 保留纯本地计划能力时，输出必须明确仅为 legacy preview，不得宣称 canonical plan。
5. 不以 `LB_ACCOUNT_KIND=paper` 作为授权依据。
6. 所有 parser 使用 `allow_abbrev=False`，同时在 parse 前检查原始 token、parse 后复核 `args.execute`；`lb_fix_hkd.py`、`lb_reconcile.py` 在导入 `quant.broker.legacy_guard` 前增加仓库根 `sys.path` bootstrap。
7. 测试 monkeypatch `subprocess.run/Popen`、`load_env`、`_config`、`trade_context`、SDK import/factory 和 broker write，断言调用次数全部为 0。
8. 四个脚本必须用真实子进程分别验证 `--execute` 和 `--exec` 返回 20；测试把 `LOCALAPPDATA`、`LONGBRIDGE_BIN` 指向 `tmp_path` 下不存在的文件，证明不是外部 CLI “碰巧失败”。
9. `lb_bridge._run([... , "--execute"])` 必须有直接测试，且在检查 CLI 路径或调用 subprocess 前阻断。
10. kill-switch 必须可测试为函数或最早参数门禁，不能依赖真实 CLI 缺失而“碰巧失败”。
11. `lb_sdk.submit_order()` 与 `lb_bridge.order_buy()/order_sell()` 必须在读取 `.env`、导入 Longbridge SDK、构造 context 或启动 subprocess 前抛出带 `exit_code=20` 的 `LegacyBrokerWriteBlocked`；所有读取函数保持不变。测试断言直接调用这三个旧写函数时外部调用计数为 0。

**验证：**

```powershell
python -m pytest tests/test_legacy_execute_killswitch.py -q
```

**提交：** `fix: 禁用旧券商写入入口`

### P0-C：旧 wrapper fail-closed

**目标文件：**

- `scripts/daily_run.ps1`
- `scripts/us_open_run.ps1`
- `tests/test_legacy_wrappers.py`

**实现要求：**

1. canonical `trade_cycle.py` 尚未交付前，两个 wrapper 必须立即输出迁移中阻断状态并返回 20。
2. wrapper 不得调用任何旧 `--execute`、数据刷新、报告或 OAuth 命令。
3. 删除硬编码 `Set-Location "D:\3314\quant-research"` 和 `LB_ACCOUNT_KIND` 假护栏。
4. 静态测试证明 wrapper 不含 `lb_order_map.py`、`lb_rebalance.py`、`--execute`、`ErrorActionPreference = "Continue"`。
5. 不修改 Windows 任务；它们仍保持 `Disabled`。

**验证：**

```powershell
python -m pytest tests/test_legacy_wrappers.py -q
```

**提交：** `fix: 让旧自动化包装器默认阻断`

### P0 阶段出口

P0 除固定验证外还必须运行：

```powershell
$null = rg -n -e "--execute" scripts/daily_run.ps1 scripts/us_open_run.ps1
$wrapperRgExit = $LASTEXITCODE
if ($wrapperRgExit -eq 0) { throw "旧 wrapper 仍含 --execute" }
if ($wrapperRgExit -gt 1) { exit $wrapperRgExit }

rg -n -e "order.*submit" -e "order.*cancel" scripts/lb_order_map.py scripts/lb_rebalance.py scripts/lb_fix_hkd.py scripts/lb_reconcile.py
$legacyRgExit = $LASTEXITCODE
if ($legacyRgExit -gt 1) { exit $legacyRgExit }
exit 0
```

第一段把 `rg` 的“无匹配”退出码 1 明确转换为 PASS；第二段允许保留不可达的 legacy 函数，但离线测试必须证明 write intent 在调用前被阻断。

交付 P0-A/P0-B/P0-C 的 commit SHA 和报告后停止。未经用户转达 Codex 的明确“P0 审核通过，进入 P1”，不得继续。

---

## P1：基础组件、依赖与时间契约

只有 P0 审核通过后才能实施。

### P1-A：依赖与 CI fail-closed

**目标文件：**

- `requirements.txt`
- `.github/workflows/ci.yml`
- `tests/test_dependency_contract.py`

**实现要求：**

1. 固定 `exchange_calendars==4.13.2`。
2. 删除 `pip install -r requirements.txt || pip install ...`；依赖安装失败必须使 job 失败。
3. 覆盖 Python 3.12 和 3.13；至少一个 Windows job 运行锁测试和全套测试。
4. CI 无 secret、无网络业务调用、无真实 broker context。
5. DeepSeek 不自行联网安装依赖。进入 P1 前由 Codex lead 预置已审核 wheel/环境；若本地 import 失败，报告 `BLOCKED_DEPENDENCY_MISSING` 并停止。CI 的依赖下载只会在 Codex 审核并推送后由受控 runner 执行。

**提交：** `ci: 固定日历依赖并移除安装回退`

### P1-B：canonical hash 与原子 IO

**目标文件：**

- `quant/automation/__init__.py`
- `quant/automation/canonical.py`
- `quant/automation/atomic.py`
- `tests/test_canonical.py`
- `tests/test_atomic_io.py`

**接口：**

```python
canonical_json_bytes(value, *, schema_name: str, schema_version: int) -> bytes
canonical_table_bytes(frame, *, schema) -> bytes
sha256_id(payload: bytes) -> str
atomic_write_bytes(path, payload, *, expected_sha256=None) -> None
atomic_write_json(path, value, *, schema_name, schema_version) -> None
```

测试覆盖 NFC、key 排序、Decimal 定点、NaN/Infinity 拒绝、缺失 tag、行排序、dtype/schema 变化、跨进程稳定性、写入故障和读回 hash 失败。按权威设计第 6.1.1 节实现字段白名单式 identity projection，并测试 `generation_id/snapshot_id/plan_id/intent_id/event_hash`：自身字段和运行/派生字段变化不改变 ID，任一身份字段变化会改变 ID，读取时自身字段篡改会被拒绝；严格按 generation/snapshot → consumption key → plan → intent → event 顺序计算，禁止 plan/intent 循环依赖。

**提交：** `feat: 增加可复现哈希和原子写基础组件`

### P1-C：交易所与 FX sessions

**目标文件：**

- `quant/data/sessions.py`
- `quant/data/fx_sessions.py`
- `tests/test_market_sessions.py`
- `tests/test_fx_sessions.py`

**接口：**

```python
expected_session(market, *, now_utc, close_buffer_minutes=15)
session_after(market, session, *, count=1)
regular_execution_window(market, session, *, open_offset=5, close_offset=90)
expected_fx_receipt(*, now_utc, schedule_version="FX_NY17/v1",
                    close_buffer_minutes=30)
calendar_identity(market, *, sessions, close_buffer_minutes=15)
```

`now_utc` 必须 aware，naive datetime 拒绝。`calendar_identity` 必须显式接收非空 session labels，hash 包含 calendar name、`exchange_calendars` version、timezone、close buffer 和每个 session 的完整 schedule receipt，不得读取墙钟或猜 session。测试覆盖空 sessions 拒绝、顺序归一、墙钟无关、DST、节假日、周末、Black Friday 半日市、午休、close buffer 变化、NY17 夏/冬令时、周末最近周五，并固定北京时间 `2026-10-23 21:35` 的 US completed session 为 `2026-10-22`。snapshot build 与 open revalidation 对同一 `[rebalance_session, eligible_execution_session]` 必须产生相同 identity。

**提交：** `feat: 统一市场与汇率完成时点`

### P1-D：状态、OS 锁、manifest 与 policy schema

**目标文件：**

- `quant/automation/status.py`
- `quant/automation/lock.py`
- `quant/automation/manifest.py`
- `quant/automation/policy.py`
- `tests/test_lock_manifest_policy.py`

实现权威设计的退出码、脱敏 manifest 和 Windows/Linux OS 锁。manifest 固定 `automation.run_manifest/v1`，含随机 run id、profile/mode/markets、commit、UTC 时间、pinned pointer/datasets、snapshot/plan id、ordered stage records、final status、exit code、broker write count；原子阶段更新并识别崩溃遗留 `STARTED`。测试状态/退出码一致、dry-run writes=0 和敏感值不回显。元数据文件只供诊断。policy 先实现 schema/read-only 校验，不实现 broker 行为。

**提交：** `feat: 增加运行锁状态清单和策略契约`

### P1 阶段出口

除固定验证外，在 Windows job 证明第二进程得到退出码 3，第一进程异常结束后 OS 自动释放锁。完成后停止等待审核。

---

## P2：Immutable generation、cutoff 与 data-close

只有 P1 审核通过后才能实施。

### P2-A：generation store 与 legacy bootstrap

**目标文件：**

- `quant/data/generations.py`
- `quant/data/store.py`
- `quant/data/panel.py`
- `quant/config.py`
- `scripts/bootstrap_generation.py`
- `tests/test_generations.py`
- `tests/test_generation_view.py`
- `tests/test_bootstrap_generation.py`

实现稳定 dataset key、固定 generation 文件/manifest schema、按设计第 6.1.1 节投影计算的 generation id、顶层 CURRENT 映射、`expected_current_sha256` CAS、碰撞校验、发布后读回和安全回切。发布 API 固定为 `publish_generations(updates, *, expected_current_sha256)`：一次调用接受一个或多个非空 dataset 更新，并对顶层 CURRENT 恰好执行一次 CAS。新增 pinned `GenerationView`，store/panel 自动化路径必须显式接收 view；CURRENT 在运行中被另一进程切换也不能混读。automation data root 由 CLI/cycle 注入，禁止回落到 config 全局 `CACHE_DIR`；legacy flat reader 明确定名 `LegacyFlatStore`，只允许 research/bootstrap 显式调用。

bootstrap 是 source-preserving：源 flat cache bytes/hash/mtime 不变，只产出 `BOOTSTRAPPED_UNEXECUTABLE` equity generation；没有 snapshot 时只跑 data-only base gate，不伪造目标 100% 覆盖，FX 由独立 profile 产生。测试证明任一失败时正式 bytes/hash/size/mtime_ns 和 CURRENT 全部不变；execute reader 不得回退 flat cache。

**提交：** `feat: 增加不可变数据代际和只读迁移`

### P2-B：结构化数据门禁

**目标文件：**

- `quant/data/gates.py`
- `tests/test_data_gates.py`

实现权威设计第 8.3 节全部门禁，返回结构化结果而非解析控制台文本。使用冻结 eligible universe 计算覆盖率，并在 snapshot 可用后单独验证目标证券 100% 覆盖。网络/空响应/解析失败不能被 90% 覆盖率掩盖。

**提交：** `feat: 增加候选数据结构化门禁`

### P2-C：Yahoo 与 FX candidate producer

**目标文件：**

- `quant/data/yahoo.py`
- `quant/data/fx.py`
- `scripts/fetch_market.py`
- `tests/test_yahoo_cutoff.py`
- `tests/test_fx_data_gate.py`

producer 只写 candidate；在收益率/因子前删除未来或未完成 bar。实现 `CNY=X` 和 `HKDCNY=X` 的方向、`source_timestamp_utc`、NY17 `expected_fx_receipt`、`receipt_label` 和 hash 验证。provider timestamp 到 receipt label 必须通过版本化 adapter 和 fixture，不能直接复制日期；真实 fixture 缺失时返回 `BLOCKED_FX_LABEL_MAPPING`。测试只能 fake HTTP，覆盖 DST、周末、假日无 bar、future timestamp 和 06:45 北京时点。

**提交：** `feat: 隔离Yahoo行情并严格校验汇率cutoff`

### P2-D：CN 全部 producer staging

**目标文件：**

- `scripts/fetch_tx.py`
- `scripts/fetch_baostock_fallback.py`
- `scripts/fetch_baostock_full.py`
- `scripts/fetch_data.py`
- `scripts/fetch_parallel.py`
- `scripts/fetch_cn500.py`
- `quant/data/store.py`
- `quant/data/panel.py`
- `tests/test_cn_candidate_producers.py`
- 扩展 `tests/test_fetch_baostock_full.py`

所有正式写改为 candidate/no-publish contract，旧全局 `save_*`/`upsert_*` 不再能绕过 publisher。`fetch_cn500.py`、`fetch_market --market sg|crypto|all` 明确 research-only 且默认 no-publish。`fetch_parallel.py` 任一 child 非零时禁止 merge 并原样非零。

区分增量与全量：增量从 pinned generation 合并，frozen inventory 每个请求必须成功或有结构化非 eligible receipt；BaoStock 全量前复权必须重建完整 frozen inventory，任何未预先排除标的失败都不与旧历史拼接。覆盖 49/50/51/99/100/101 和第 50 只故障；验证内容、唯一性、空名称保护、成功只发布一次和失败正式状态完全不变。

**提交：** `feat: 将A股数据源统一迁入候选发布`

### P2-E：`fx-close` 编排

**目标文件：**

- `quant/automation/cycle.py` 的 FX 部分
- `scripts/trade_cycle.py` 的 fx-close 参数壳
- `tests/test_fx_close_cycle.py`

固定顺序：lock → manifest → freeze NY17 receipt/cutoff → 注入的 producer adapter → timestamp mapping → gate → 复制 CURRENT 中未变映射并只替换 `fx-cny` → readback → 释放。生产 adapter 与测试 fake 使用同一接口。真实 provider fixture 缺失时保持 blocked，production data root 不得发布 synthetic `fx-cny`；synthetic PASS 只能在 `tmp_path` 证明控制流。

**提交：** `feat: 增加汇率独立收盘编排器`

### P2-F：`data-close` 编排

**目标文件：**

- `quant/automation/cycle.py` 的 data 部分
- `scripts/trade_cycle.py` 的 data-close 参数壳
- `tests/test_data_close_cycle.py`

固定顺序：lock → manifest → 一次 freeze clock/sessions/CURRENT hash → 为全部 requested markets 运行注入的 producer adapter → cutoff → gate → 完成本轮全部 immutable equity generations → 聚合所有 `equity-<market>` 更新并对顶层 CURRENT 做一次 batch CAS → readback → 释放。禁止逐市场切指针；全部市场成功时 CAS 调用数严格为 1，任一市场失败时为 0。生产 adapter 与测试 fake 使用同一接口。缺少 FX 映射不阻断纯 equity data-close，但 open profile 后续必须同时 pin 有效 equity 与 FX generation。失败立即停止，不运行后续 stage；日历失败同样退出 11 且不发布。`--no-publish` 不切 CURRENT；`--mode dry-run` 仍可在指定临时 root 发布本地 generation。

**提交：** `feat: 增加数据收盘编排器`

### P2 阶段出口

执行完整故障矩阵，证明任意 producer、gate、fsync、replace 或并发指针失败都不污染旧 CURRENT 及其引用 generation。另以 CN/US/HK 三市场覆盖：全部成功只做一次 CAS；任一市场失败不做 CAS；并发 CAS 失败不得覆盖更新者，未引用 content-addressed orphan 不算发布。完成后停止等待审核。

---

## P3：研究 schedule 与 immutable snapshot

只有 P2 审核通过后才能实施。

### P3-A：共享 schedule 与 golden exporter

**目标文件：**

- `quant/strategies/schedule.py`
- `scripts/export_target_golden.py`
- `tests/test_rebalance_schedule.py`
- `tests/test_phase_ensemble_schedule.py`

US/HK 共用研究 schedule；CN 保留 `master_daily=True` 和四成员相位语义。exporter 只读取显式 `--data-root`，按研究 `weight_func` 重放，禁止对最后一天直接调用 `target_weights()` 冒充回测口径。输出必须含 market、源 fingerprint、code commit、model/universe/calendar identity、phase anchor、warmup、rebalance days、preset、rebalance/eligible session、完整 canonical weights 和 hash；不访问网络、不修改源数据。

**提交：** `refactor: 共享研究调仓时序并增加golden导出器`

### P3-B：lead golden checkpoint 与执行模型身份

**目标文件：**

- `models/execution_v1.json`
- lead 提供的 `tests/fixtures/golden/approved_targets_v1.json`
- `tests/fixtures/golden/SHA256SUMS`
- `tests/test_approved_target_golden.py`

P3-A 提交经 Codex 审核后，lead 在隔离临时输出中显式只读运行数据，记录源 hash/mtime，不联网、不写运行数据，人工检查并交回脱敏 canonical JSON 与 SHA-256 allowlist。DeepSeek 只接受上述固定目录且 hash 在 allowlist 中的文件；缺失、额外文件或 hash 不符均报告 `BLOCKED_GOLDEN_MISSING` 并停止，不得打开未获批历史报告补齐。

只有 fixture 到位后，写入经审核的 US/HK `phase_anchor/warmup/rebalance_days/preset/universe` 到版本化 `models/execution_v1.json`，并锁定 session、成员、canonical weights hash。不得从当前 Panel 起点推导 anchor。CN golden 缺失时保持 research-only blocked。

**提交：** `test: 固化审核后的研究目标golden`

### P3-C：版本身份与 snapshot schema

**目标文件：**

- `quant/automation/targets.py`
- `tests/test_target_versions.py`

实现 execution model、equity generation/data slice、universe、显式 session receipts 的 calendar identity、按设计第 6.1.1 节投影计算的 snapshot canonical hash、权重 schema 和 US/HK-only execute 标记。`model_version` 必须覆盖 `models/execution_v1.json` 和相关策略代码。snapshot build 与 open revalidation 必须对相同的 rebalance/eligible session labels 得到相同 calendar identity。执行端不得导入策略。

**提交：** `feat: 增加可追溯目标快照契约`

### P3-D：原子 snapshot、指针和一次性窗口

**目标文件：**

- `quant/automation/targets.py`
- `tests/test_target_snapshot_store.py`

非调仓日 bytes/mtime 不变；pointer 原子切换；缺失/损坏/过期均 fail-closed；eligible session 和 missed-window 状态固定，禁止补单。

**提交：** `feat: 原子发布不可变目标快照`

### P3-E：把 snapshot build 接回 `data-close`

**目标文件：**

- `quant/automation/cycle.py`
- `tests/test_data_close_snapshot_cycle.py`

在完整 equity generation 发布/readback 后运行 shared schedule：due 才构建 snapshot candidate、完整验证并切 snapshot 指针；非 due 不改 snapshot bytes/mtime。snapshot candidate 失败不回滚已经完整通过的 generation，但整轮 manifest/退出码必须失败，且旧 snapshot 指针不变。测试锁定 stage 顺序、hash 和状态。

**提交：** `feat: 将目标快照接入数据收盘编排`

### P3 阶段出口

必须由 Codex 在真实 fixture 上审核通过后才能进入 P4。

---

## P4：Policy、ownership、plan 与 ledger

只有 P3 审核通过后才能实施。

### P4-A：execution policy、enrollment 与 ownership

**目标文件：**

- `quant/automation/policy.py`
- `quant/automation/ownership.py`
- `tests/test_execution_policy.py`
- `tests/test_ownership.py`

实现本地未跟踪 policy/enrollment schema、canonical `policy_id`、批准 commit、到期时间和人工资产隔离。必填硬限额按设计列全：order/market/run/day CNY caps、run/day order count、rebalance tolerance、cash buffer、min order、quote age、plan TTL、slippage、订单类型和窗口；缺字段默认禁用/0。命令行只能收紧，不能放宽。离线测试不得创建真实 enrollment。enrollment identity 只进入 plan、门禁和绑定事件；重新登记、client/cache path 或 attestation generation 变化均不得改变 consumption key、清空 ledger 或解除已有终态。

**提交：** `feat: 增加执行策略和资产归属护栏`

### P4-B：订单计划纯函数

**目标文件：**

- `quant/automation/plans.py`
- `tests/test_order_plans.py`

输入是 validated snapshot、一次性 pinned `{pointer_hash,equity generation,fx-cny generation}`、broker read snapshot 和 policy，输出 immutable plan。先完成全计划报价、FX、currency、cash、lot、tick、ownership、挂单和 CNY 等值限额预检，任何失败时订单写调用为 0。plan 与 intent 严格使用设计第 6.1.1 节的白名单投影，先算不含 intent 派生字段的 plan id，再算 intent id；测试证明自身字段、运行元数据、`client_request_id`/remark/汇总不会造成自引用或身份漂移。`pointer_hash` 只写入持久化 plan/manifest 作为运行溯源，不进入 `plan_id`；plan identity 只承诺其固定 generation identities。plan 记录 policy/quote/generation/TTL hash；US/HK symbol 规范、确定性 `client_request_id`/remark 按设计锁定。v1 只允许 `LO + Day + Buy/Sell`。

**提交：** `feat: 增加全量预检订单计划`

### P4-C：append-only ledger、intent 与 reconciliation contract

**目标文件：**

- `quant/automation/ledger.py`
- `tests/test_consumption_ledger.py`

实现 immutable event files + CAS `HEAD.json` 的 hash-chained ledger；`event_hash` 使用设计第 6.1.1 节的白名单投影，半写、断链、orphan event、自引用字段篡改和并发 HEAD 均需测试。固定调用为 `sha256_id(canonical_json_bytes({"environment":"paper","market":market,"snapshot_id":snapshot_id,"eligible_execution_session":eligible_execution_session}, schema_name="automation.consumption_key", schema_version=1))`；schema name/version 只通过 keyword-only 参数注入，不在 value 中重复。enrollment id/generation/attestation、client id/cache path、policy、commit 和 token receipt 均不能改变 key；它们只进入门禁、plan identity 和首次绑定事件。一个 key 只能绑定一份 execute plan，plan 可有多条稳定 intent，每个 intent 最多提交一次。测试必须证明 enrollment 全部相关字段变化后 key 不变，已绑定、unknown、partial 或 missed 的 key 仍不能创建第二份 plan。

第一笔 SDK 调用前用一个原子事件绑定整份 plan 和全部 `PREPARED` intent；每次调用前先持久化 `SUBMITTING`。同一未中断 run 内，仅当上一 intent 已唯一核验为 `WORKING` 或 `FILLED` 且全部门禁仍通过时，才可按原 plan 顺序继续下一 intent。任何 intent 到过 `SUBMITTING` 后，只要 run 崩溃、退出，或进入 partial、unknown、rejected、canceled、expired/失败，后续 run 就只能对整份 plan 做有界只读 reconciliation；其他 `PREPARED` sibling 必须永久转为 `ABORTED_UNSUBMITTED`，不得提交。只有整份 plan 从未进入过 `SUBMITTING` 时，后续 run 才可在同 plan、TTL 有效且门禁重验通过后开始首笔；TTL 过期永久 `EXPIRED_UNSUBMITTED`。dry-run 只写 preview/manifest，不 reserve key。

**提交：** `feat: 增加目标消费账本和幂等意图`

### P4 阶段出口

故障注入证明在第一笔提交前的任一失败均为零 broker write；重复 run、损坏 ledger 和未知 intent 都不能产生第二份 plan 或重复 intent。另证明 dry-run 后同一窗口仍可进行唯一一次 execute；出现 `PARTIALLY_FILLED` 后本 run 不再提交 sibling，后续 run 只能只读 reconciliation，其他 `PREPARED` sibling 转为 `ABORTED_UNSUBMITTED`。完成后停止等待审核。

---

## P5：Longbridge OAuth-only adapter 与 open profiles

只有 P4 审核通过后才能实施。

### P5-A：OAuth-only paper adapter

**目标文件：**

- `quant/broker/lb_paper.py`
- `tests/test_lb_paper_adapter.py`

使用依赖注入的 fake `OAuthBuilder`、`Config.from_oauth(..., enable_papertrading=True)`、fake `TradeContext` 和 fake `QuoteContext` 验证：

- token cache 缺失时在 builder 前返回 `AUTH_REQUIRED`/20。
- cache probe 可注入；生产默认检查 SDK 5.2.2 的 `~/.longbridge/openapi/tokens/<client_id>`，不读取或打印 token 内容。enrollment identity 使用 `client_id + resolved cache path + 人工登记 generation/attestation`，不使用文件 hash/mtime；identity 进入 plan 和门禁，但不得进入 consumption key。
- 即使缓存存在，也必须按 longbridge 5.2.2 的 positional-only 签名调用 `OAuthBuilder.build(reject_interactive_callback)`；callback 收到 URL 时立即抛 `AUTH_REQUIRED`，失效/过期缓存不得启动浏览器。
- 一轮只构造同 config 的一个 TradeContext 和一个 QuoteContext；账户/订单/post-check 复用前者，报价/市场状态/lot/tick 复用后者。
- longbridge 5.2.2 的 context 没有公开 `close()`；不得调用不存在的方法，对象生命周期限于单次 run 并在结束后释放引用。
- execute 图不导入/调用 Longbridge CLI、Legacy config 或 `lb_bridge.py`。
- 按 longbridge 5.2.2 的 `submit_order` 签名直接传参；捕获完整参数。
- `client_id`、cache path 或人工 enrollment generation 变化时 enrollment 失效并要求用户重新确认，但 ledger 保留且 consumption key 不变；同一账户的普通 token 刷新不根据 mtime 自动更换 enrollment id。任何 enrollment 变化都不能为已有 key 创建第二份 plan。
- 缺 scope、paper guard、order id、响应字段或未知 enum 均 fail-closed。
- submit 超时后只读 reconciliation，无法唯一确认则 22 且不重试。
- TradeContext 查询固定为 account balance、positions、当前 session orders/detail，并严格处理分页/schema/session filter；QuoteContext 固定为 quote、static info、trading session。
- post-check 优先 exact client request id，否则使用设计规定的完整 exact fallback key；零/多匹配、缺 order id、未知状态均 22。状态映射分别测试：`New/NotReported -> WORKING`；`PartialFilled -> PARTIALLY_FILLED`；`Filled -> FILLED/RECONCILED`；`Canceled/Expired/Rejected` 为终态失败。partial 后本 run 不再提交 sibling，后续 run 只读 reconciliation，并把其余 `PREPARED` sibling 转为 `ABORTED_UNSUBMITTED`。
- 不假设 SDK context 有 `close()`；adapter release 在无显式 API 时为幂等 no-op/释放引用。

**提交：** `feat: 增加OAuth模拟盘单上下文适配器`

### P5-B：`hk-open` / `us-open` 状态机

**目标文件：**

- `quant/automation/cycle.py`
- `tests/test_open_cycles.py`

按权威设计顺序实现 policy、commit、market/FX generation、snapshot、闭区间窗口、OAuth cache、单 config 双 context、broker reads、ownership、plan、多 intent、提交和 cycle 内 post-check。

普通 `dry-run` 只能用 fixture，在 OAuth config 前结束且不消费 key；`connected-dry-run` 才允许真实 SDK 只读、write count=0、不消费 key；`execute` 才能绑定 key。DeepSeek 只实现并 fake 测试三种模式，不能运行真实 connected mode。

**提交：** `feat: 增加美港股开盘执行状态机`

### P5-C：CLI contract

**目标文件：**

- `scripts/trade_cycle.py`
- `tests/test_trade_cycle_cli.py`

实现 `--profile data-close|fx-close|hk-open|us-open`、`--mode dry-run|connected-dry-run|execute`、显式 roots、固定时钟测试入口和结构化退出码。data/fx profile 只接受 dry-run；connected/execute 只适用于 open profile。生产模式禁止随意传 fake 或绕过 policy 的选项，参数错误规范化为 10。

**提交：** `feat: 完成统一自动化命令入口`

### P5 阶段出口

只能报告 `OFFLINE_BROKER_ADAPTER_PASS`。真实 OAuth scope、paper account 和交易权限仍为 `PENDING`。完成后停止等待审核。

---

## P6：wrapper、post-check、迁移与文档

只有 P5 审核通过后才能实施。

### P6-A：PowerShell wrapper 与严格 post-check

**目标文件：**

- `scripts/daily_run.ps1`
- 新增 `scripts/fx_close_run.ps1`
- `scripts/us_open_run.ps1`
- 新增 `scripts/hk_open_run.ps1`
- 将 broker post-check 固定集成进 `cycle.py`；`scripts/post_daily_check.py` 只读本地 manifest/ledger
- `tests/test_powershell_wrappers.py`
- `tests/test_post_check.py`

每个 wrapper 使用 `$PSScriptRoot` 定位仓库，只调用一次对应 `trade_cycle.py`，原样传播 `0/3/10/11/12/13/20/21/22/30`。不得串联旧脚本。正常 no-op 返回 0；missed window 返回 13。cycle 内 post-check 复用本轮同一 TradeContext、当前 intents 和 consumption key；查询异常非零。独立脚本不得新建 broker context。

任务映射只写文档：`QuantDailyRun -> data-close`、未来 `QuantFXClose -> fx-close`、`QuantHKOpen -> hk-open`、`QuantUSOpen -> us-open`。DeepSeek 不查询或修改实际任务，所有现有/未来任务继续 Disabled。

**提交：** `feat: 接入单入口包装器和严格运行后检查`

### P6-B：运行时迁移工具

**目标文件：**

- `scripts/migrate_automation_runtime.py`
- `tests/test_runtime_migration.py`
- 迁移说明文档

实现纯离线、显式 source/destination 的 plan/apply 工具：选择性迁移可再生产物，保留 policy/enrollment/ledger，绝不递归移除 state 根目录。测试只用 `tmp_path`。真实 D 盘执行留给 Codex lead。

**提交：** `feat: 增加保守的自动化运行时迁移工具`

### P6-C：隐私、研究证据和正式文档

**目标文件：**

- `README.md`
- `models/model_v4.json`
- `docs/HANDOFF.md`
- `docs/longbridge-setup.md`
- `artifacts/research/` 中经审核的安全研究证据
- `tests/test_privacy_contract.py`

DeepSeek 不自行打开历史中可能含敏感值的报告。Codex lead 必须先提供“已脱敏研究证据包 + 文件 SHA-256 allowlist”；DeepSeek 只能核对并迁入 allowlist 中的文件。broker/post-check、账户、订单和资产报告始终禁止迁入 Git。证据包缺失时本任务准确标为 `BLOCKED_SANITIZED_EVIDENCE_MISSING`，正式指标写 `PENDING`，不编造；其余 OAuth-only 和阶段状态文档仍可完成。privacy scanner 只输出规则、文件路径和计数，禁止回显匹配值。

**提交：** `docs: 更新方案B证据隐私和运行说明`

### P6 阶段出口

当前树隐私扫描通过；历史隐私仍标为 `PENDING`，仓库保持 private。完成后停止等待审核。

---

## P7：完全离线全链路验收

只有 P6 审核通过后才能实施。

### P7-A：四 profile fake E2E

**目标文件：**

- `tests/e2e/test_data_close_profile.py`
- `tests/e2e/test_fx_close_profile.py`
- `tests/e2e/test_hk_open_profile.py`
- `tests/e2e/test_us_open_profile.py`
- `tests/e2e/test_failure_matrix.py`

全部使用临时 roots、固定时钟、fake source、fake broker。覆盖 data-close、fx-close、hk-open、us-open 的成功、正常 no-op=0、closed、missed=13、lock，以及 data fail、FX label fail、snapshot fail、policy fail、broker read fail、preflight fail、submit partial/unknown 和 reconciliation fail。真实 Yahoo FX fixture 缺失时仍保持 `BLOCKED_FX_LABEL_MAPPING`。

断言：

- dry-run broker writes = 0。
- dry-run 不创建 OAuth context；fake connected-dry-run 创建一对 context、broker writes=0 且不消费 key。
- submit 前任一失败 broker writes = 0。
- unknown/partial 后自动重试 = 0。
- CURRENT/快照/ledger/manifest 的 hash 和状态符合契约。
- 所有退出码与 manifest 状态一致。

**提交：** `test: 增加四类自动化离线全链路验收`

### P7-B：安全扫描与候选报告

**目标文件：**

- `scripts/audit_automation_candidate.py`
- `docs/superpowers/reports/plan-b-offline-candidate.md`
- `tests/test_candidate_audit.py`

报告必须分别列出 PASS、BLOCKED、PENDING，不得把离线 fake 测试写成真实 paper 验收。最终最高状态只能是 `OFFLINE_IMPLEMENTATION_CANDIDATE`。

**提交：** `docs: 生成方案B离线候选验收报告`

### P7 阶段出口

运行：

```powershell
python -m pytest -q
python -m compileall -q quant scripts
python scripts/audit_automation_candidate.py --offline
git diff --check
git status --short --branch
```

完成后停止。不得部署、OAuth、canary 或恢复任务。

---

## 8. Codex lead 专属后续步骤

这些步骤不属于 DeepSeek 的 P0–P7 权限：

1. P3-A exporter commit 审核后，在隔离临时输出中显式只读运行数据，记录源 hash/mtime，不写 D 盘、不联网；人工检查后把脱敏 US/HK golden 交回 P3-B。CN golden 可继续 blocked/research-only。
2. 提供已审核的 Yahoo FX 夏/冬令时 provider fixture，确认 timestamp→NY17 receipt label；在此之前保持 `BLOCKED_FX_LABEL_MAPPING`。
3. 审核 source-preserving bootstrap 的 plan；在运行副本执行 apply/readback，确认源 flat cache bytes/hash/mtime 不变且初代状态为 `BOOTSTRAPPED_UNEXECUTABLE`。
4. 审核每阶段 commit，决定是否合并到 `main`。
5. 以 `pull --ff-only` 或明确审核过的 commit 发布到运行副本，并验证工作树/HEAD、CURRENT 和 manifest readback。
6. 使用用户授权的 OAuth 运行 `connected-dry-run` 最小只读 paper probe；不打印余额、持仓、member id 或 token，broker writes 必须为 0。
7. 单独人工批准后执行有硬上限的模拟盘 canary。
8. canary 审核后再次人工批准，才允许创建/修改/启用 `QuantDailyRun`、`QuantFXClose`、`QuantHKOpen`、`QuantUSOpen`。

回滚只能定点回退代码、CURRENT 指针和可再生产物；必须保留 policy、enrollment 和 append-only ledger。任一失败时所有任务保持 `Disabled`。

## 9. 每阶段交付报告模板

```text
阶段：P0/P1/...
状态：PASS / BLOCKED / FAIL
分支：
提交 SHA：
修改文件：
新增测试及目的：
验证命令、退出码、结果：
安全声明：未读取凭证；未访问任何网络（含 Git remote、包索引和业务服务）；未创建真实 TradeContext/QuoteContext；
          未访问 D 盘运行数据；未修改定时任务；未进行 broker write。
仍未完成或需 Codex lead 的事项：
下一阶段：未开始，等待审核。
```

出现阻塞时同样提交已完成且自洽的最小改动，准确报告阻塞；不要跨阶段寻找绕过方法。

## 10. 首次交给 DeepSeek 的启动指令

用户只需让 DeepSeek 完整读取本总路线图、对应权威设计和 P0 逐步施工单，并发送以下指令：

```text
从 tag plan-b-approved-20261024 开始，严格执行任务书第 0 节预检。
完整读取 docs/superpowers/plans/2026-10-24-quant-safe-automation-plan-b-p0.md。
本轮只按该施工单实施 P0-A、P0-B、P0-C；每个任务独立提交并完成固定验证。
只能人工读取施工单列出的 P0 精确 allowlist；禁止递归枚举/搜索整仓、git grep、docs/journal/**、其他分析/报告和 Git 历史/blob。
全程不访问任何网络（含 Git remote、包索引和业务服务），不读取凭证、不访问 D 盘运行数据、不创建真实 TradeContext/QuoteContext、
不查询或修改定时任务、不进行任何 broker write。
P0 完成后按第 9 节模板报告并停止，等待 Codex 审核；不得进入 P1。
```
