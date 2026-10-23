"""各市场股票池（静态名单）。

说明：非 A 股市场拿不到「历史时点成分股」的免费接口，这里用**静态名单**，
因此存在幸存者偏差（只包含今天还活着的公司）。这一点在报告里必须标注，
不能假装它和 A 股的时点成分股等价。

A 股不用本文件：A 股用 BaoStock 的 ``query_hs300_stocks(date)`` 取时点成分股。

名单选择标准：各市场流动性最好、成交额最高的一批标的，
目的是「验证同一套因子在多市场是否成立」，而不是构建完整指数增强。
"""

from __future__ import annotations

# 美股：市值/流动性靠前的大盘股（Yahoo 代码）
US_UNIVERSE: list[str] = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "BRK-B", "JPM",
    "V", "MA", "UNH", "XOM", "JNJ", "WMT", "PG", "HD", "CVX", "LLY",
    "ABBV", "MRK", "KO", "PEP", "COST", "ADBE", "CRM", "MCD", "NKE", "TMO",
    "ACN", "ABT", "CSCO", "DHR", "TXN", "NEE", "AMD", "INTC", "QCOM", "PM",
    "WFC", "BAC", "GS", "MS", "BLK", "C", "AXP", "SPGI", "CAT", "DE",
    "BA", "GE", "HON", "LMT", "RTX", "UNP", "UPS", "FDX", "T", "VZ",
]

# 港股：恒生指数权重股（Yahoo 代码带 .HK）
HK_UNIVERSE: list[str] = [
    "0700.HK", "9988.HK", "3690.HK", "0941.HK", "1299.HK", "0005.HK", "0388.HK",
    "1810.HK", "2318.HK", "1398.HK", "3988.HK", "0939.HK", "0883.HK", "0857.HK",
    "0386.HK", "0960.HK", "1109.HK", "0016.HK", "0001.HK", "0002.HK", "0003.HK",
    "0006.HK", "0011.HK", "0012.HK", "0066.HK", "0083.HK", "0101.HK", "0175.HK",
    "0267.HK", "0288.HK", "0291.HK", "0316.HK", "0322.HK", "0669.HK", "0688.HK",
    "0762.HK", "0823.HK", "0868.HK", "0968.HK", "1024.HK", "1044.HK", "1093.HK",
    "1177.HK", "1209.HK", "1211.HK", "1928.HK", "1929.HK", "2020.HK", "2269.HK",
    "2382.HK", "2388.HK", "2628.HK", "2899.HK", "3692.HK", "3968.HK", "6098.HK",
    "6618.HK", "6690.HK", "6862.HK", "9633.HK",
]

# 加密货币：按市值靠前的主流币（Yahoo 代码）
CRYPTO_UNIVERSE: list[str] = [
    "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD", "ADA-USD", "DOGE-USD",
    "AVAX-USD", "DOT-USD", "LINK-USD", "MATIC-USD", "LTC-USD", "BCH-USD", "UNI-USD",
    "ATOM-USD", "ETC-USD", "FIL-USD", "APT-USD", "ARB-USD", "OP-USD",
]

# 新加坡：SGX 蓝筹（Yahoo .SI 代码）
SG_UNIVERSE: list[str] = [
    "D05.SI", "O39.SI", "U11.SI", "Z74.SI", "C6L.SI", "C09.SI",
    "A17U.SI", "BS6.SI", "F34.SI", "G13.SI", "S68.SI", "U14.SI",
    "V03.SI", "H78.SI", "Z25.SI", "SK6U.SI",
]

UNIVERSES: dict[str, list[str]] = {
    "sg": SG_UNIVERSE,
    "us": US_UNIVERSE,
    "hk": HK_UNIVERSE,
    "crypto": CRYPTO_UNIVERSE,
}


def get_universe(market: str) -> list[str]:
    if market not in UNIVERSES:
        raise KeyError(f"未知市场 {market}，可选 {list(UNIVERSES)}")
    return list(UNIVERSES[market])
