# Codex 安全自动化交接文档评审（lead 视角，2026-10-24）

> **历史评审：** 本文的问题裁决已经汇总进方案 B。实施时只以
> `docs/superpowers/specs/2026-10-24-quant-safe-automation-plan-b-design.md`
> 和 `docs/superpowers/plans/2026-10-24-quant-safe-automation-plan-b.md` 为准。

评审对象：
- `docs/superpowers/specs/2026-10-24-quant-safe-automation-design.md`（378 行）
- `docs/superpowers/plans/2026-10-24-quant-safe-automation-deepseek-handoff.md`（224 行）

总体结论：**设计与任务书方向正确、风险识别到位**（尤其是「SDK paper 校验 ≠ CLI 下单通道」这一条，
与实测完全吻合：当前 token 在 `enable_papertrading=True` 下报 `202201 get userinfo error`）。
但作为交给执行代理的任务书，**还缺 6 类内容，其中 3 类会直接导致卡死或返工**。

## 一、必须改（阻塞级）

### 1. 审查副本与 GitHub 未同步
- 审查副本 HEAD = `c22b74a`，是 `5caa85c` 的子提交，但 **ahead 1 未 push**；
- `docs/superpowers/plans/` 目录仍是 **untracked**；
- 若按任务书在审查副本开工，所有提交都不会进 GitHub，"审查副本"失去可追溯性。
**建议**：交接前先 `git add docs/superpowers/plans && git commit && git push`；
任务书里把基线改成 "c22b74a 且 origin/main == c22b74a"。

### 2. 缺「paper 授权」前置任务（P-1）
任务书 P5 假设 SDK paper adapter 可用，但**当前凭证无法在 paper 环境通过身份校验**（202201）。
执行代理会卡在 P5 的验收上，且不能靠 fake SDK 证明真实可交易。
**建议**：在 P0 增加只读 Task 0.5「凭证与 paper 授权现状核验」：
- 用 SDK 构造 `enable_papertrading=True` 的 context，只记录成功/异常类型（不打印 token）；
- 明确记下 202201 的当前状态与解锁条件（App 内重新授权模拟盘）；
- P5 验收允许"凭证未就绪 → BLOCKED"作为合法终态，不算失败。

### 3. 缺「审查副本 → 运行副本（D:）」的同步协议
任务书禁止直接改 D: 运行副本，但没写清楚：谁合并、何时合并、如何验证一致。
**建议**：增加 P6.5「发布与一致性校验」：
- 只有 lead 执行合并；D: 在重构期间任务 Disabled（已是现状）；
- 合并前 `git rev-parse HEAD` 必须等于审查副本 HEAD；
- 合并后跑 `pytest` + `pre_trade_gate` + `trade_cycle --dry-run` 三件套；
- 打 tag（如 `runtime-2026-11`）便于回滚。

## 二、应补充（会返工）

### 4. 缺「现状对照表」：我最近已修的 6 项，任务书视角像是从零开始
`c22b74a` 已包含以下修复，任务书应显式映射到新模块，避免重复实现或回退：
| 已修 | 位置 | 新架构对应 |
|---|---|---|
| 汇率 ffill bug | `scripts/portfolio_fx.py` | 保留（研究侧，不在自动化链路） |
| predict / predict_multi 假共识 | `scripts/predict*.py` | 并入 `targets.py` |
| 订单提交幂等/unknown | `scripts/lb_order_map.py` | 并入 `plans.py` + `ledger.py` |
| 交易前数据门禁 | `scripts/pre_trade_gate.py` | 重构为 `sessions.py` + `staging.py` 调用 |
| 目标快照 | `scripts/build_target_snapshot.py` + `state/target_snapshot.json` | 并入 `targets.py`；注意该文件当前被 tracked，需移出 |
| 模拟盘 fail-closed 校验 | `lb_order_map` / `lb_rebalance` | 移到 `policy.py` + `lb_paper.py` |

### 5. 隐私项需要落实为具体命令
任务书只写"标注保留/脱敏/移出 tracked"。**建议直接给出操作**：
```
.gitignore 追加：state/ logs/ reports/ snapshots/ .env data/cache/
git rm --cached -r state logs reports snapshots
```
并注明：仓库已 private；公开前需在 bare mirror 清洗历史（任务书已说"另案"，但应加"公开前禁止"）。

### 6. 日历依赖风险要在 Task 1 里写成验收条件
`exchange_calendars==4.13.2` × `pandas==3.1.0` × `numpy==2.4.6` × Python 3.13 的兼容性未知；
若失败，任务书要求"提交报告不改未锁定版本"是对的，但**没有 Plan B**。
**建议**：Task 1 增加"若安装/兼容失败 → 允许 vendored 交易日历表（XSHG/XHKG/XNYS 2024-2027）作为 fallback"，
并把这个 fallback 写进 spec 6.1。

## 三、建议优化（非阻塞）

7. **一次性执行窗口的「错过」语义**：spec 8.5 说非调仓日只在 `eligible_execution_session` 当天可执行；
   建议显式加一条：错过即记 `SKIPPED_MISSED_WINDOW`，**绝不自动补单**（避免补跑引入未来信息）。
8. **单测必须补 49/50/51/99/100/101 边界**（任务书 Task 提到 staging 分块，但没写测试点）：
   这是本轮真实踩过的崩溃（`flush_partial` 未定义即调用），必须回归覆盖。
9. **任务书的「每任务等外部审核」对单代理执行过重**：建议改为
   「P0/P1 各一次 checkpoint 审核 + P2–P7 每阶段一次」，否则往返成本高于收益。
10. **最小 Python 版本要写死**：当前代码用了 3.12+ 的嵌套同引号 f-string，CI 已改 3.13；
    spec 应声明 "requires-python >= 3.12"，避免再出现 3.11 CI 全红。
11. **spec 11.1 建议补一条**：账户身份证明采用「本地 `enrollment_id` UUID + 用户确认文件」，
    而不是任何从 token 推导的成员 ID（token 轮换不得改变 enrollment）。
12. **回滚定义**：建议写"回滚 = git revert 到 tag + 任务保持 Disabled + 删除 state/ledger 后从 staging 重建"，
    明确"快照可重建、数据可重抓"，避免"回滚需要人工恢复数据"。

## 四、结论

- **spec**：7.5/10，安全不变量写得很扎实；补上「日历 fallback」「执行窗口错过语义」「enrollment 定义」即可 9。
- **plan**：6.5/10，阶段划分正确；但 3 个阻塞项（未 push、缺 P-1 授权核验、缺同步协议）必须先补，
  否则执行代理会在 P5 卡死或产出无法合并的分支。
- 建议改动量很小：任务书加 1 个 Task 0.5、1 个 P6.5、1 张现状映射表、几条 .gitignore 命令。
