# 外部审查回应：已修 / 未修清单（2026-10-24）

审查来源：外部对 main(2f4fcfc) 的只读审计。以下按严重度逐条回应。

## 严重问题

| # | 问题 | 状态 |
|---|---|---|
| 1 | 汇率收益 ffill 重复计算（美股 +22.54%、港股 +38.35% 虚高） | ✅ **已修**：改为「价格先并集对齐+ffill，再 pct_change」。复算：美股 +22.45% → **-1.45%**，港股 +46.88% → **-2.44%**。README 人民币口径结果已加横幅撤回；**已全量重算**（4 相位：A60/美30/港10 = 15.64%±0.54、夏普 0.97、回撤 -22.65%、月胜率 62.1%），旧数字作废 |
| 2 | 重启后四相位共识失效（PaperTrader 每次新建策略、只处理新日期） | ✅ **已修**：`PhaseEnsembleStrategy.replay()` 重放全部历史后再交易；`run_paper` 调用 |
| 3 | predict 的「四相位预测」是假共识（4 个 warmup 不同但目标相同） | ✅ **已修**：predict 改用真实 `PhaseEnsembleStrategy` + replay；验证输出 40 只/68.6%（0.70 仓位） |
| 4 | 自动下单重复委托/账户护栏 | ⚠️ **部分修**：①`lb_rebalance` 跳过有未成交挂单的标的；②不再把未成交卖出的预计金额当购买力（cap=cash）；③市场状态查询失败改为「全部不可交易」；④两个定时任务已 **Disabled**。**未修**：`lb_order_map` 默认仍非 dry-run；限价失败降级市价单仍在 |
| 5 | 美股/港股数据可能过旧 | ✅ **已修**：`daily_run.ps1` 增加 `fetch_market.py --market us/hk`；自检增加 Longbridge 连接返回码与任务延迟检测 |
| 6 | `lb_rebalance` 未真正再平衡 | ⚠️ **部分修**：跳过挂单标的、卖出未成交不计购买力。**未修**：被移出目标的旧持仓不卖出、港股 board lot/tick 未对齐 |

## 其他

| 问题 | 状态 |
|---|---|
| 腾讯前复权增量更新未重算旧历史（除权边界假跌幅） | 未修，已记录 |
| post_check 不检查返回码、task_late 未使用 | ✅ 已修（连接检查 + 延迟检测） |
| requirements 缺 longbridge、无锁定 | ✅ 已加 longbridge；版本锁定仍未做 |
| 无 CI、无 LICENSE | ✅ 已加 `.github/workflows/ci.yml`（compileall + pytest）与 MIT LICENSE |
| README 写「27 个测试/不接真实券商」等过时内容 | 部分修：顶部加撤回横幅；正文数字待重算后统一更新 |
| 2025+ 已多次查看，不再是独立 OOS | 已披露，继续以「样本内观察」对待 |

## 建议的下一步顺序（按审查意见）

1. 全量重跑 `portfolio_fx.py`，用修正后的汇率口径重算人民币结果并更新 README；
2. `lb_rebalance` 补：移除目标外持仓、港股 board lot、tick 对齐；
3. `lb_order_map` 默认 dry-run、移除市价单降级（或改为显式开关）；
4. 腾讯前复权增量更新：改为定期全量重算；
5. 恢复定时任务前，先人工跑一轮验证。

## 当前安全状态

- 自动任务 **Disabled**（不会自动下单）；
- 账户：HKD +506k / USD 117k / SGD 1M；持仓 29 只（模拟盘）；
- 本轮所有修复已提交，35 个测试通过。
## 2026-10-24 收尾验证（休市日）

### 已完成并验证

