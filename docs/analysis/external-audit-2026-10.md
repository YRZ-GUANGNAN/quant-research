# 外部审查回应：已修 / 未修清单（2026-10-24）

审查来源：外部对 main(2f4fcfc) 的只读审计。以下按严重度逐条回应。

## 严重问题

| # | 问题 | 状态 |
|---|---|---|
| 1 | 汇率收益 ffill 重复计算（美股 +22.54%、港股 +38.35% 虚高） | ✅ **已修**：改为「价格先并集对齐+ffill，再 pct_change」。复算：美股 +22.45% → **-1.45%**，港股 +46.88% → **-2.44%**。README 人民币口径结果已加横幅撤回，待全量重算 |
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
