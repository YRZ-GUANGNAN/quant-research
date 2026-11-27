"""净值绩效指标的单测（纯函数，不依赖真实净值文件）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import equity_curve as ec  # noqa: E402


def _rows(*vals, start="2026-11-01"):
    from datetime import date, timedelta

    d0 = date.fromisoformat(start)
    return [((d0 + timedelta(days=i)).isoformat(), float(v)) for i, v in enumerate(vals)]


def test_series_metrics_return_and_drawdown():
    m = ec.series_metrics(_rows(100, 110, 90, 120))
    assert m["return"] == 0.2
    assert m["max_drawdown"] == round(90 / 110 - 1, 4)
    assert m["days"] == 4


def test_series_metrics_needs_two_points():
    m = ec.series_metrics(_rows(100))
    assert m["return"] is None and m["sharpe"] is None
    assert m["days"] == 1


def test_series_metrics_sharpe_sign():
    up = ec.series_metrics(_rows(100, 101, 102.1, 103.2))
    down = ec.series_metrics(_rows(100, 99, 98.1, 97.2))
    assert up["sharpe"] > 0 > down["sharpe"]


def test_win_rate_counts_positive_days():
    m = ec.series_metrics(_rows(100, 101, 100, 102))
    assert m["win_rate"] == round(2 / 3, 3)


def test_combined_series_uses_common_dates_only():
    nav = {
        "us": [("2026-11-01", 100.0), ("2026-11-02", 110.0), ("2026-11-03", 120.0)],
        "hk": [("2026-11-02", 200.0), ("2026-11-03", 210.0)],
    }
    combined = ec.combined_series(nav)
    assert [d for d, _ in combined] == ["2026-11-02", "2026-11-03"]
    assert [v for _, v in combined] == [310.0, 330.0]


def test_gaps_detects_long_hole():
    rows = [("2026-11-01", 1.0), ("2026-11-03", 1.0), ("2026-11-20", 1.0)]
    gaps = ec._gaps(rows)
    assert len(gaps) == 1 and "2026-11-03" in gaps[0]


def test_build_metrics_warns_on_thin_sample():
    nav = {"us": _rows(100, 101)}
    report = ec.build_metrics(nav)
    assert any("天净值" in w for w in report["warnings"])


def test_load_nav_skips_bad_rows(tmp_path: Path):
    p = tmp_path / "nav.csv"
    p.write_text("date,market,equity\n2026-11-01,us,100\n坏行,x,y\n2026-11-02,us,105\n",
                 encoding="utf-8")
    nav = ec.load_nav(p)
    assert nav == {"us": [("2026-11-01", 100.0), ("2026-11-02", 105.0)]}
