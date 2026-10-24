# 量化交易安全自动化实施任务书（DeepSeek 执行版）

> **已被取代：** 本文保留为历史交接材料。不要按本文继续实施。
> 当前唯一任务书是 `docs/superpowers/plans/2026-10-24-quant-safe-automation-plan-b.md`。

> **For agentic workers:** REQUIRED SUB-SKILL: 按任务顺序执行；每个任务完成后运行该任务的测试并提交独立 commit，等待外部审核后再进入下一个任务。不得跳过失败测试，不得把“能运行”当作“安全门禁已通过”。

**Goal:** 在不启用任何定时任务、不使用任何券商凭证、不提交任何订单的前提下，把 `quant-research` 改造成默认 dry-run、数据与目标快照可追溯、Longbridge 模拟盘执行单通道且 fail-closed 的安全自动化系统。

**Architecture:** 使用一个 `scripts/trade_cycle.py` 入口和 `quant/automation/` 编排层。数据阶段先生成隔离候选 bundle，全部门禁通过后以 immutable generation + `CURRENT.json` 原子发布；交易阶段只读取与研究调仓相位一致、具有一次性执行窗口的快照。账户查询、持仓、挂单、下单和对账全部由同一组凭证创建的 SDK paper `TradeContext` 完成，旧 CLI 提交入口在迁移期被禁用。

**Tech Stack:** Python 3.13、pandas 3.1、pytest、`exchange_calendars==4.13.2`（必须先通过 CI 兼容性验证）、Longbridge Python SDK 5.2.2、PowerShell 兼容入口、gzip CSV 运行时缓存。

---

## 0. 交给 DeepSeek 的固定指令

将本文件和以下设计文档一并提供给 DeepSeek：

- `docs/superpowers/specs/2026-10-24-quant-safe-automation-design.md`
- `docs/superpowers/plans/2026-10-24-quant-safe-automation-deepseek-handoff.md`

复制下面的指令作为 DeepSeek 的任务开头：

```text
你在 quant-research 仓库中按实施任务书逐项工作。先阅读已批准 spec，再阅读本任务书。

硬性限制：
1. 只能在审查副本工作，不得直接改动 D:\\3314\\quant-research 运行副本。
2. 不要读取、请求、生成、打印或保存 Longbridge App Secret、Access Token、member_id、完整订单号、精确资产或现金。
3. 不运行 scripts/lb_oauth.py，不调用任何券商写接口，不创建连接真实账户的 TradeContext，不提交、撤单或改单。
4. 不启用、修改或删除 QuantDailyRun、QuantUSOpen 或其他 Windows 计划任务。
5. 不重写 Git 历史，不执行 git reset --hard、git checkout -- 或删除用户现有数据。
6. 所有 execute 路径在本阶段必须因为缺少 execution policy 而 fail-closed；默认只允许 fake broker 测试和离线 dry-run。
7. 每个任务只做该任务列出的文件；先写失败测试，再写最小实现；任务完成后运行指定命令并创建独立 commit。
8. 如果测试、依赖或现有数据与 spec 冲突，停止该任务，保留证据并报告，不用降级为 fail-open。

每个任务的交付格式：
- commit SHA 和中文 commit message；
- 修改/新增文件清单；
- 实际运行的命令及完整 PASS/FAIL 摘要；
- 未解决问题、风险和是否触及任何外部系统（必须是“未触及券商/任务”）；
- 下一任务前需要人工确认的事项。
```

## 1. 当前基线与发布边界

DeepSeek 开始前必须只读确认：

```powershell
git status --short --branch
git rev-parse HEAD
Get-ScheduledTask -TaskName 'QuantDailyRun','QuantUSOpen' |
  Select-Object TaskName,State
```

预期基线：

- 审查副本：`C:\Users\33141\Documents\Codex\2026-10-23\https-github-com-yrz-guangnan-quant\work\quant-research`
- 基线 commit：`c22b74a`（设计文档已提交）；
- 工作树干净；不得把 D 盘运行副本当作开发目录；
- `QuantDailyRun`、`QuantUSOpen` 均为 `Disabled`；
- 当前设计文档：[2026-10-24-quant-safe-automation-design.md](../specs/2026-10-24-quant-safe-automation-design.md)。

如果基线不符合上述条件，DeepSeek 只提交一份诊断报告，不修改代码。

