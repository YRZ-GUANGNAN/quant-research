# data/

本地数据目录。

```
data/
└─ cache/                    # 不进 git（见 .gitignore）
   ├─ bars.csv.gz            # 个股日线长表
   ├─ index_bars.csv.gz      # 基准指数日线
   └─ universe_000300.json   # 成分股快照 + 抓取时间
```

## 数据口径

- **复权**：个股使用前复权（`fqt=1`），收益包含现金分红再投；指数不复权。
- **字段**：`date, code, open, close, high, low, volume(手), amount(元), amplitude(%), pct_chg(%), chg(元), turnover(%)`。
  其中 `pct_chg` 是**原始价格**的日涨跌幅，用于判断涨跌停是否封板（复权序列不能直接判断）。
- **停牌**：停牌日接口不返回该行，回测中按「无 bar = 不可交易」处理。
- **幸存者偏差**：成分股是**当前**名单，历史调仓无法还原，因此回测收益偏乐观。此偏差已在
  `docs/decisions/` 中单独评估，不隐瞒。

## 重建

```powershell
python scripts/fetch_data.py --index 000300 --start 2018-01-01
```
