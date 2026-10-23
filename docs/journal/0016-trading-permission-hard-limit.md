# 迭代日志 0016 · 交易权限的硬边界与方案切换（2026-10-23）

## 决定性证据

用户提供的 Longbridge 控制台截图：

- **行情权限：已开通 6/9**（US LV1/Nasdaq Basic、HK LV2/恒指、CN LV1、SG LV1）
- **交易权限：暂无权限信息**（空）

结论：**该账户没有 OpenAPI 交易权限**。这不是 OAuth scope、也不是 App Key 的问题——
没有交易权限，任何凭据都无法下单。之前所有 403308 的根因到此闭环。

## 所以方案 A 暂停

需要的不是密钥，而是**账户级交易能力**：

1. 开通 Longbridge 证券账户（HK/US 需开户+入金，视地区合规）；
2. 在控制台申请/开通「交易权限」（OpenAPI 交易，通常需 2FA）；
3. 之后交易权限页会显示已开通项，App Key / Secret / Access Token 才有下单能力；
4. 在那之前，`lb_sdk_check.py` 会一直报 403308，属预期。

## 当前生效方案：B（本地引擎 + 实时报价）

- Longbridge 实时报价可用（US/HK/CN），用于执行模拟盘挂单；
- 本地引擎负责撮合（T+1/整手/涨跌停/佣金印花税/滑点）；
- 10-23 已用真实报价完成一次调仓：1 笔买入、费用 5.11 元，账户 348.9 万、41 只持仓；
- 数据源：腾讯日线限流中 → 新增 `scripts/fetch_baostock_fallback.py`（单线程增量、可重跑）。

## 每日运行顺序（方案 B）

```powershell
# 收盘后
python scripts/fetch_tx.py --workers 8          # 腾讯；失败则用下一行
python scripts/fetch_baostock_fallback.py       # BaoStock 备用（单线程、增量）
python scripts/run_paper.py                     # 推进日线账户
# 盘中需要执行挂单时
python scripts/paper_live.py                    # Longbridge 实时报价撮合
```

## 反思

这一轮最值得记的是：**先确认能力边界，再谈配置**。
我们花了三轮在 OAuth scope 上打转，最后一张截图（交易权限为空）才是根因。
如果一开始就要求看控制台权限页，可以省掉两轮实验。

下次接任何券商/API，第一步固定为：
1. 账户具备哪些权限（截图/控制台）；
2. 权限对应哪些接口；
3. 再决定用哪套认证。
