# 迭代日志 0014 · Longbridge 交易权限诊断（2026-10-23）

## 现象

两次 OAuth 授权都成功（token valid，dc_region=ap），但：

| 接口 | 结果 |
|---|---|
| quote（行情） | ✅ 正常 |
| order buy 预览（dry run） | ✅ 正常（本地计算，不调账户接口） |
| assets / positions / order list | ❌ 403308: Target API's scope is not in authorized scopes |
| auth status 里的 account | account_no=None, account_type=None, channel=None |

结论：**当前 OAuth client 只拿到了行情 scope，没有账户/交易 scope。**
重新登录不能解决——scope 由 client 注册决定，不是每次登录可勾选。

## 为什么

CLI（`longbridge` v0.30.0）用动态注册的默认 client（client_id 834f4357-…），
它只申请行情范围。交易需要：
1. 一个申请了**账户 + 交易** scope 的 OAuth client（在 Longbridge 控制台注册）；
2. 账户本身开通 OpenAPI 交易权限（通常还需 2FA）；
3. AP 账号下单还需要逐笔确认码（`--execute <CODE>`），这是券商侧风控。

## 需要用户提供的（二选一）

### A. 提供带交易权限的 OAuth client（推荐，能真正下单）

1. 打开 <https://open.longbridge.com/connect>（或 OpenAPI 控制台）注册应用；
2. 勾选权限：**账户、交易**（行情默认有）；
3. 把 **Client ID** 和 **Client Secret** 给我（或让我读环境变量）。

我会用它实现标准 OAuth 授权码流程（Python SDK），拿到带交易 scope 的 token，
然后用 `TradeContext` 下模拟盘单。token 由 SDK 管理，不写入仓库。

### B. 不开交易权限（保持现状）

Longbridge 只作为**实时报价源**：
- 本地引擎照常撮合（已可运行，10-23 已用真实报价执行过一次调仓）；
- 每日用实时报价执行挂单，记录真实滑点；
- 回测/历史数据继续用本地缓存（腾讯限流时切 BaoStock）。

## 顺带修复

`lb_bridge.order_list` 之前调用 `order list`（子命令不存在），
已改为不带子命令的 `order`（当日订单）+ `--history`（历史订单）。

## 当前系统状态（不依赖券商权限也可持续运行）

- 本地模拟盘：349 万、41 只持仓、pending 空（10-23 已按实时报价调仓）；
- 行情：Longbridge 实时报价可用；腾讯日线限流中；历史 K 线配额 0；
- 每日流程：`paper_live.py`（实时报价执行）→ 收盘后用本地数据源补日线 → `run_paper.py`。

## 下一步

1. 用户给 Client ID/Secret → 我接交易 scope，下模拟盘单做双轨对照；
2. 不给 → 我继续「本地引擎 + Longbridge 实时报价」的每日实操，并补 BaoStock 日更备用源。