## 2. 文件责任地图

| 文件/目录 | 责任 | 允许的主要变更 |
|---|---|---|
| `quant/automation/canonical.py` | canonical bytes、JSON、表格和 SHA-256 | 新建纯函数模块 |
| `quant/automation/manifest.py` | run manifest、脱敏错误和阶段状态 | 新建，不覆盖旧运行日志 |
| `quant/automation/lock.py` | Windows OS 级全局锁 | 新建，不用 PID 猜测抢锁 |
| `quant/automation/policy.py` | execute 开关、账户 enrollment、限额和部署 commit 校验 | 新建，默认不可执行 |
| `quant/automation/ownership.py` | 策略专用账户及仓位/挂单登记 | 新建，未登记资产阻断执行 |
| `quant/automation/targets.py` | 研究相位、快照构建和验证 | 新建，禁止执行侧现场选股 |
| `quant/automation/plans.py` | 快照到 immutable plan/intent 的纯函数 | 新建，先整体预检再提交 |
| `quant/automation/ledger.py` | Gitignored 私有 execution ledger | 新建，跨 run 幂等 |
| `quant/automation/cycle.py` | profile 状态机和统一退出码 | 新建，所有前置失败立即停止 |
| `quant/data/sessions.py` | `exchange_calendars` session/cutoff | 新建，固定时区和收盘缓冲 |
| `quant/data/staging.py` | generation、bundle、CURRENT 原子发布 | 新建，不先覆盖正式缓存 |
| `quant/data/yahoo.py` | Yahoo 时区转换和完成 bar 截断 | 修改为纯函数可测试 |
| `quant/data/store.py` | generation 读取和 fingerprint | 扩展，保留旧读接口兼容期 |
| `scripts/fetch_market.py` | 候选抓取、统一 cutoff 和 bundle 交付 | 重构为可注入 client |
| `scripts/fetch_baostock_full.py` | 49/50/51/99/100/101 分块 staging | 禁止部分写正式缓存 |
| `scripts/pre_trade_gate.py` | 结构化数据门禁 | 改为调用 sessions/staging 结果 |
| `scripts/build_target_snapshot.py` | CLI builder 兼容入口 | 只调用 `targets.py`，原子写入 |
| `quant/broker/lb_paper.py` | 唯一 SDK paper context adapter | 新建；禁止 CLI fallback |
| `scripts/trade_cycle.py` | 唯一 CLI 入口 | 新建；不承载业务细节 |
| `scripts/daily_run.ps1` | data-close 兼容入口 | 最终只转发到 `trade_cycle.py` |
| `scripts/us_open_run.ps1` | us-open 兼容入口 | 最终只转发并传播退出码 |
| `scripts/post_daily_check.py` | 当前 run 的严格对账 | 修改 schema、return code 和 ledger 来源 |
| `requirements.txt` | 依赖锁定 | 仅在 CI 验证后加入 calendar 依赖 |
| `.gitignore` | 运行时状态隔离 | 加入 generation、state、ledger、staging |
| `tests/` | 纯 fake、固定时钟和故障注入测试 | 不访问真实网络/券商 |

研究专用 `scripts/run_paper.py` 可以保留为手动工具；`scripts/paper_live.py`、旧 CLI 提交入口和 SG 自动下单必须从新调度链路移除或改成只读诊断。

## 3. 阶段总览与停止条件

阶段必须严格按顺序执行，每阶段独立 commit：

| 阶段 | 内容 | 可接触外部系统 | 完成条件 |
|---|---|---|---|
| P0 | 基线、文件影响矩阵、依赖/API 事实核验 | 仅本地只读 | 报告提交，代码不变 |
| P1 | canonical、sessions、atomic store、lock、schema/退出码基础 | 仅 fake/临时目录 | 单测通过 |
| P2 | Yahoo/BaoStock staging、generation、数据门禁 | fake client | 失败时正式数据 byte/hash/mtime 不变 |
| P3 | 研究 schedule、目标快照、执行窗口、ledger | fake panel/ledger | golden fixture 与研究侧一致 |
| P4 | plan、ownership、policy、FX、限额、幂等 | fake broker | dry-run 写调用为 0 |
| P5 | SDK adapter、cycle、三个 profile | fake SDK | CLI/OAuth 调用扫描为 0 |
| P6 | PowerShell wrapper、post-check、文档和运行时迁移 | 仅本地 | 任务仍 Disabled |
| P7 | 隔离目录全链路离线 dry-run | fake broker | 三 profile、写调用 0、报告完整 |

