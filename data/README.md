# data/

本地数据目录（不进 git，可随时重建）。

```
data/
└─ cache/
   ├─ bars.csv.gz            # 个股日线长表（BaoStock）
   ├─ index_bars.csv.gz      # 基准指数日线
   └─ members_000300.csv.gz  # 季度成分股快照（时点成分，防幸存者偏差）
```

## 数据源：BaoStock（主）

| 字段 | 含义 | 单位 |
|---|---|---|
| date | 交易日 | — |
| open/high/low/close | 价格，**前复权**（adjustflag=2） | 元 |
| volume | 成交量 | **股** |
| amount | 成交额 | 元 |
| turnover | 换手率 | % |
| pct_chg | 涨跌幅 | % |

- 指数行情用 `adjustflag=3`（不复权）。
- 成分股快照每季末一份，`snapshot_date` 为快照生效日；回测按「上一份快照生效」使用。
- `pct_chg` 是原始价格涨跌幅，用于判断涨跌停是否封板。

## 为什么不用 akshare / 东财

- 东财接口在批量抓取约 5 只后对本机返回 `RemoteDisconnected`，属按客户端限流（详见
  `docs/decisions/0003-data-source-pivot.md`）；适配器 `quant/data/eastmoney.py` 保留作备用。
- 腾讯单次只回 320~640 根，8 年需翻页 4 次 × 数百只，请求量与限流风险都不划算。
- BaoStock 一次调用给全历史，且能查**历史时点成分股**，这是其它源给不了的。

## 重建

```powershell
python scripts/fetch_data.py --start 2018-01-01 --end 2026-10-22
```
