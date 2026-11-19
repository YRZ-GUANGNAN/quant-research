"""生成 A 股实盘目标权重（小账户版）：模型信号 → 少量高信心标的。

模型原始目标是 40 只等权（适合百万级账户）。20 万级账户照抄会被手续费和
最小 100 股整手吃掉收益，所以这里做两件事：
1. 取共识权重最高的前 N 只（默认 6），按原总仓位重新归一，仓位不变；
2. 落盘 state/cn_target_live.json，供 cn_broker 执行层使用。

用法：
    python scripts/build_cn_target_live.py                 # 只生成目标
    python scripts/build_cn_target_live.py --preview       # 顺带对照真实账户算单（不下单）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quant.data.panel import Panel  # noqa: E402
from quant.strategies.ensemble import PhaseEnsembleStrategy  # noqa: E402

OUT = ROOT / "state" / "cn_target_live.json"


def build_target(top: int = 6, exposure: float = 0.70, warmup: int = 130,
                 index: str = "000300", rebalance: int = 35) -> dict:
    """返回目标权重与元信息（纯计算，不写盘、不读账户）。"""
    from quant.config import BacktestConfig

    cfg = BacktestConfig(index_code=index, top_k=40, rebalance_days=rebalance)
    panel = Panel.load(index_code=index)
    strategy = PhaseEnsembleStrategy(panel=panel, cfg=cfg, name="ic_tilt",
                                     warmup=warmup, exposure_scale=exposure)
    strategy.replay(len(panel.dates) - 1)
    raw = strategy.target_weights(panel.dates[-1])
    if not raw:
        return {"weights": {}, "source_date": str(panel.dates[-1].date()), "raw_count": 0}

    gross = sum(raw.values())
    ranked = sorted(raw.items(), key=lambda kv: -kv[1])[:top]
    picked_sum = sum(w for _, w in ranked)
    weights = {c: round(gross * w / picked_sum, 4) for c, w in ranked}
    return {
        "weights": weights,
        "source_date": str(panel.dates[-1].date()),
        "raw_count": len(raw),
        "gross": round(gross, 4),
        "phase_disagreement": strategy.phase_disagreement()[-1] if strategy.phase_disagreement() else None,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成 A 股实盘目标权重")
    ap.add_argument("--top", type=int, default=6, help="持有标的数（小账户建议 5-8）")
    ap.add_argument("--exposure", type=float, default=0.70, help="股票仓位系数")
    ap.add_argument("--preview", action="store_true", help="对照真实账户算单（不下单）")
    args = ap.parse_args(argv)

    target = build_target(top=args.top, exposure=args.exposure)
    if not target["weights"]:
        print("[cn-target] 模型无信号，保留上一版目标")
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": "phase_ensemble/ic_tilt",
        "exposure": args.exposure,
        "top": args.top,
        **target,
        "weights": target["weights"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[cn-target] 数据日 {target['source_date']}，模型 {target['raw_count']} 只 → 取前 {args.top}")
    for code, w in target["weights"].items():
        print(f"  {code}  {w * 100:5.2f}%")
    print(f"[cn-target] 合计仓位 {sum(target['weights'].values()) * 100:.1f}%  -> {OUT.name}")

    if args.preview:
        from quant.broker import cn_broker

        result = cn_broker.execute_target(target["weights"], dry_run=True)
        print("[cn-target] 真实账户算单（dry-run）:")
        print(json.dumps(result, ensure_ascii=False, indent=2)[:2500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