任何阶段出现 P0 安全问题、秘密泄露、非零错误被吞、正式缓存被部分写入、快照现场重算、任务状态变化或外部券商调用，必须停止并回报，不能继续后续阶段。

## 4. P0：基线与事实核验

**Files:**

- Create: `docs/superpowers/reports/2026-10-24-deepseek-p0-baseline.md`
- Do not modify: application code, data, tasks, credentials

### Step 1：建立文件影响矩阵

列出 `git ls-files` 中的运行时状态、订单/账户报告、日志和凭证相关文件，至少包括：

```text
state/target_snapshot.json
logs/lb_login_out.txt
logs/lb_login_err.txt
logs/lb_order_map_result.json
reports/broker_daily_*.json
reports/post_check_*.json
docs/journal/0013-*.md
docs/journal/0018-*.md
docs/journal/0019-*.md
docs/longbridge-setup.md
```

报告中逐项标注“保留/脱敏/移出 tracked/历史清洗另案”，不要把真实报告复制到测试 fixture。

### Step 2：核验研究相位事实

只读检查以下代码，记录每个市场的真实 `warmup`、ensemble offset、rebalance 和 preset，不得把 `130/20` 作为所有市场的默认事实：

```text
quant/strategies/topk.py
quant/strategies/ensemble.py
scripts/predict_multi.py
scripts/portfolio.py
quant/strategies/presets.py
```

特别记录 CN 的 `top40/ic_tilt/reb35`、US 的 `top20/momentum/reb20`、HK 的 `top15/momentum/reb20`，以及 ensemble 的 `master_warmup = warmup + max(offsets)`。P0 只报告事实，不改研究逻辑。

### Step 3：核验 SDK 5.2.2 API（不读取凭证）

从已安装包的签名/文档确认 `Config.from_apikey`、`enable_papertrading=True`、`TradeContext` 查询和订单响应字段。不得调用构造函数连接外部服务；只记录签名和异常类型。

### Step 4：提交 P0 报告

```powershell
git diff --check
git add docs/superpowers/reports/2026-10-24-deepseek-p0-baseline.md
git commit -m "docs: 提交安全自动化基线核验"
```

P0 报告经维护者审核前，不得开始 P1。


### Task 0.5：凭证与 paper 授权现状核验（只读）

**目的**：确认当前凭证能否构造 `enable_papertrading=True` 的 SDK context。实测当前 token
在 paper 环境返回 `202201 get userinfo error`，若不前置核验，P5 会以"假 SDK 通过、真环境不可用"卡死。

**Files:** Create `docs/superpowers/reports/2026-10-24-paper-credential-status.md`（只写状态码，不写凭证）

**Steps:**
1. 读取 `.env` 是否存在（不打印内容），检查三个键是否齐全；
2. 尝试构造 `Config.from_apikey(..., enable_papertrading=True)` 并 `TradeContext.account_balance()`；
3. 仅记录成功/异常类型与错误码（如 202201），**不得打印 token、member_id、余额数值**；
4. 结论三分支：`PAPER_READY` / `PAPER_AUTH_REQUIRED` / `CREDENTIAL_MISSING`。

**验收**：报告提交；`PAPER_AUTH_REQUIRED` 时 P5 允许以 BLOCKED 终态结束，不算失败。

## 4.5 现状对照表（c22b74a 已包含，避免重复实现或回退）

| 已修项 | 当前位置 | 新架构归属 |
|---|---|---|
| 汇率 ffill bug | `scripts/portfolio_fx.py` | 研究侧，保留 |
| predict / predict_multi 假共识 | `scripts/predict*.py` | `targets.py` |
| 订单提交幂等/unknown | `scripts/lb_order_map.py` | `plans.py` + `ledger.py` |
| 交易前数据门禁 | `scripts/pre_trade_gate.py` | `sessions.py` + `staging.py` |
| 目标快照 | `scripts/build_target_snapshot.py` | `targets.py` |
| 模拟盘 fail-closed 校验 | `lb_order_map` / `lb_rebalance` | `policy.py` + `lb_paper.py` |

