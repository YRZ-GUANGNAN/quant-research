"""资金分布检查：钱到底有多少真正在工作。

现状（2026-11-27）：Longbridge 账户净资产 198 万美元，其中 **100 万新币（约 74 万美元）
以现金形式闲置**——占全部资金 37%。模型再好，三分之一资金不参与，收益就被摊薄三分之一。

这个脚本只做三件事：
1. 读各市场资产与现金，算出「已投 / 闲置」比例；
2. 与实际设计权重（CN 60% / US 30% / HK 10%）对比，指出偏离；
3. 闲置超过阈值就明确报警（默认 10%）。

用法：
    python scripts/capital_check.py
    python scripts/capital_check.py --json
输出：reports/capital_allocation.json
退出码：0 正常 / 1 闲置或偏离超阈值 / 3 取不到数据
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
LB = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "longbridge" / "longbridge.exe"

# 目标权重（决策 0014，2026-11-27 按 2023+ 实测重定）：
# 2023 年以来（含真实预热）：美股夏普 1.19、A 股 0.34、港股 0.24 —— 只有美股明显更强。
DESIGN_WEIGHTS = {"us": 0.60, "hk": 0.20, "cn": 0.20}
IDLE_WARN = 0.10        # 闲置现金超过 10% 就报警
DEVIATION_WARN = 0.15   # 实际权重与设计权重偏离超过 15pp 报警
# 粗略汇率（仅用于量级判断；不参与下单）
FX_TO_USD = {"USD": 1.0, "HKD": 0.1285, "CNY": 0.1408, "SGD": 0.7450}


def _cli(args: list[str], timeout: int = 60) -> object:
    proc = subprocess.run([str(LB), *args, "--format", "json"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or out).strip()[:160])
    return json.loads(out) if out else {}


def to_usd(amount: float, ccy: str) -> float:
    return float(amount) * FX_TO_USD.get(str(ccy).upper(), 1.0)


def summarize(assets: list[dict], positions_value_by_market: dict[str, float]) -> dict:
    """纯计算：Longbridge 资产 + 各市场持仓市值 → 已投/闲置/偏离。"""
    cash_by_ccy: dict[str, float] = {}
    net_assets_usd = 0.0
    for acct in assets or []:
        net_assets_usd += to_usd(acct.get("net_assets") or 0, acct.get("currency") or "USD")
        for c in (acct.get("cash_infos") or []):
            ccy = str(c.get("currency") or "").upper()
            cash_by_ccy[ccy] = cash_by_ccy.get(ccy, 0.0) + float(c.get("available_cash") or 0)

    cash_usd = {ccy: to_usd(v, ccy) for ccy, v in cash_by_ccy.items()}
    invested = {m: float(v) for m, v in (positions_value_by_market or {}).items()}
    invested_total = sum(invested.values())
    total = max(net_assets_usd, invested_total + sum(cash_usd.values()))

    weights = {m: round(v / total, 4) for m, v in invested.items()} if total else {}
    idle_usd = sum(cash_usd.values())
    return {
        "net_assets_usd": round(net_assets_usd, 2),
        "cash_by_currency": {k: round(v, 2) for k, v in cash_by_ccy.items()},
        "cash_usd": {k: round(v, 2) for k, v in cash_usd.items()},
        "invested_usd": {m: round(v, 2) for m, v in invested.items()},
        "invested_ratio": round(invested_total / total, 4) if total else None,
        "idle_ratio": round(idle_usd / total, 4) if total else None,
        "weights": weights,
        "design_weights": DESIGN_WEIGHTS,
        "deviation": {m: round(weights.get(m, 0.0) - DESIGN_WEIGHTS[m], 4) for m in DESIGN_WEIGHTS},
        "total_usd": round(total, 2),
    }


def positions_by_market() -> dict[str, float]:
    """按市场汇总持仓市值（USD 估算）。数据源：持仓成本 × 数量 的粗略替代——用市值字段。"""
    out: dict[str, float] = {}
    try:
        rows = _cli(["positions"]) or []
    except (RuntimeError, json.JSONDecodeError):
        return out
    # 逐个市场取报价太重，这里用「持仓数量 × 成本价」近似市值（只做量级判断）
    for p in rows:
        sym = str(p.get("symbol") or "")
        try:
            qty = float(p.get("quantity") or 0)
            cost = float(p.get("cost_price") or 0)
        except (TypeError, ValueError):
            continue
        market = "us" if sym.endswith(".US") else ("hk" if sym.endswith(".HK") else "other")
        ccy = "USD" if market == "us" else "HKD"
        out[market] = out.get(market, 0.0) + to_usd(qty * cost, ccy)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="资金分布检查")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        assets = _cli(["assets"]) or []
    except (RuntimeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"[capital] 取不到账户资产: {type(exc).__name__} {str(exc)[:100]}")
        return 3
    report = {"generated_at": datetime.now().isoformat(timespec="seconds"),
              **summarize(assets if isinstance(assets, list) else [assets], positions_by_market())}

    problems = []
    if report["idle_ratio"] is not None and report["idle_ratio"] > IDLE_WARN:
        biggest = max(report["cash_usd"].items(), key=lambda kv: kv[1], default=("", 0))
        problems.append(f"闲置现金占 {report['idle_ratio'] * 100:.0f}%"
                        f"（最大一笔 {biggest[0]} {biggest[1]:,.0f} 美元）")
    for m, dev in report["deviation"].items():
        if abs(dev) > DEVIATION_WARN:
            problems.append(f"{m.upper()} 实际权重 {report['weights'].get(m, 0) * 100:.0f}%"
                            f" vs 设计 {DESIGN_WEIGHTS[m] * 100:.0f}%（偏离 {dev * 100:+.0f}pp）")
    report["problems"] = problems

    out = REPORTS / "capital_allocation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"=== 资金分布（净资产 {report['net_assets_usd']:,.0f} 美元）===")
        print(f"  已投 {report['invested_ratio'] * 100:.0f}% | 闲置 {report['idle_ratio'] * 100:.0f}%")
        for ccy, amt in sorted(report["cash_usd"].items(), key=lambda kv: -kv[1]):
            print(f"    现金 {ccy}: {report['cash_by_currency'].get(ccy, 0):,.0f}（≈{amt:,.0f} 美元）")
        for m, w in sorted(report["weights"].items()):
            print(f"    {m.upper()} 权重 {w * 100:.0f}%（设计 {DESIGN_WEIGHTS.get(m, 0) * 100:.0f}%）")
        for p in problems:
            print(f"  [!] {p}")
        print(f"  -> {out}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
