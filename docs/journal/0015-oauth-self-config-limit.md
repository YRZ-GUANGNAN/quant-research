# 迭代日志 0015 · 自我配置 OAuth 的极限（2026-10-23）

## 我尝试了什么（全自动，不需要用户密钥）

1. **动态注册 OAuth 客户端**：`POST https://openapi.longbridge.com/oauth2/register`
   - 成功返回：`client_id=779863fe-…`（client_secret_post）、`client_id=ae36bff8-…`（public/none）
   - 服务端自动授予 scope：**"4 6 10 11 12"**（数字 ID，不可自定义）
2. **自建授权流程**：`scripts/lb_oauth.py` 用 SDK `OAuthBuilder` 启动 localhost:60355 回调，
   用户在浏览器授权 → token 交换成功。
3. **测试交易接口**：`TradeContext.account_balance()` / `stock_positions()`
   → **仍然 403308: Target API's scope is not in authorized scopes**。
4. 对照：CLI 的 OAuth（设备码/授权码）同样只拿到行情 scope；
   `auth status` 的 `account_no` 始终为 None。

## 结论：self-configuration 的边界

**动态注册的 client 只被授予行情类 scope，交易/账户 scope 无法自助获得。**
这是服务端策略，不是代码问题：
- 交易 scope 绑定到「在 Longbridge 开发者控制台注册并通过的应用」；
- 且账户需开通 OpenAPI 交易权限（通常含 2FA / 模拟盘开通）；
- 客户端无法通过注册时请求 scope 参数绕过（服务端返回固定 scope 集合）。

因此「完全自我完成配置」不可行，**必须由账户持有人提供**以下之一：
1. 开发者控制台里**带账户+交易权限**的应用的 `App Key / App Secret / Access Token`；
2. 或一个已获交易 scope 的 OAuth client_id（+secret）。

## 能做到的替代方案（已跑通）

- Longbridge **实时报价**可用（HK/US/A 股）；
- `scripts/paper_live.py` 已用真实报价执行本地模拟盘调仓（10-23：1 笔买入、费用 5.11 元）；
- 因此系统可以持续「真实行情 + 本地撮合」运行，不需要交易权限。

## 现状

- 本地模拟盘：348.9 万、41 只持仓、pending 空；
- 券商交易：行情通、交易阻塞（403308）；
- 自注册 client 已创建（未使用，不影响环境）。

## 下一步

1. 若有交易需求：用户提供开发者控制台的 App Key/Secret/Access Token（或开通交易权限后告诉我）；
2. 若无：保持「本地引擎 + 实时报价」每日实操，并补 BaoStock 日更备用源；
3. 记录：不要把「能注册 OAuth client」误当成「能交易」。
