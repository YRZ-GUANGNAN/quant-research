# 归档核验（ARCHIVE CHECK）

- 核验时间：2026-10-23 10:31（Asia/Shanghai）
- 核验方式：只读检查 + 测试重跑，不做任何策略改动
- 目的：确认项目暂停/结项时资料完整、可复现、已同步 GitHub

## 1. 版本控制状态

| 项目 | 结果 |
|---|---|
| 当前提交 | `3243b1b` |
| 远端 main | `3243b1b`（一致，无未推送提交） |
| 受控文件数 | 99 |
| 工作区 | 干净（无未提交改动） |

## 2. 关键资料清单

| 类别 | 数量 | 状态 |
|---|---|---|
| 决策文档（docs/decisions） | 9 份（0001–0009） | ✅ 全部在位，每份含 10 条攻击性自问 |
| 迭代日志（docs/journal） | 6 份（0001–0006） | ✅ 含失败记录与自我修正 |
| 模型规格（models/） | v1 / v2 / v3 / v4 | ✅ 均为合法 JSON |
| 评估报告（reports/） | 评估 JSON + 跨市场结果 + 净值 | ✅ |
| 快照（snapshots/） | v4 目标组合、A 股目标、模拟盘账户 | ✅ |
| 交接文档 | docs/HANDOFF.md | ✅ |
| 开源生态调研 | docs/research/ | ✅ |
| 测试 | tests/ 共 35 个 | ✅ 全部通过 |

## 3. 可复现性检查

```powershell
cd D:\3314\quant-research
python -m pytest tests -q                     # 35 passed
python scripts/evaluate.py --strategy ic_tilt --ensemble --topk 40 --rebalance 35 --phases 0,5,10,15
python scripts/portfolio.py --phases 0,5,10,15
```

数据缓存（`data/cache/`，约 40MB）不进 git，但可由以下命令完整重建：
```powershell
python scripts/fetch_data.py --start 2018-01-01   # BaoStock：指数 + 时点成分股
python scripts/fetch_tx.py                        # 腾讯：A 股日线（558 只约 72 秒）
python scripts/fetch_market.py --market all       # Yahoo：美股/港股/加密
```

## 4. 结项时的模型结论

**模型 v4（A60/美30/港10，4 相位复核）**

| 指标 | 数值 |
|---|---|
| 年化收益 | 15.32% ± 0.53% |
| 夏普比率 | 0.95 |
| 最大回撤 | -23.50% |
| 月胜率 | 58.1% ± 0.7% |
| 季胜率 | 66.4% |
| 12 个月胜率 | 87.2% |

满足决策 0007/0009 的 S1–S9 标准，即「稳定 + 大部分情况盈利」。
**限制（必须随结论一起引用）**：未计汇率、不同交易日历按并集近似、
非 A 股为静态名单（幸存者偏差）、A 股袖子样本外超额接近 0。
因此这是**研究结论**，不是可实盘组合。

## 5. 明确未完成、留给下一阶段的事项

1. 补汇率（USD/CNY、HKD/CNY），把 v4 变成可估值组合；
2. 2027+ 新样本外数据复核 S1–S9（唯一没被污染的检验）；
3. 共识变化阈值：A 股袖子换手 10.4x → 8x；
4. 中证500 宇宙验证 amihud 溢价（新宇宙 = 新样本外）；
5. IC 闸门（决策 0006 的失败项）等新数据再检验。

## 6. 结论

**归档完整、可复现、已同步远端。** 项目可以安全暂停；
恢复时从 `docs/HANDOFF.md` 的「如何继续」一节开始即可。
