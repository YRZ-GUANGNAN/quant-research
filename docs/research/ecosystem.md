# 2025–2026 开源量化工具生态调研（A 股本地系统视角）

> **调研时点**：2026-10-22（北京时间）。所有 star / 最后提交 / 版本号均在该时点实时抓取，**不是记忆值**。
> **核实规则**：带来源链接的为已核实；标注「未核实」的表示本轮未找到可靠公开依据，使用前必须自己复核。
> **统计口径**：star、fork、`pushed_at`、`archived` 来自 GitHub REST API（`https://api.github.com/repos/<owner>/<repo>`）；版本号、上传时间、`requires_python`、Python 版本 classifier 来自 PyPI JSON API（`https://pypi.org/pypi/<pkg>/json`）。注意 `pushed_at` 会被分支/tag 推送刷新，因此对疑似停更的项目我另外查了默认分支最近 commit 时间。

---

## 0. 一页结论（TL;DR）

1. **A 股回测的「规则正确性」比「回测框架性能」重要一个数量级。** 2026-07-06 起沪深主板 ST/*ST 涨跌幅由 5% 改为 10%、盘后固定价格交易扩到全部 A 股与 ETF（15:05–15:30，收盘价成交、申报不一定成交）——任何硬编码「ST=5%」或假设「收盘价一定能成交」的回测都会产生幽灵收益。来源：[证券时报 2026-07-06](https://stcn.com/article/detail/3999185.html)、[新浪财经/中国基金报 2026-07-05](https://finance.sina.com.cn/wm/2026-07-05/doc-inifsuvq9174512.shtml)。
2. **backtrader 事实上已停更**：默认分支最后提交 2023-04-19，最后一次 PyPI 发布 2023-04-19；仓库 `pushed_at` 2024-08-19 只是分支/tag 级动作，不代表默认分支仍在维护。2026 年新项目不应以它为主引擎。
3. **真正「为 A 股而生」且仍在维护的成熟选项是 RQAlpha**（v6.4.2，2026-10-20 发布，Python 3.10–3.14 classifier；内置涨跌停/停牌/成交量/税费 mod），代价是**非商业许可**与数据 bundle 依赖米筐服务器。
4. **2026 年出现的新变量是 AKQuant**（AKShare 团队，2026-01-30 建仓，已 2449 star，v0.3.71 日更，Rust 内核、MIT 许可、内置 T+1 与涨跌停的 golden test）。它最贴合 A 股 + ML 研究流，但**版本号 0.3.x，属于「可以押注、不能托付」的阶段**。
5. **qlib 不是「开箱即用平台」**：官方数据集因数据安全策略已停用（README 明写 "The official dataset is disabled temporarily"），官方支持表只到 Python 3.12，3.13 + NumPy 2.x 有崩溃 issue。它更适合当「Alpha158/Alpha360 因子集 + 模型 zoo + 工作流范式」的参考实现。
6. **数据层是最大的单点风险**：AKShare 免费但反爬/限流/接口改名频繁（官方 FAQ 直接教你「换 IP、降频、重试」），Tushare 需 token + 积分（120 分只能拿非复权日线），BaoStock 免费且有 `tradestatus`/`isST`/`adjustflag` 字段适合 A 股，但覆盖与更新时效需自建校验。

---

## 1. 回测框架现状对比

### 1.1 主表（2026-10-22 实测）

| 框架 | Stars（等级） | 默认分支最后提交 | 最新版本（日期） | Py 3.13 | A 股规则天然支持 | 学习成本 | 维护状态判断 |
|---|---|---|---|---|---|---|---|
| **vectorbt** | 9,489（1 万级） | 2026-10-19 | 1.2.0（2026-10-19） | ✅ 3.11–3.14 | ❌ 无涨跌停/停牌/限价单（限价单是 PRO 功能） | 中 | 活跃（开源社区版，PRO 闭源付费） |
| **backtrader** | 23,543（2 万级） | **2023-04-19** | 1.9.78.123（2023-04-19） | ⚠️ 无声明（未核实） | ❌ 需自写 | 低–中（教程最多） | **事实停更** |
| **zipline-reloaded** | 1,968 | 2026-10-19 | 3.2.0（2026-10-18） | ✅ 3.11–3.14（pandas<3.0） | ❌ 美股为主，A 股需自建 bundle | 高 | 维护模式（新功能转向 ml4t/backtest） |
| **bt** | 3,000 | 2026-10-20 | 1.4.1（2026-10-10） | ✅ 3.10–3.13 | ❌ 组合权重回测，无个股撮合 | 低 | 活跃 |
| **qlib** | 49,594（5 万级） | 2026-10-22 | pyqlib 0.9.7（2025-08-15） | ❌ 官方表到 3.12 | ⚠️ 有 cn 数据管线，但官方数据集已停用 | 高 | 活跃（研究平台） |
| **nautilus_trader** | 30,593（3 万级） | 2026-10-22 | 1.231.0（2026-08-02）；v2.0.0rc 预发布 | ✅ 3.12–3.14（官方平台表写 3.13–3.14） | ❌ 无 A 股券商/数据 adapter | 很高 | 活跃（v1 仅安全回补，v2 RC） |
| **backtesting.py** | 9,106 | 2026-08-05 | 0.6.6（2026-07-22） | ⚠️ 无 requires-python（未核实） | ❌ 单标的、无 A 股规则 | 低 | 活跃（小步维护） |
| **RQAlpha** | 6,836 | 2026-10-22 | 6.4.2（2026-10-20） | ✅ 3.10–3.14 | ✅✅ 涨跌停/停牌/成交量/税费内置 | 中 | 活跃（米筐主导） |
| **vnpy** | 45,980（4.5 万级） | 2026-10-06 | 4.5.0（2026-10-06） | ✅ 3.10–3.13 | ⚠️ 股票接口靠网关（vnpy_tora 等），期货/期权是主场 | 高 | 活跃 |
| **hikyuu** | 3,570 | 2026-10-22 | 2.8.3（2026-09-30） | ✅ 3.10–3.13 | ⚠️ A 股本地数据是强项，撮合规则支持未核实 | 中–高 | 活跃 |
| **QUANTAXIS** | 11,301 | 2026-09-18 | —（未逐一核实） | 未核实 | ⚠️ A 股全流程平台（模块多，质量需自检） | 高 | 活跃但工程重 |
| **AKQuant**（2026 新） | 2,449 | 2026-10-22 | 0.3.71（2026-10-22） | ✅ ≥3.10（classifier 未标 3.13） | ✅✅ 内置 T+1/涨跌停 golden test | 中 | 极活跃、极年轻 |
| **PyBroker** | 3,635 | 2026-10-20 | lib-pybroker 2.0.2（2026-10-20） | ✅ ≥3.11 | ❌ 美股/加密为主 | 中 | 活跃 |

来源：[vectorbt](https://github.com/polakowo/vectorbt) · [backtrader](https://github.com/mementum/backtrader) · [zipline-reloaded](https://github.com/stefan-jansen/zipline-reloaded) · [bt](https://github.com/pmorissette/bt) · [qlib](https://github.com/microsoft/qlib) · [nautilus_trader](https://github.com/nautechsystems/nautilus_trader) · [backtesting.py](https://github.com/kernc/backtesting.py) · [rqalpha](https://github.com/ricequant/rqalpha) · [vnpy](https://github.com/vnpy/vnpy) · [hikyuu](https://github.com/fasiondog/hikyuu) · [QUANTAXIS](https://github.com/yutiansut/QUANTAXIS) · [AKQuant](https://github.com/akfamily/akquant) · [pybroker](https://github.com/edtechre/pybroker)。

### 1.2 逐个说明（只写有依据的部分）

**vectorbt —— 最好的「批量实验」工具，不是 A 股撮合引擎。**
- 已从 0.x 走到 v1.x：v1.1.0（2026-07-05）新增 Python 3.14 / pandas 3 / NumPy 2.4+ 支持；v1.2.0 为 2026-10-19。来源：[releases](https://github.com/polakowo/vectorbt/releases)。
- 开源版是 PRO 的社区版，README 明确把 **limit orders、leverage、contract multiplier 等列为 PRO 功能**；许可为 Apache 2.0 + Commons Clause（fair-code），**可以免费用，但不能拿它卖产品/服务**。来源：[README](https://github.com/polakowo/vectorbt/blob/master/README.md)。
- 对 A 股的直接影响：`Portfolio.from_signals` 只吃「信号」，涨跌停不可成交、停牌、成交量参与率都要自己编码进信号，否则回测偏乐观。

**backtrader —— 教程最多，但已经是「遗产项目」。**
- 默认分支最后提交 2023-04-19（`Version 1.9.78.123`），PyPI 最后发布同为 2023-04-19。仓库 `pushed_at` 显示 2024-08-19，但那是分支/tag 级动作，不代表代码维护。来源：[commits](https://github.com/mementum/backtrader/commits/master)、[PyPI](https://pypi.org/project/backtrader/)。
- 无 Python 版本 classifier、无 `requires_python`，因此 **Python 3.13 兼容性无官方声明（未核实）**。2026 年把它当主力，等于把维护责任接过来。

**zipline-reloaded —— 维护模式，作者自己指向后继项目。**
- README 原文："We continue to maintain Zipline Reloaded for bug, security and compatibility fixes. Our new feature development focuses on ML4T Backtest"。来源：[README](https://github.com/stefan-jansen/zipline-reloaded/blob/main/README.md)。
- 3.2.0 支持 Python 3.11–3.14，但约束 pandas<3.0；数据入门是 NASDAQ Data Link（需 API key），A 股要自己写 bundle + 自己实现 T+1/涨跌停，成本高。

**qlib —— 研究范式价值 > 平台价值。**
- 官方支持 Python 3.8–3.12，**3.13 不在列表内**；issue #2018（2025-09-23，仍 open）报告 Python 3.13 + NumPy 2.0 环境下 backtest 模块因 gym 依赖直接崩溃进程。来源：[README](https://github.com/microsoft/qlib/blob/main/README.md)、[issue #2018](https://github.com/microsoft/qlib/issues/2018)。
- **官方数据集已停用**：README "Due to more restrict data security policy. The official dataset is disabled temporarily."，官方建议用社区数据 `chenditc/investment_data`；且数据来自 Yahoo Finance，README 自认 "the data might not be perfect"。来源同上。
- 它的真正资产：Alpha158/Alpha360 因子集、LightGBM/Transformer/TFT 等 benchmark、点时间数据库（PIT）与 nested execution 设计。可借鉴，别照搬。

**nautilus_trader —— 最好的「事件驱动 + 实盘同构」引擎，但 A 股要自建整条通路。**
- Rust 内核 + Python 控制面，v1 稳定线 + v2 RC（`--pre` 安装 2.0.0rcN）；官方平台表写 Python 3.13–3.14。来源：[README](https://github.com/nautechsystems/nautilus_trader/blob/develop/README.md)。
- adapter 清单里是 Binance/Bybit/OKX/IB/Databento/Tardis 等，**没有沪深交易所、没有 A 股券商**；接 A 股要自己写 adapter + 数据标准化。学习曲线是全表最陡的。

**RQAlpha —— A 股规则建模最完整。**
- `sys_simulation` mod 的默认配置直接暴露了 A 股撮合要素：`matching_type`（current_bar / next_bar / vwap）、`price_limit`（涨跌停撮合限制，默认开）、`volume_limit`（默认每 bar 最多吃市场成交量的 0.25）、`inactive_limit`（无成交量标的不撮合）、`slippage_model`、`signal`（信号模式：**不撮合直接成交**，专门用来「屏蔽订单细节」）。来源：[sys_simulation README](https://github.com/ricequant/rqalpha/blob/master/rqalpha/mod/rqalpha_mod_sys_simulation/README.rst)。
- 税费 mod 默认：股票佣金万八、最低 5 元、印花税千分之一单边，并提供 `commission_multiplier` / `tax_multiplier` 调整。**这两个默认值都与现实不符**（见 §5.4），必须显式改。来源：[sys_transaction_cost README](https://github.com/ricequant/rqalpha/blob/master/rqalpha/mod/rqalpha_mod_sys_transaction_cost/README.rst)。
- 许可：README 写明「**仅限非商业使用**」。来源：[README](https://github.com/ricequant/rqalpha/blob/master/README.rst)。

**vnpy —— 期货/期权的工业级底座，股票是「后补的」。**
- 4.5.0（2026-10-06），Python 3.10–3.13。股票接口情况见官方论坛：4.0 的 station 未移植股票接口，4.1.0 起「直接 pip install 需要的接口就好」。来源：[vnpy 论坛 2025-06-25](https://www.vnpy.com/forum/topic/33929-xiao-bai-qiu-zhu-4-0jiao-yi-jie-kou-mei-you-agu-jie-kou)。
- 具体网关以 PyPI 为准：`vnpy_tora`（华鑫奇点，2026-10-08 更新）、`vnpy_ib`（2026-09-13）、`vnpy_ctp`（2026-03-29）等；`vnpy_emt`（东财）、`vnpy_ost` 最后发版均停留在 2022 年（疑似停更）。来源：[PyPI vnpy_tora](https://pypi.org/project/vnpy-tora/)、[vnpy_ctp](https://pypi.org/project/vnpy-ctp/)。

**AKQuant —— 2026 年最值得跟踪的新框架（详见 §6）。**
- 建仓 2026-01-30，2449 star；v0.3.71（2026-10-22）；Rust + Python，MIT；README 宣称内置 Walk-forward Validation、Polars 因子表达式（Alpha101 风格）、多进程网格搜索，且测试里有「黄金测试（Golden Tests）……验证关键业务逻辑（如 **T+1、涨跌停**、保证金、期权希腊值）」。来源：[README](https://github.com/akfamily/akquant/blob/master/README.md)、[PyPI](https://pypi.org/project/akquant/)。

**backtesting.py / bt / hikyuu / QUANTAXIS / PyBroker（简述）**
- backtesting.py：9,106 star，0.6.6（2026-07-22），单标的、API 极简，无 `requires-python` 声明（**3.13 未核实**），适合教学与快速验证，不适合 A 股组合。
- bt：3,000 star，1.4.1（2026-10-10），做「组合权重再平衡」类回测很顺手（`bt.Backtest`），但不管个股撮合与 A 股规则。
- hikyuu：3,570 star，C++ 内核 + Python 接口，2.8.3（2026-09-30），中文社区、A 股本地数据是强项；具体 T+1/涨跌停实现细节**未逐项核实**。
- QUANTAXIS：11,301 star，2026-09-18 仍有提交，A 股全流程（数据/回测/实盘）都有，但模块多、工程重、文档不成体系。
- PyBroker：3,635 star，`lib-pybroker` 2.0.2（2026-10-20），ML 友好（walk-forward 内置），但面向美股/加密。

---

## 2. 数据获取（A 股）

### 2.1 对比表（2026-10-22）

| 来源 | 最新版本（日期） | 费用 / 门槛 | 限流 | A 股覆盖 | 2025 后变化 | 主要风险 |
|---|---|---|---|---|---|---|
| **AKShare** | 1.19.1（2026-09-30） | 免费、无 token | 无官方配额，但**接口级反爬**（IP 封禁、502、403） | 全（行情/财务/基金/期货/宏观/打板） | 持续高频迭代，接口改名/移除频繁 | 反爬 + 接口漂移 + **静默返回坏数据** |
| **Tushare Pro** | 1.4.29（2026-03-25） | 免费额度极低，实际需积分/付费 | 120 分：50 次/分、8000 次/天 | 全（含复权因子、涨跌停价、停复牌、PIT 财务） | 积分为主的商业化模式已固化 | 积分门槛 + 部分数据单独收费 |
| **BaoStock** | 0.9.4（2026-09-21） | 免费、`login()` 无需注册 | 未见官方数值（未核实） | A 股日线/分钟、`adjustflag` 复权、`tradestatus`、`isST` | 仍在发版 | 无实时；覆盖与更新时效需自检 |
| **yfinance** | 1.7.0（2026-08-26） | 免费 | Yahoo 侧限流 | 美股/港股强，A 股（.SS/.SZ）质量一般 | v1.x 大版本，接口有变更 | 不适合当 A 股主数据源 |

来源：[AKShare PyPI](https://pypi.org/project/akshare/) · [AKShare 文档](https://akshare.akfamily.xyz/) · [Tushare 积分与频次权限表](https://tushare.pro/document/2?doc_id=290) · [BaoStock PyPI](https://pypi.org/project/baostock/) · [yfinance PyPI](https://pypi.org/project/yfinance/)。

### 2.2 AKShare：免费，但要接受「它会坏」

- 官方 FAQ 直接承认这类故障是常态：`ReadTimeout` 的处理办法是「重新运行 / 更换 IP（代理）/ 降低数据访问的频率」；「数据返回错位」的处理办法是「多运行几次 / 切换 IP 后重试」。官方还要求 **Python 3.11 以上，推荐 3.13.x 及以上**。来源：[AKShare 答疑专栏](https://akshare.akfamily.xyz/answer.html)。
- Issue 层面证据（2026 年仍活跃）：
  - #6061（2025-04-15，已关闭）「疑似使用了异步高并发导致东财大量封 IP」，复现方式是每 10 秒调 `stock_zh_a_spot_em()`，解决办法是换公网 IP + 降低并发。[链接](https://github.com/akfamily/akshare/issues/6061)
  - #7301（2026-06-05）`stock_individual_basic_info_xq` 403，端点被雪球限流。[链接](https://github.com/akfamily/akshare/issues/7301)
  - #7431（2026-09-24）`fund_etf_spot_em` 502。[链接](https://github.com/akfamily/akshare/issues/7431)
  - #7444（2026-10-16，open）传非法 symbol 时**静默回落到别的 URL**，抓完全部分页才抛 `Length mismatch`——这正是「静默错误」的典型样本。[链接](https://github.com/akfamily/akshare/issues/7444)
- 结论：AKShare 适合做「研究期数据」，正式系统必须加**数据校验层**（行数、日期连续性、价格合法性、复权因子单调性、与第二个源交叉比对）。

### 2.3 Tushare：不是「免费数据源」，是「付费数据服务」

官方积分表（2026 年仍在维护，页面版权 2026）关键行：

| 积分数 | 每分钟频次 | 每天总量 | 可访问接口 | 价格（元/年） |
|---|---|---|---|---|
| 120 | 50 | 8000 次 | **仅非复权日线**，其他接口无法调取 | 0 |
| 2000 以上 | 200 | 100,000 次/API | 按接口文档的积分要求 | 200 |
| 5000 以上 | 500 | 常规数据无上限 | 按接口文档 | 500 |
| 10000 以上 | 500 | 特色数据 300 次/分 | 盈利预测、筹码分布等 | 1000 |

分钟数据、港美股、公告、新闻等是**独立权限**（如历史分钟 2000 元、美股日线 2000 元）。来源：[Tushare 积分与频次权限对应表](https://tushare.pro/document/2?doc_id=290)。

> 注意：`waditu/tushare` 仓库最后提交是 2024-03-13，但 PyPI 包 1.4.29 发布于 2026-03-25——**仓库陈旧 ≠ 服务停摆**，判断这类 SaaS 要看服务端文档与包发布，而不是 GitHub 活跃度。来源：[repo](https://github.com/waditu/tushare)、[PyPI](https://pypi.org/project/tushare/)。

### 2.4 BaoStock：A 股回测的「合规底座」候选

- 官方定位：「Free china stock market data」「We have our own data server, efficient and stable operation」。来源：[PyPI](https://pypi.org/project/baostock/)。
- 字段层面对回测非常友好：`query_history_k_data_plus` 一次可取 `preclose, volume, amount, adjustflag, turn, tradestatus, pctChg, peTTM, pbMRQ, isST`，`adjustflag` 支持「不复权/前复权/后复权」。来源同上（PyPI 描述中的示例代码）。
- 缺口：无实时行情；是否支持全历史退市股、更新延迟多少，**本轮未核实**，落地前要用 2–3 只已知除权/退市股票做校验。

### 2.5 yfinance 与「Yahoo 系」A 股数据

- yfinance 1.7.0（2026-08-26）仍是美股/港股首选；A 股以 `.SS`/`.SZ` 后缀取 Yahoo 数据，质量与复权口径不稳定（**经验判断，本轮未系统验证**）。
- 旁证：Qlib 的官方 cn 数据集就是从 Yahoo 采集的，README 自己写「the data might not be perfect. We recommend users to prepare their own data」。来源：[qlib README](https://github.com/microsoft/qlib/blob/main/README.md)。
- 结论：**不要把 yfinance 作为 A 股主数据源**，最多做交叉校验。

---

## 3. 因子 / 机器学习

### 3.1 qlib 的真实门槛

| 门槛 | 具体表现 | 来源 |
|---|---|---|
| 数据格式 | 必须转成 qlib 专用 `.bin` 格式，官方提供 csv→bin 转换与 collector 脚本 | [qlib README / Data Preparation](https://github.com/microsoft/qlib/blob/main/README.md) |
| 数据获取 | 官方数据集**已停用**，需用社区数据（`chenditc/investment_data`），且数据源为 Yahoo，质量自担 | 同上 |
| Python | 官方支持 3.8–3.12；3.13 不在表内，且 3.13+NumPy2 有崩溃 issue | [issue #2018](https://github.com/microsoft/qlib/issues/2018) |
| 概念栈 | DataHandler / Dataset / Model / Strategy / Executor / nested execution / recorder 一套 DSL + YAML workflow，`qrun` 可一键但排错难 | [README](https://github.com/microsoft/qlib/blob/main/README.md) |
| 社区 | GitHub API 的 `open_issues_count` 为 503（含 PR，2026-10-22），维护活跃但 issue 消化慢 | GitHub API |

**正确用法**：把 qlib 当「因子库 + 模型 benchmark + PIT 数据设计参考」，只取 `Alpha158` 因子定义、LightGBM/Transformer 的 benchmark 配置来对标，不必把整条流水线搬进自己的仓库。

### 3.2 GBDT 仍是因子选股的主力基线

- LightGBM 4.7.0（2026-07-18，Python ≥3.10，classifier 覆盖 3.10–3.14）：[PyPI](https://pypi.org/project/lightgbm/)。
- XGBoost 3.4.1（2026-08-15，**requires_python ≥3.12**，classifier 3.12–3.14）：[PyPI](https://pypi.org/project/xgboost/)。
- 常规用法（qlib / AKQuant 都是这个范式）：截面因子 → 排序标签（如未来 N 日收益分位）→ GBDT 回归/分类 → 每日打分 → 选 top-k → 按行业/市值中性化 → 组合优化 → 撮合。
- 注意：**GBDT 不会自动解决数据泄漏**。标签必须用「T 日收盘后可知的信息」预测「T+1 之后可交易的收益」，且训练/验证要按时间切分并留 gap（purge/embargo）。

### 3.3 有没有「更现代」的替代？

- 有，但 2026 年的现实是：**PyTorch 时序模型在 A 股日频选股上通常打不过调好的 GBDT**（这是社区与从业者的普遍经验判断，本文未做独立复现，属「未核实」）；其价值更多在于多任务/多模态或高频。qlib 自带 LSTM/GRU/ALSTM/GATs/Transformer/Localformer/TFT/TabNet/TRA 等 benchmark，可直接对比。来源：[qlib README 的 Quant Model Zoo](https://github.com/microsoft/qlib/blob/main/README.md)。
- 新兴方向不是「换模型」，而是「换研发流程」：Walk-forward 滚动训练 + 因子表达式引擎 + 自动因子挖掘。AKQuant 把 walk-forward、Polars 因子表达式、多进程网格搜索做进了框架；qlib 侧有 RD-Agent（LLM 自动因子挖掘，[arXiv:2505.15155](https://arxiv.org/abs/2505.15155)）。
- 结论：**先把 GBDT + 正确切分 + 成本模型跑通**，再考虑时序深度模型；反过来做的项目，90% 的时间花在调参和修数据上。

### 3.4 常见失败模式（按危险程度排序）

| 失败模式 | 表现 | 怎么防 | 依据 |
|---|---|---|---|
| **未来函数 / PIT 偏差** | 用「事后前复权」价格回测历史；用今天的股票池回测过去；用当期财报（实际未披露）做因子 | 信号用复权价、下单映射回原始价；股票池按 `listing_date` 逐日生成；财务数据用披露日而非报告期 | [复权与 PIT 偏差（TickDB 技术博客，2026-05）](https://tickdb.ai/blog/general/a-%e8%82%a1%e5%9b%9e%e6%b5%8b%e4%b8%ad%e7%9a%84%e5%a4%8d%e6%9d%83%e4%b8%8e-point-in-time-%e5%81%8f%e5%b7%ae%e4%b8%ba%e4%bb%80%e4%b9%88%e5%ae%9e%e7%9b%98%e4%bc%9a%e8%bf%bd%e4%b8%8d%e4%b8%8a%e5%9b%9e%e6%b5%8b-mpdlbtro) |
| **幸存者偏差** | 用「当前在市」股票池回测十年，退市股消失 | 承认数据层无法完全消除，用「按时间点构建池 + 超额收益 + 更严格风控」对冲 | [腾讯云社区 2026-09-12](https://cloud.tencent.cn/developer/article/2742106) |
| **过拟合 / 搜索运气** | 跑了几千组参数，挑最好的 Sharpe；没有记录「试了多少次」 | 记录试验次数，用 Deflated Sharpe / PBO / MinTRL 做后验折扣 | [arXiv:2608.23808（2026-08-24）](https://arxiv.org/abs/2608.23808) |
| **样本外衰减** | 训练期 IC 高，上线后迅速归零 | 严格 walk-forward + 概念漂移检测；把「换手/成本」计入目标函数 | 同上（该文预注册测试显示：对未见真实数据的**前瞻关系不显著**，ρs=0.013, p=0.40） |

---

## 4. 模拟盘 / 交易接口

### 4.1 现状

| 方案 | 现状（2026-10） | 适用 | 风险 |
|---|---|---|---|
| **easytrader** | 0.23.7（2025-04-16）；README 支持「同花顺客户端模拟操作」「券商 miniqmt 官方量化接口」「雪球组合调仓」 | 个人小资金、半自动 | 客户端 UI 变动/弹窗即失效（历史 issue #376、#409） |
| **vnpy** | 4.5.0（2026-10-06）；股票靠网关（`vnpy_tora` 2026-10-08 仍在更新） | 期货/期权实盘、股票需券商网关 | 网关稳定性取决于券商；学习成本高 |
| **QMT / miniQMT** | 券商开通的官方量化通道，easytrader 已封装 miniqmt；**开通门槛由各券商自定（未核实统一标准）** | A 股程序化交易的正规路径 | 门槛/权限变动、需要 Windows 客户端常驻 |
| **券商模拟盘 API** | 多为券商自研（如 QMT 模拟、PTrade 模拟），接口非公开标准（**未核实**） | 上线前验证 | 撮合规则与真实市场不同 |
| **纯本地模拟盘** | 自己写撮合器 | 策略验证 | 撮合细节全都要自己建模，见 §4.2 |

来源：[easytrader README](https://github.com/shidenggui/easytrader/blob/master/README.md)、[easytrader issue #376](https://github.com/shidenggui/easytrader/issues/376)、[vnpy 论坛：4.0 没有 A 股接口](https://www.vnpy.com/forum/topic/33929-xiao-bai-qiu-zhu-4-0jiao-yi-jie-kou-mei-you-agu-jie-kou)、[vnpy_tora on PyPI](https://pypi.org/project/vnpy-tora/)。

> 有官方论坛帖直接讨论「bar 回测和实盘不相符的 bug」：[vnpy 论坛 topic 4146](https://www.vnpy.com/forum/topic/4146-guan-yu-barhui-ce-he-shi-pan-bu-xiang-fu-de-bug)、[实盘与回测不一致 topic 32842](https://www.vnpy.com/forum/topic/32842-shi-pan-yu-hui-ce-bu-yi-zhi)。这说明「回测/实盘不一致」是各家框架的共性难题，不是某个库的 bug。

### 4.2 纯本地模拟盘必须自己建模的撮合细节

参考 RQAlpha `sys_simulation` 暴露的配置项（等于一份「A 股撮合清单」）：

1. **成交价锚点**：当前 bar 收盘价 / 下一 bar 开盘价 / VWAP——日频回测里选 `current_bar` 等于「用当日收盘价成交」，而真实下单是看不到收盘价的（**前视**）。保守做法：`next_bar` 开盘价 + 滑点。
2. **涨跌停不可成交**：涨停时买不进、跌停时卖不出；一字板尤其严重。RQAlpha 默认 `price_limit=True` 就是为此。
3. **停牌不可成交**：`inactive_limit=True`；复牌跳空要按真实价格处理。
4. **成交量参与率**：`volume_limit=True`、`volume_percent=0.25`——单 bar 最多吃掉市场成交量的 25%，防止「小票满仓」的虚假容量。
5. **T+1**：当日买入不可卖出。
6. **费用**：佣金（含最低 5 元）、印花税（卖出单边）、过户费，见 §5。
7. **2026 新增通道**：盘后固定价格交易（15:05–15:30，收盘价、时间优先、**申报不一定成交**）——如果你的策略在尾盘调仓，2026-07 之后多了一条真实可用的通道，但绝大多数框架还没建模。

来源：[RQAlpha sys_simulation README](https://github.com/ricequant/rqalpha/blob/master/rqalpha/mod/rqalpha_mod_sys_simulation/README.rst)、[证券时报 2026-07-06](https://stcn.com/article/detail/3999185.html)。

---

## 5. 真实项目的坑（尤其 A 股特有）

### 5.1 涨跌停无法成交

- 机制：封板时买一/卖一队列极长，市价单也未必成交；一字板全天无成交，回测若按收盘价成交，等于凭空造出收益。
- 框架证据：RQAlpha 把 `price_limit` 做成默认开启的配置项（见上）。
- **2026 年新变化**：2026-07-06 起沪深主板 ST/*ST 涨跌幅由 5% 改为 10%；创业板/科创板风险警示股 20%、北交所 30% 不变。**回测的涨跌停阈值必须按「板块 × 日期」分段**，硬编码 5% 会把 2026-07 之后的 ST 股全部误判为「一字板」。来源：[证券时报](https://stcn.com/article/detail/3999185.html)、[新浪财经](https://finance.sina.com.cn/wm/2026-07-05/doc-inifsuvq9174512.shtml)。
- 社区工具：有项目专门做「A 股可交易性约束审计：涨跌停/停牌/T+1/裸卖空/参与率五道约束」（[skill-a-share-tradability-auditor](https://github.com/quantskills/skill-a-share-tradability-auditor)），可当作检查清单参考（其质量与适用性未独立验证）。

### 5.2 停牌

- 停牌期间无行情、不可交易；复牌首日往往大幅跳空。
- 数据层：Tushare 有「每日停复牌信息」（[doc 214](https://tushare.pro/document/2?doc_id=214)）、BaoStock 有 `tradestatus` 字段；AKShare 有停复牌接口但稳定性需自检。
- 回测层：停牌股票必须从可交易池剔除，而不是用停牌前收盘价「假装持有」。

### 5.3 复权处理

- **事后前复权 = 把未来的除权信息注入历史**：今天的复权因子会改写半年前的价格序列，而当时的交易者并不知道未来的送转。这是标准的 Point-in-Time 偏差。
- 实操建议：信号计算用复权价（相对指标线性不变），**下单/止损映射回原始市场价**；跨除权日持仓必须复权，纯日内可不复权但要过滤除权日开盘跳空。来源：[TickDB 技术博客 2026-05](https://tickdb.ai/blog/general/a-%e8%82%a1%e5%9b%9e%e6%b5%8b%e4%b8%ad%e7%9a%84%e5%a4%8d%e6%9d%83%e4%b8%8e-point-in-time-%e5%81%8f%e5%b7%ae%e4%b8%ba%e4%bb%80%e4%b9%88%e5%ae%9e%e7%9b%98%e4%bc%9a%e8%bf%bd%e4%b8%8d%e4%b8%8a%e5%9b%9e%e6%b5%8b-mpdlbtro)（厂商博客，有产品推广，技术论述可独立核对）。
- 交叉校验：前复权与后复权序列的比值应为常数；比值不恒定说明复权因子口径不一致或对齐出错。

### 5.4 手续费与滑点假设（2026 年现行口径）

| 项目 | 现行 | 依据 |
|---|---|---|
| 印花税 | **卖出单边 0.05%**（2023-08-28 起减半） | [人民网 2023-08-28](http://finance.people.com.cn/GB/n1/2023/0828/c1004-40065300.html)、[证券时报](https://www.stcn.com/article/detail/960778.html) |
| 过户费 | 沪深 A 股按成交金额 **0.01‰**（2022-04-29 下调 50%） | [中国结算通知](http://www.chinaclear.cn/zdjs/gszb/202204/837e3c5031104aa099d6597ba381342a.shtml)、[证券时报](https://www.stcn.com/article/detail/573392.html) |
| 佣金 | 万 2.5–万 3（含最低 5 元）为主流，因券商而异 | 行业惯例（**未逐一核实**，以自己账户费率填） |
| 滑点 | 无官方值；日频回测常用 0.05%–0.2% 或 1 个跳 | 经验值（**未核实**），应按成交额/换手率分档 |

- **框架默认值往往过时**：RQAlpha 税费 mod 默认佣金万八、印花税千分之一单边。若不改 `commission_multiplier` / `tax_multiplier`，回测成本被系统性高估（佣金）同时印花税也被高估（0.1% vs 0.05%），结论方向都可能变。来源：[rqalpha sys_transaction_cost README](https://github.com/ricequant/rqalpha/blob/master/rqalpha/mod/rqalpha_mod_sys_transaction_cost/README.rst)。
- 2026 年未检索到印花税/过户费再次调整的公开信息（**截至 2026-10-22 的检索结论，非官方承诺**）。

### 5.5 幸存者偏差

- 「用今天的全 A 列表回测十年」= 只统计活到今天的赢家，退市/暴雷/面值退市全部消失。
- 修法：按 `listing_date` 逐日构建候选池（解决前视）；退市股缺失只能对冲（更严风控、看超额收益、报告里如实标注）。来源：[腾讯云社区 2026-09-12](https://cloud.tencent.cn/developer/article/2742106)（注意：该文有付费数据产品推广，方法与风险论述可参考，产品结论不要照单全收）。

### 5.6 「回测好、实盘亏」的结构性原因

1. **统计层**：结果是从大量尝试中挑出来的，Sharpe 未做多重检验折扣。2026-08 的 arXiv 论文给出系统化工具（DSR/PBO/SPA/MinTRL 组合成 MinervaScore），并诚实指出：在预注册的真实市场测试中，该评分对未来的预测关系不显著（ρs=0.013, p=0.40）——即「稳健性评级」能过滤明显噪声，但不保证盈利。来源：[arXiv:2608.23808](https://arxiv.org/abs/2608.23808)。
2. **执行层**：涨跌停/停牌/成交量/盘后交易通道未建模；用收盘价成交；滑点为 0。
3. **成本层**：佣金最低 5 元对小资金影响巨大；印花税/过户费口径错。
4. **数据层**：复权、PIT、股票池三类偏差叠加。
5. **容量层**：小市值股票按回测仓位根本买不到量（参与率约束）。
6. **框架层**：「信号模式」直接成交（RQAlpha `--signal`）、`matching_type=current_bar` 等便利选项，本质是用回测精度换开发速度，适合做上界估计，不适合做上线决策。

---

## 6. 给你的落地建议（与 selection-brief 对应）

### 6.1 推荐组合

```
数据：BaoStock（主，A 股日线/复权/停牌/ST 字段）+ AKShare（补财务、板块、另类）
      + 自建校验层（行数/日期连续/复权因子单调/双源交叉）
回测基准：RQAlpha 6.4.2（规则引擎，负责「这笔交易到底能不能成交」）
快速研究：AKQuant 0.3.71（Rust 内核，负责批量实验/因子扫描/walk-forward）
只读参考：qlib 的 Alpha158 因子定义与 benchmark 配置、vectorbt 的向量化写法
不选：backtrader（停更）、zipline-reloaded（维护模式）、
      vectorbt 开源版当撮合引擎（限价单在 PRO）、
      easytrader/QMT 当「策略正确性」的验证工具（它们只保证下单通路）
```

### 6.2 分期策略

- **第 1 阶段（1–2 周）**：只做数据 + 可交易性审计。把 5.1–5.5 的每个坑写成断言（例如「涨停价买入的订单必须不被成交」），跑通 BaoStock 数据校验，不写任何策略。
- **第 2 阶段（2–4 周）**：用 RQAlpha 跑一个最简单的因子策略，把费用口径改成 §5.4 的现行值，输出「含成本/不含成本」两条曲线，差值就是成本敏感度。
- **第 3 阶段**：引入 AKQuant 做批量实验，所有参数搜索记录试验次数，最后用 DSR/PBO 类指标折扣。
- **第 4 阶段**：模拟盘（QMT/miniQMT 或本地撮合器）对照，重点比对「成交价、成交率、费用」三项，而不是收益。

### 6.3 未核实清单（落地前必须自己验证）

1. backtrader 在 Python 3.13 上是否可运行（无官方声明）。
2. backtesting.py 的 `requires_python` 与 3.13 支持（`pyproject.toml` 中未见该字段）。
3. hikyuu 的 T+1/涨跌停实现细节。
4. QUANTAXIS 当前可用模块清单与文档状态。
5. BaoStock 对退市股、更新延迟、限流的具体边界。
6. QMT/miniQMT 各券商的开通门槛与权限差异。
7. 佣金行业主流费率（本文用「万 2.5–万 3」为经验值）。
8. 2026 年是否存在印花税/过户费的进一步调整（本文截至 2026-10-22 未检索到）。

---

## 附：本文引用的关键一手链接

**框架**
- vectorbt：<https://github.com/polakowo/vectorbt> ｜ <https://github.com/polakowo/vectorbt/releases> ｜ <https://github.com/polakowo/vectorbt/blob/master/README.md>
- backtrader：<https://github.com/mementum/backtrader> ｜ <https://github.com/mementum/backtrader/commits/master> ｜ <https://pypi.org/project/backtrader/>
- zipline-reloaded：<https://github.com/stefan-jansen/zipline-reloaded> ｜ <https://github.com/stefan-jansen/zipline-reloaded/blob/main/README.md>
- qlib：<https://github.com/microsoft/qlib> ｜ <https://github.com/microsoft/qlib/issues/2018>
- nautilus_trader：<https://github.com/nautechsystems/nautilus_trader> ｜ <https://github.com/nautechsystems/nautilus_trader/blob/develop/README.md>
- RQAlpha：<https://github.com/ricequant/rqalpha> ｜ <https://github.com/ricequant/rqalpha/blob/master/rqalpha/mod/rqalpha_mod_sys_simulation/README.rst>
- AKQuant：<https://github.com/akfamily/akquant> ｜ <https://pypi.org/project/akquant/>
- vnpy：<https://github.com/vnpy/vnpy> ｜ <https://www.vnpy.com/forum/topic/33929-xiao-bai-qiu-zhu-4-0jiao-yi-jie-kou-mei-you-agu-jie-kou>

**数据**
- AKShare：<https://akshare.akfamily.xyz/answer.html> ｜ <https://github.com/akfamily/akshare/issues/6061> ｜ <https://github.com/akfamily/akshare/issues/7444>
- Tushare：<https://tushare.pro/document/2?doc_id=290>
- BaoStock：<https://pypi.org/project/baostock/>
- yfinance：<https://pypi.org/project/yfinance/>

**规则/费率/风险**
- 2026-07-06 A 股交易新规：<https://stcn.com/article/detail/3999185.html> ｜ <https://finance.sina.com.cn/wm/2026-07-05/doc-inifsuvq9174512.shtml>
- 印花税减半：<http://finance.people.com.cn/GB/n1/2023/0828/c1004-40065300.html>
- 过户费下调：<http://www.chinaclear.cn/zdjs/gszb/202204/837e3c5031104aa099d6597ba381342a.shtml>
- 复权与 PIT 偏差：<https://tickdb.ai/blog/general/a-%e8%82%a1%e5%9b%9e%e6%b5%8b%e4%b8%ad%e7%9a%84%e5%a4%8d%e6%9d%83%e4%b8%8e-point-in-time-%e5%81%8f%e5%b7%ae%e4%b8%ba%e4%bb%80%e4%b9%88%e5%ae%9e%e7%9b%98%e4%bc%9a%e8%bf%bd%e4%b8%8d%e4%b8%8a%e5%9b%9e%e6%b5%8b-mpdlbtro>
- 幸存者偏差：<https://cloud.tencent.cn/developer/article/2742106>
- 回测过拟合（DSR/PBO/MinTRL）：<https://arxiv.org/abs/2608.23808>
