# 迭代日志 0017 · SDK 凭据可用但账户无交易权益（2026-10-23）

## 进展

1. `.env` 已写入 App Key（35 字符）、App Secret（64 字符）、Access Token（1064 字符）；
   `credential_status()` 显示 `ready=True`，且 `.env` 确认被 git 忽略。
2. 修复 SDK 用法：`Config` 不能直接构造，必须 `Config.from_apikey(app_key, app_secret, access_token)`。
   这是 SDK 的 pyo3 绑定特性（`from_apikey` / `from_apikey_env` / `from_oauth` 三个工厂）。

## 新错误与含义

| 接口 | 错误 |
|---|---|
| account_balance | `202201: get userinfo error` |
| positions | `code=2: internal server error` |
| today_orders | `602001: user not found` |

三个域名（`openapi.longbridge.com` / `.cn` / `.hk`）中前两个返回 202201，`.hk` 不可达。
HMAC 签名本身通过（否则会是鉴权错误），说明**凭据正确**；`get userinfo error`
指向账户侧：该账号没有可交易的账户/OpenAPI 交易权益。

与截图证据一致：**交易权限页为空**。控制台右上角有「开通模拟账号」按钮——
未开通模拟账号时，交易类接口没有 userinfo 可返回，于是报 202201/602001。

## 结论

- 方案 A 的最后一步不是配置，而是**开通模拟账号/交易权限**（账户持有人操作）；
- 开通后无需改代码，直接重跑 `python scripts/lb_sdk_check.py` 即可验证；
- 在此之前保持方案 B：本地引擎 + Longbridge 实时报价（已运行，账户 348.9 万、41 只持仓）。

## 安全

Access Token 与 App Secret 已在截图/对话中暴露，测试完成后应到控制台「重置 Token」。
本仓库不含凭据：`.env` 在 `.gitignore` 第 18 行，`git check-ignore` 已验证。

## 下一步

1. 用户在控制台点「开通模拟账号」→ 回我一声，我重跑自检；
2. 若开通后仍报 202201，则是 OpenAPI 交易权益需单独申请（联系 Longbridge 支持）；
3. 方案 B 照常每日执行，不受影响。
