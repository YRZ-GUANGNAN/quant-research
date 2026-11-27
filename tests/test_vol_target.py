"""波动率目标缩放的纯函数测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quant.automation import vol_target as vt  # noqa: E402


def test_no_target_returns_full_exposure():
    factor, diag = vt.exposure_factor([100.0] * 30, None)
    assert factor == 1.0
    assert "未启用" in diag["reason"]


def test_insufficient_sample_does_not_scale():
    factor, diag = vt.exposure_factor([100.0, 101.0, 102.0], 0.15)
    assert factor == 1.0
    assert "样本不足" in diag["reason"]


def test_high_vol_reduces_exposure():
    import math
    values = [100.0 * (1.03 if i % 2 else 0.97) ** (i // 2) for i in range(40)]
    vol = vt.realized_vol(values)
    assert vol is not None and vol > 0.15
    factor, _ = vt.exposure_factor(values, 0.15)
    assert factor < 1.0
    assert factor >= 0.5


def test_low_vol_keeps_full_exposure():
    values = [100.0 * (1.001 ** i) for i in range(40)]
    factor, _ = vt.exposure_factor(values, 0.15)
    assert factor == 1.0


def test_factor_never_below_floor():
    values = [100.0 * (1.10 if i % 2 else 0.90) ** (i // 2) for i in range(40)]
    factor, _ = vt.exposure_factor(values, 0.05, min_exposure=0.5)
    assert factor == 0.5


def test_realized_vol_matches_manual_calc():
    import math
    rets = [0.01, -0.01] * 15
    values = [100.0]
    for r in rets:
        values.append(values[-1] * (1 + r))
    vol = vt.realized_vol(values, lookback=20)
    assert vol is not None and math.isfinite(vol)
