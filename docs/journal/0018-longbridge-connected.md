# 迭代日志 0018 · Longbridge 正式接通（2026-10-23 11:23）

## 按官方三步执行

1. **更新 CLI**：`longbridge update` → Already up to date (v0.30.0)
2. **兑换授权码**：`longbridge auth login --auth-code <CODE>` → `Successfully authenticated.`，token status=valid
3. **验证**：`longbridge quote NVDA.US` →
   `{symbol: NVDA.US, last: 212.210, change: -9.760, change_pct: -4.40%}`

## 关键突破：账户接口这次可用

```
longbridge assets --format json
→ [{"currency":"USD","net_assets":"2020585.42", ...}]

longbridge positions --format json
→ []   （空仓，符合新模拟账号预期）
```

对比之前：同样的 API 一直返回 `403308 scope not authorized` / `202201 get userinfo error`。
**区别在于授权来源**：这次授权码来自带交易权限的授权入口（用户已在控制台开通模拟账号），
token 的 scope 包含账户/交易，所以 assets/positions 直接通过。

结论：之前三轮排查的根因（账户无交易权限 / 未开通模拟账号）已被用户侧操作解决。

## 当前能力清单

| 能力 | 状态 |
|---|---|
| 实时行情 quote | ✅ NVDA.US 212.21 |
| 资产 assets | ✅ USD 2,020,585.42 |
| 持仓 positions | ✅ 空仓 |
| 下单 order | 待验证（CLI 预览可用；AP 账号真实下单需 `--execute <CODE>`） |
| 历史 K 线 | 待验证（此前配额 0，可能随模拟账号开通变化） |

## 下一步

1. 测试 `order buy` 预览 → 真实提交（用极小单，如 1 股 NVDA，确认撮合链路）；
2. 确认该模拟账号是否支持 A 股/港股（符号 `600519.SH` / `700.HK`）；
3. 若支持，把本地模型的目标组合映射到券商模拟盘，做「本地引擎 vs 券商撮合」双轨对照；
4. 更新 `lb_bridge.order_buy/sell` 支持 `--execute <CODE>` 参数。

## 安全

本 token 同样只存在 `~/.longbridge` 与本地 `.env`，未进 git。
建议测试完成后重置 token。
