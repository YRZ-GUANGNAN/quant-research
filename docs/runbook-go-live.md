# 上实钱操作手册（go-live runbook）

> 原则：**先模拟稳定，再实钱**。闸门只判定，最后一步必须由人按。
> 这份手册把"按什么顺序、看哪些文件、失败怎么退"写死，避免临场凭感觉。

---

## 0. 一句话前提

只有当 `reports/promotion_status.json` 里 `ready_for_real = true`（六项硬指标全绿）时，
才允许进入本手册第 3 步（切换真实资金）。在此之前，一切都在模拟盘。

```powershell
python scripts/promotion_check.py     # 全绿才算过
```

---

## 1. 切换前的四项体检（缺一不可）

| # | 检查 | 命令 | 通过标准 |
|---|---|---|---|
| 1 | 环境自检 | `python scripts/preflight.py` | 凭证 PASS、磁盘无 FAIL、通道 PASS |
| 2 | 数据门禁 | `python scripts/pre_trade_gate.py --markets cn,hk`（美股用 `--markets us`） | 每个市场 PASS |
| 3 | 权重漂移 | `python scripts/weight_drift.py --markets us,hk` | 需换手 ≤ 20% |
| 4 | 下单前模拟 | `python scripts/simulate_plan.py --market us --cash-share 0.35 --vol-target 0.15` | `problems` 为空、现金为正 |

四项任一不过，**当天不上**。这不是保守，是因为这四项里任何一项失败，实盘都会真金白银出错。

---

## 2. 连续 3 个交易日的观察清单

每天收盘后看这五个文件，全部正常再继续：

| 文件 | 看什么 | 异常阈值 |
|---|---|---|
| `reports/post_check_<日期>.json` | `warns` 数量 | 出现 FAIL 即停 |
| `reports/reconcile_<市场>.json` | `mismatched` 里有 `missing` | 有 missing 即查原因 |
| `reports/execution_quality.json` | 成交率 / 滑点中位 | 成交率 < 95% 或滑点中位 > 30bps |
| `reports/weight_drift.json` | 实际 vs 目标 | 需换手 > 25% 连续两天 |
| `state/ALERT.json` | 是否存在 | 存在即处理 |

---

## 3. 切换到真实资金（按顺序，一步一验）

### 3.1 美股/港股（Longbridge）

```powershell
# 1) 确认当前是模拟账户
longbridge auth status        # Account 行应显示模拟账户
# 2) 切真实账户后，先把执行环境改成 real（默认 paper 会拒绝提交）
$env:LB_ACCOUNT_KIND = "real"
# 3) 单笔/单日限额先收紧到 1/5，跑一周
#    在 scripts/us_open_run.ps1 / daily_run.ps1 里把 Policy 的
#    max_notional_per_order_usd 与 max_notional_per_day_usd 调小
```

### 3.2 A 股（同花顺）

```powershell
# 1) 客户端登录「真实资金账户」，勾选记住密码并保持在线
# 2) 把账户总资产上限设成真实账户的规模（默认 30 万是模拟阶段的上限，会拦住真实账户）
$env:CN_MAX_EQUITY = "300000"     # 按实际规模调整，先小后大
$env:CN_ALLOW_CLIPBOARD = "0"     # 真实券商有防拷贝风控，必须关掉剪贴板读表
# 3) 先只开风控（止损/减仓），不开新开仓
#    在 scripts/cn_open_run.ps1 里把目标权重的 top 设小、exposure 设低
```

### 3.3 首日只做"减法"
第一天只允许**卖出/减仓**动作（止损、清仓、降杠杆），不做新开仓。
理由：卖出错了顶多少赚，买入错了直接亏本金。第二天再放开买入。

---

## 4. 回滚（任何时候发现问题）

```powershell
# 立刻止血：关掉所有真实执行开关
$env:LB_ALLOW_EXECUTION   = "0"
$env:RISK_ALLOW_EXECUTION = "0"
$env:CN_ALLOW_EXECUTION   = "0"

# 确认没有挂单（有就先撤）
longbridge order
longbridge order cancel <order_id> --execute <code>

# 记录：把当天 reports/ 与 state/ 相关文件提交进 git，便于事后复盘
git add reports state && git commit -m "incident: <一句话>"
```

---

## 5. 什么时候该退回模拟

任一条命中就退回：

- 连续两个交易日 `reconcile` 出现 `missing`
- 成交率跌破 90%
- 单日实际亏损超过账户 3%
- 出现任何一次"以为下了其实没下"（`state/*_orders_*.jsonl` 与券商委托不一致）
- 看门狗连续两天 ALERT

---

## 6. 责任边界（写给自己看）

- 模型给的是**目标权重**，不是指令；执行层有额度、换手、集中度上限，任何一层都可以否决模型
- 闸门通过只代表"这段模拟表现够稳定"，**不代表未来会赚钱**
- 所有"更好"的结论都必须有实验记录（`docs/analysis/exp-*.md`），否则视为猜测