| 项 | 验证结果 |
|---|---|
| 汇率修复重算 | ✅ 4 相位：A60/美30/港10 = 15.64%±0.54、夏普 0.97、回撤 -22.65%、月胜率 62.1% |
| 相位状态重放 | ✅ `run_paper` 打印「相位状态已重放到最新日期」，权益 3,567,701 不变（无新交易日） |
| 下单默认 dry-run | ✅ 输出 `[plan] ... dry-run`，20 笔全部未提交 |
| 依赖锁定 | ✅ requirements.txt 固定版本（numpy 2.4.6 / pandas 3.1.0 / lightgbm 4.7.0 / longbridge 5.2.2 等） |
| 自动任务 | 仍 Disabled（未恢复） |

### 未完成：A 股前复权全量重算被限流

- 第一次全量刷新（8 线程）：563 只中 209 只成功、354 只被腾讯限流；
- 第二次降并发（3 线程）重试：**100% 失败**（150/150、200/200、250/250 全失败），
  说明腾讯已对本机 IP 限流，需冷却（通常数小时）。
- 影响：209 只历史已按最新复权因子重算，354 只仍是旧快照；**每只股票自身序列连续**，
  但不同股票的复权快照时间不一致，除权边界可能有个别假跌幅。
- 处理建议：等 IP 冷却后重跑 `python scripts/fetch_tx.py --full --workers 2`；
  或写 BaoStock 全量前复权刷新脚本（当前 fallback 脚本仅补缺失日期，不支持全量）。

### 数据现状

- 缓存 1,123,342 行，覆盖 2018-01-02 ~ 2026-10-23，563 只，无重复行。
## 第三轮复审回应（2026-10-24 11:30）

### 已修

| # | 问题 | 状态 |
|---|---|---|
| 1 | `flush_partial` 在第 50 只未定义即调用（UnboundLocalError） | ✅ 定义移到循环前；语法/导入通过（边界单测未补） |
| 2 | 数据失败后仍继续下单 | ✅ 新增 `pre_trade_gate.py`：按北京时间收盘点算各市场最近已完成交易日 + 覆盖率≥90% + 指数一致性 + 重复行；失败 `exit 1`；接入 daily_run（4b）与 us_open_run（下单前硬门禁）。实测：US/HK 停在 10-22 时拦截（exit 1），补 `fetch_market` 后三市场 PASS |
| 3 | 订单响应复用 / 未知状态 | ✅ 每笔 `resp=None` 独立初始化；缺 `confirmation_code` 标记失败；提交异常先查订单判重，查询失败 fail-closed；任一 error/unknown → 进程返回非零 |
| 4 | 模拟账户只靠环境变量 | ⚠️ 已加 SDK 真校验（`enable_papertrading=True` 连接 + `account_balance`），但当前 token 报 `202201 get userinfo error` → 校验**失败即拒绝交易**（fail-closed）。需要重新用模拟盘 OAuth 授权才能通过 |

### 未修（明确列出）

| 问题 | 说明 |
|---|---|
| 调仓节奏不一致 | 海外回测 20 日调仓，执行脚本每天重选；需「目标快照」（model/data version + rebalance_date + next_rebalance_date），非调仓日不选股 |
| 美股盘中 Yahoo 当日 K | 未排除未收盘 bar |
| 前复权 staging/原子发布 | 未实现；BaoStock 全量刷新仍不稳定（超时/解码异常） |
| post_check JSON 结构校验 | 部分：连接失败标 FAIL，但订单查询异常仍可能 PASS |
| model_v4 正式字段 | 仍为旧数字，仅加了作废说明 |
| README 正文 | 旧汇率表格未重写 |
| 隐私历史 | 仓库已私有；历史未清洗 |

### 交易安全状态

- `QuantDailyRun` / `QuantUSOpen`：**Disabled**；
- `--execute` 现在要求：`LB_ACCOUNT_KIND=paper` + SDK 模拟盘连接成功；当前第二步失败 → **所有自动下单被拒绝**（安全但阻塞）。
- 解锁条件：用模拟盘 OAuth 重新授权（`scripts/lb_oauth.py`）拿到能在 `enable_papertrading=True` 下工作的 token。
