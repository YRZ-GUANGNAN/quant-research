# 迭代日志 0026 · 每日自动化完成（2026-10-23）

## 最后一块拼图：Windows 计划任务

```
schtasks /Create /TN "QuantDailyRun" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 15:10
→ SUCCESS
Next Run Time: 2026/10/23 15:10:00
Status: Ready
```

`daily_run.ps1` 语法校验通过（`[scriptblock]::Create` 解析 OK）。
从今天起，A 股收盘后（15:10）自动执行完整链路，无需人工触发。

## 自动化最终形态（6 步全自动）

```
15:10 触发 scripts/daily_run.ps1
 1. fetch_tx.py            腾讯补 A 股日线（限流则跳过）
 2. fetch_baostock_fallback.py  BaoStock 兜底增量
 3. run_paper.py           本地 A 股模拟盘推进（T+1/费用/滑点）
 4. paper_live.py          Longbridge 实时报价执行本地挂单
 5. lb_order_map.py        券商模拟盘美/港/SG 下单
      · 交易时段门控（market-status）
      · 按币种购买力预检（USD/HKD/SGD）
      · 幂等去重（订单∪持仓）
      · 三级降级（tick→最新价→市价）
 6. lb_daily_report.py     日度对照报告（按市场拆分+拒单归因）
```

## 风控与边界

- **只操作 Longbridge 模拟盘**，无实盘下单；
- 任何需要账户权限/人工确认的环节（HKD 换汇、融资提额、SG 覆盖）自动跳过并记录；
- 每笔委托走「预览→一次性确认码→提交」，天然防误单。

## 当前已知状态

| 市场 | 状态 |
|---|---|
| A 股 | 本地引擎正常运行（账户 349 万） |
| 美股 | 可下单（休市排队，开盘后成交） |
| 港股 | 可下单，但 HKD 现金 −304 万 → 自动跳过，等用户换汇/提额 |
| 新加坡 | 券商无报价，仅研究 |
| 加密 | 仅回测 |

## 监控方式

- 每日 15:10 自动运行，日志与报告落盘：
  - `reports/broker_daily_YYYY-MM-DD.{json,md}`
  - `logs/lb_order_map_result.json`
- 异常会打印在任务日志里（可通过 `schtasks /Query /TN QuantDailyRun /V` 查看上次结果）。

## 后续优化（非阻塞）

1. 美股开盘后复核 NotReported 成交；
2. 港股换汇/提额后自动恢复（脚本已能识别 HKD 正负）；
3. SG 等券商覆盖后再接入；
4. 把「按市场」滑点纳入周度汇总。