## 5. P1：纯函数基础与依赖门禁

### Task 1：依赖兼容性

**Files:** `requirements.txt`、`.github/workflows/ci.yml`、`tests/test_dependency_matrix.py`

在隔离虚拟环境验证 Python 3.13 + pandas 3.1 + `exchange_calendars==4.13.2`。只有安装、import、现有测试和 `XNYS` calendar smoke test 全部通过，才能把依赖写入 `requirements.txt`。依赖失败时提交报告，不改成未锁定版本。

```powershell
python -m pytest tests/test_dependency_matrix.py -q
python -m pytest -q
git diff --check
git add requirements.txt .github/workflows/ci.yml tests/test_dependency_matrix.py
git commit -m "build: 锁定交易日历依赖并验证兼容性"
```

### Task 2：canonical hash

**Files:** `quant/automation/__init__.py`、`quant/automation/canonical.py`、`tests/test_canonical.py`

提供固定接口：`canonical_json_bytes(value) -> bytes`、`canonical_table_bytes(frame, *, columns, key_columns, schema_name, schema_version) -> bytes`、`sha256_hex(payload) -> str`、`hash_file_set(root, relative_paths) -> str`。

测试必须证明 Unicode NFC、key 排序、NaN/Infinity 拒绝、Decimal 定点、缺失值 tag、行排序、dtype/schema 变化和跨进程结果一致。不能直接 hash gzip 的非稳定元数据。

```powershell
python -m pytest tests/test_canonical.py -q
git add quant/automation tests/test_canonical.py
git commit -m "feat: 增加可复现内容哈希基础组件"
```

### Task 3：交易 session

**Files:** `quant/data/sessions.py`、`tests/test_market_sessions.py`

提供固定接口：`expected_session(market, *, now, close_buffer_minutes=15)`、`session_after(market, session, count=1)`、`is_regular_session_open(market, now)`、`calendar_identity(market)`。

固定映射：CN=`XSHG/Asia-Shanghai`、HK=`XHKG/Asia-Hong-Kong`、US=`XNYS/America-New-York`。测试固定验证北京时间 `2026-10-23 21:35` 得到 US `2026-10-22`，并覆盖 DST、节假日、周末、Black Friday 半日市。execute 不能使用工作日 fallback；dry-run fallback 必须标记 degraded。

```powershell
python -m pytest tests/test_market_sessions.py -q
git add quant/data/sessions.py tests/test_market_sessions.py
git commit -m "feat: 统一交易所交易日和完成时点"
```

### Task 4：OS 锁、manifest 和退出码

**Files:** `quant/automation/lock.py`、`quant/automation/manifest.py`、`quant/automation/policy.py`、`tests/test_lock_manifest.py`

实现 Windows OS 持有的全局锁，不得只创建 lock 文件或根据 PID 自动抢占。manifest 必须原子写入、每 run 独立、脱敏，并区分 `SUCCESS`、`NOOP_NOT_DUE`、`SKIPPED_MARKET_CLOSED`、`WORKING`、`BLOCKED`、`PARTIAL`、`UNKNOWN`。锁失败返回 3；所有阶段错误有非零退出码；`WORKING`/休市虽可返回 0，监控状态不能误报为完整成功。

```powershell
python -m pytest tests/test_lock_manifest.py -q
git add quant/automation/lock.py quant/automation/manifest.py quant/automation/policy.py tests/test_lock_manifest.py
git commit -m "feat: 增加运行锁和脱敏状态清单"
```

P1 完成后停止，等待维护者审核；未通过不得进入数据写入相关任务。

## P6.5：发布与一致性校验（审查副本 → D: 运行副本）

1. 只有 lead 执行合并；合并前后 `git rev-parse HEAD` 必须与审查副本一致；
2. D: 运行副本在重构期间保持 `QuantDailyRun` / `QuantUSOpen` 为 Disabled；
3. 合并后依次执行：`python -m pytest -q`、`python scripts/pre_trade_gate.py`、
   `python scripts/trade_cycle.py --profile data-close --dry-run`（三件套全绿才继续）；
4. 打 tag（`runtime-YYYY-MM`）；回滚 = `git revert` 到 tag + 任务保持 Disabled + 删除 `state/` 后从 staging 重建。
