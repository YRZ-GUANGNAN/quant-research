# 迭代日志 0012 · 插件市场操作、软件安装与模拟盘实操状态（2026-10-23）

## 本轮做了什么

### 1. 技能市场（skills.sh）操作

用 `npx skills find` 检索 quant / trading / finance / backtest / stock market，只装高安装量、来源可信的：

| 技能 | 来源 | 安装量 | 状态 |
|---|---|---|---|
| `backtesting-frameworks` | wshobson/agents | 16.6K | ✅ 已进入技能目录 |
| `longbridge` | longbridge/skills（官方券商） | 4.3K | ✅ 已进入技能目录 |

其余检索结果（sales / resume / 第三方 stock-analysis）安装量低或与项目无关，**未安装**——
不为了「补充插件」而装低质量包。

### 2. 缺失软件安装

- `longbridge` Python SDK：`pip install longbridge` ✅ 安装成功、可 import。
- 说明：Longbridge 实盘/模拟盘交易需要 App Key / App Secret / Access Token，
  当前环境没有这些凭据，因此**只能装软件，暂时无法接入其行情与交易接口**。

### 3. 模拟盘实操状态

- 增量更新 A 股行情失败：腾讯接口对全部 563 只返回 HTTP 错误（临时限流/封禁），
  单只重试仍失败 → 数据仍停在 **2026-10-22**。
- 模拟盘推进结果：账户保持 10-22 状态，**41 只持仓、总资产 349.3 万、累计 +249.30%**，
  次日待执行目标 **40 只（总仓位 98%）**。
- 今日（10-23）尚在交易时段，日线未收盘，本系统按「t 收盘算信号、t+1 开盘成交」设计，
  因此 **10-23 的调仓要等收盘数据入库后才会真正执行**。这不是故障，是日频规则。
- 待执行指令已快照：`snapshots/pending_orders_20261023.txt`。

## 阻塞项（如实记录）

| 阻塞 | 影响 | 备选方案 |
|---|---|---|
| 腾讯行情限流（HTTP 错误） | 无法增量更新 A 股日线 | ① 等限流恢复后重跑 `fetch_tx.py`；② 切 BaoStock（此前也限流过）；③ 装 Longbridge SDK 后接官方行情（需凭据） |
| Longbridge 无 API 凭据 | 无法接券商模拟盘/实盘 | 需要用户提供 App Key/Secret；否则继续用本地模拟盘引擎 |
| 今日日线未收盘 | 10-23 调仓无法执行 | 收盘后（≥15:00）跑 `fetch_tx.py` + `run_paper.py` |

## 结论

- 插件/软件补充完成（2 个技能 + 1 个 SDK），但券商实盘通道因**缺少 API 凭据**未打通。
- 模拟盘引擎本身正常运行：账户、持仓、待执行目标都已就绪；
  真正的「下一笔成交」取决于①行情恢复 ②收盘数据入库。
- 不把「装好插件」说成「已开始实盘交易」——这两件事必须分开。

## 下一步

1. 收盘后重跑：`python scripts/fetch_tx.py --workers 8` → `python scripts/run_paper.py`；
2. 若腾讯持续不可用，写一个 BaoStock 增量补充脚本作为备用源；
3. 若用户提供 Longbridge 凭据，接入其模拟盘做「真实撮合」对照（本项目引擎 vs 券商模拟盘）。
