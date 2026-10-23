# Longbridge 接入配置（给用户的最小操作清单）

## 已完成（我这边的动作）

| 项 | 状态 |
|---|---|
| `longbridge` 官方技能 | ✅ 已安装（`~/.agents/skills/longbridge`） |
| Longbridge CLI v0.30.0 | ✅ 已安装 → `%LOCALAPPDATA%\Programs\longbridge\longbridge.exe` |
| Python SDK | ✅ 已 `pip install longbridge`（备用，不接触 token） |
| 桥接代码 | ✅ `quant/broker/lb_bridge.py`（认证/账户/行情/下单） |
| 检查脚本 | ✅ `scripts/lb_check.py` |

## 需要你做的一步：授权码（OAuth）

Longbridge 用 OAuth 2.0，**不需要** App Key/Secret。两种方式任选：

### 方式 A（推荐，给 AI 用，最快）
1. 打开 <https://open.longbridge.com/connect> 并登录你的 Longbridge 账号；
2. 生成一个 **authorization code**（一次性）；
3. 把 code 发给我，我执行：
   ```powershell
   longbridge auth login --auth-code <CODE>
   ```
4. 成功后我立刻跑 `scripts/lb_check.py` 验证账户。

> 注意：code 是一次性的，几分钟内有效；只给我 code，不要给密码。

### 方式 B（设备码流程，无需复制 code）
我运行 `longbridge auth login`，它会打印一个 URL 和一个码，你在浏览器里确认即可。
你有空盯着屏幕时用这个方式也行。

## 还需要你确认两件事

1. **账号类型**：是 **模拟盘（Paper）** 还是实盘？本项目的操作目标先接模拟盘。
2. **区域**：账号是 AP（Longbridge SG/HK）还是 US？
   - 中国大陆访问 US 账号需要 `LONGBRIDGE_REGION=global`，否则登录页不提供 US 账号。
   - AP 账号用默认自动选择即可。

## 接上之后我会做什么

1. `lb_check.py`：认证状态、资产、持仓（JSON）；
2. 用 Longbridge 模拟盘账户与本地模拟盘做**双轨对照**：
   - 本地引擎：按我们的 T+1/涨跌停/费用假设撮合；
   - 券商模拟盘：真实撮合与真实行情；
   - 每日比较两者成交价、滑点、净值差异；
3. 用 Longbridge 的实时/日线行情补充数据源（解决腾讯限流问题）：
   `600519.SH` / `000001.SZ` / `700.HK` / `AAPL.US`，符号格式见技能文档；
4. 把差异写入 `docs/journal/`，作为「回测假设 vs 真实撮合」的实证。

## 安全边界

- OAuth token 由 CLI 保存在 `~/.longbridge/openapi/tokens/`，**本项目的代码不读取、不打印、不提交**；
- 本仓库 `.gitignore` 已排除 `.env`，也不会写入任何凭据；
- 所有下单动作只会在你明确确认「接模拟盘」后执行。
