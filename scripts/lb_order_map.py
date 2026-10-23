"""把模型 v4 的美股/港股袖子映射到 Longbridge 模拟盘并自动下单（v2）。

v1 的教训（实测）：
1. 美股符号必须带 `.US` 后缀（本地代码是 `AAPL`，CLI 需要 `AAPL.US`）；
2. 港股每手股数各不相同（700.HK=100，3988.HK=1000），必须查 `static_info.lot_size`；
3. 港股限价必须落在合法 tick 上（否则 602035 Wrong bid size）。

流程：算目标 → 查手数 → 实时报价 → tick 对齐限价 → 预览拿确认码 → 提交。
用法：
    python scripts/lb_order_map.py --dry-run
    python scripts/lb_order_map.py --limit 3
    python scripts/lb_order_map.py
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quant.config import BacktestConfig  # noqa: E402
from quant.data.panel import Panel  # noqa: E402
from quant.strategies.presets import PRESETS  # noqa: E402
from quant.strategies.topk import TopKStrategy  # noqa: E402

LB = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "longbridge" / "longbridge.exe"
USD_HKD = 7.80

HK_TICKS = [
    (0.25, 0.001), (0.5, 0.005), (10, 0.01), (20, 0.02), (100, 0.05),
    (200, 0.1), (500, 0.2), (1000, 0.5), (2000, 1), (5000, 2), (float("inf"), 5),
]


def cli_json(args: list[str], timeout: int = 40) -> object:
    proc = subprocess.run([str(LB), *args, "--format", "json"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip()[:300])
    out = (proc.stdout or "").strip()
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        # CLI 有时把人类可读文本混进输出（尤其订单预览），截取第一个 JSON 片段
        start, end = out.find("{"), out.rfind("}")
        if start >= 0 and end > start:
            return json.loads(out[start:end + 1])
        raise


def hk_tick(price: float) -> float:
    for upper, tick in HK_TICKS:
        if price < upper:
            return tick
    return 5.0


def round_price(symbol: str, price: float) -> float:
    if symbol.endswith(".HK"):
        tick = hk_tick(price)
        return math.ceil(price / tick) * tick
    return round(price, 2)


def lot_sizes(symbols: list[str]) -> dict[str, int]:
    """用 SDK static_info 查每手股数；失败则按市场保守假设。"""
    out: dict[str, int] = {}
    try:
        from longbridge.openapi import Config, QuoteContext

        from quant.broker import lb_sdk

        env = lb_sdk.load_env()
        cfg = Config.from_apikey(app_key=env["LONGBRIDGE_APP_KEY"],
                                 app_secret=env["LONGBRIDGE_APP_SECRET"],
                                 access_token=env["LONGBRIDGE_ACCESS_TOKEN"])
        ctx = QuoteContext(cfg)
        for i in range(0, len(symbols), 20):
            for info in ctx.static_info(symbols[i:i + 20]):
                out[info.symbol] = int(info.lot_size or 1)
    except Exception as exc:  # noqa: BLE001
        print(f"[lot] static_info 失败，使用默认手数: {str(exc)[:120]}")
    for s in symbols:
        out.setdefault(s, 100 if s.endswith(".HK") else 1)
    return out


def net_assets_usd() -> float:
    assets = cli_json(["assets"])
    if isinstance(assets, list):
        for a in assets:
            if a.get("currency") == "USD":
                return float(a.get("net_assets") or 0)
    return 0.0


def quotes(symbols: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(symbols), 20):
        for q in cli_json(["quote", *symbols[i:i + 20]]) or []:
            out[q["symbol"]] = q
    return out


def sleeve_targets(market: str, top_k: int, suffix: str) -> dict[str, float]:
    panel = Panel.load(index_code=None, market=market)
    cfg = BacktestConfig.for_market(market, top_k=top_k, rebalance_days=20)
    strat = TopKStrategy(panel=panel, cfg=cfg, factor_weights=PRESETS["momentum"], warmup=130)
    weights = strat.target_weights(panel.dates[-1])
    return {(c if c.endswith(suffix) else f"{c}{suffix}"): w for c, w in weights.items()}


def build_plan(weights: dict[str, float], budget_local: float) -> list[dict]:
    lots = lot_sizes(list(weights))
    quotes_map = quotes(list(weights))
    plan = []
    for sym, w in weights.items():
        q = quotes_map.get(sym)
        if not q or not q.get("last"):
            continue
        last = float(q["last"])
        price = round_price(sym, last * 1.01)
        lot = lots.get(sym, 1)
        value = budget_local * w
        shares = int(value / price // lot) * lot
        if shares <= 0:
            continue
        plan.append({"symbol": sym, "side": "buy", "quantity": shares, "price": price, "lot": lot})
    return plan


def submit(plan: list[dict], dry_run: bool) -> list[dict]:
    results = []
    for item in plan:
        if dry_run:
            results.append({**item, "status": "dry-run"})
            continue
        try:
            preview = cli_json(["order", item["side"], item["symbol"],
                                str(item["quantity"]), "--price", f"{item['price']:.3f}"])
            code = preview.get("confirmation_code")
            if not code:
                results.append({**item, "status": "no-confirm-code"})
                continue
            resp = cli_json(["order", item["side"], item["symbol"], str(item["quantity"]),
                             "--price", f"{item['price']:.3f}", "--execute", code])
            results.append({**item, "status": "submitted", "order_id": resp.get("order_id")})
        except Exception as exc:  # noqa: BLE001
            results.append({**item, "status": f"error: {str(exc)[:90]}"})
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="模型 v4 美港股 → 券商模拟盘下单")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--us-weight", type=float, default=0.75)
    ap.add_argument("--only", choices=["us", "hk", "both"], default="both")
    ap.add_argument("--skip-existing", action="store_true", help="跳过今日已有订单的标的")
    args = ap.parse_args()

    total = net_assets_usd()
    print(f"[broker] 模拟盘净资产 USD {total:,.2f}")
    if total <= 0:
        return 1

    us_w = sleeve_targets("us", 20, ".US")
    hk_w = sleeve_targets("hk", 15, ".HK")
    print(f"[model] 美股 {len(us_w)} 只 / 港股 {len(hk_w)} 只")

    us_plan = build_plan(us_w, total * args.us_weight) if args.only in ("us", "both") else []
    hk_plan = build_plan(hk_w, total * (1 - args.us_weight) * USD_HKD) if args.only in ("hk", "both") else []
    plan = us_plan + hk_plan
    if args.skip_existing:
        try:
            existing = {o.get("symbol") for o in cli_json(["order"]) or []}
            plan = [p for p in plan if p["symbol"] not in existing]
            print(f"[plan] 跳过今日已下单标的，剩余 {len(plan)} 笔")
        except Exception as exc:  # noqa: BLE001
            print(f"[plan] 查已有订单失败，继续: {str(exc)[:100]}")
    if args.limit:
        plan = plan[: args.limit]

    print(f"[plan] 订单 {len(plan)} 笔（美股 {len(us_plan)} / 港股 {len(hk_plan)}），"
          f"{'dry-run' if args.dry_run else '真实提交'}")
    results = submit(plan, args.dry_run)
    for r in results:
        print(f"  {r['side']:<4}{r['symbol']:<12}{r['quantity']:>8} @ {r['price']:<10} -> {r['status']}")

    out = Path("logs") / "lb_order_map_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[plan] 结果 -> {out}（成功 {sum(1 for r in results if r['status'] in ('submitted', 'dry-run'))} 笔）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
