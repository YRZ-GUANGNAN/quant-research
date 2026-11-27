"""晋级闸门判定 + 模拟阶段资产上限的单测（不依赖真实账户与网络）。"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import promotion_check as pc  # noqa: E402
from quant.broker import cn_broker as cb  # noqa: E402


def _orders(tmp: Path, name: str, rows: list[dict]) -> None:
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / f"cn_orders_{name}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def test_execution_stats_counts_days_and_unconfirmed(tmp_path: Path):
    _orders(tmp_path, "2026-11-01", [{"status": "confirmed"}, {"status": "unconfirmed"}])
    _orders(tmp_path, "2026-11-02", [{"status": "confirmed"}, {"status": "error: 余额不足"}])
    stats = pc.execution_stats(tmp_path, tmp_path)
    assert stats["trade_days"] == 2
    assert stats["confirmed"] == 2
    assert stats["exec_errors"] == 1
    assert stats["unconfirmed_ratio"] == 0.3333


def test_execution_stats_ignores_corrupt_lines(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "cn_orders_2026-11-03.jsonl").write_text(
        '{"status": "confirmed"}\n不是 JSON\n', encoding="utf-8")
    stats = pc.execution_stats(tmp_path, tmp_path)
    assert stats["confirmed"] == 1 and stats["trade_days"] == 1


def test_tracking_hit_reads_latest_snapshot(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "action_tracking_2026-11-01.json").write_text(
        json.dumps({"actions": [{"verdict": "bad"}]}), encoding="utf-8")
    (tmp_path / "action_tracking_2026-11-02.json").write_text(
        json.dumps({"actions": [{"verdict": "good"}, {"verdict": "good"}, {"verdict": "bad"}]}),
        encoding="utf-8")
    hit = pc.tracking_hit(tmp_path)
    assert hit["source"] == "action_tracking_2026-11-02.json"
    assert hit["actions"] == 3 and hit["good"] == 2
    assert hit["hit"] == 0.6667


def test_evaluate_blocks_on_insufficient_days(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pc, "ROOT", tmp_path)
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    report = pc.evaluate(date(2026, 11, 20))
    assert report["ready_for_real"] is False
    assert any("模拟运行天数" in g for g in report["gaps"])


def test_evaluate_can_pass_with_all_conditions(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pc, "ROOT", tmp_path)
    state = tmp_path / "state"
    for i in range(25):
        _orders(state, f"2026-10-{i + 1:02d}", [{"status": "confirmed"}])
    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "action_tracking_2026-11-20.json").write_text(json.dumps(
        {"actions": [{"verdict": "good"}] * 8 + [{"verdict": "bad"}] * 2}), encoding="utf-8")
    (reports / "equity_curve.json").write_text(json.dumps(
        {"total": {"sharpe": 1.2, "days": 20, "return": 0.08}}), encoding="utf-8")
    (reports / "nav_history.csv").write_text(
        "date,market,equity,cash,positions\n"
        "2026-11-18,us,100000,0,1\n"
        "2026-11-20,us,88000,0,1\n", encoding="utf-8")
    for i in range(10):
        d = date(2026, 11, 20).fromordinal(date(2026, 11, 20).toordinal() - i)
        if d.weekday() < 5:
            (reports / f"automation_health_{d}.json").write_text("{}", encoding="utf-8")
    report = pc.evaluate(date(2026, 11, 20))
    assert report["ready_for_real"] is True, report["gaps"]


def test_equity_cap_blocks_oversized_account(monkeypatch):
    """模拟阶段上限：账户总资产超过 CN_MAX_EQUITY 时必须拒绝下单（返回 blocked）。"""
    monkeypatch.delenv("CN_ALLOW_EXECUTION", raising=False)
    monkeypatch.setenv("CN_ALLOW_EXECUTION", "1")
    monkeypatch.setattr(cb, "detect", lambda: {"ths": {"available": True, "blockers": []}})
    monkeypatch.setattr(cb, "account_guard", lambda: (True, ""))
    monkeypatch.setattr(cb, "_focus_trade_window", lambda: True)
    monkeypatch.setattr(cb, "_ths_account", lambda: object())
    monkeypatch.setattr(cb, "_read_with_retry", lambda getter, attempts=2, delay=0.0: getter())
    monkeypatch.setattr(cb, "_read_balance_via_gui",
                        lambda: {"可用金额": 1_000_000.0, "总资产": 5_000_000.0})
    monkeypatch.setattr(cb, "_read_positions_via_clipboard", lambda: [])

    out = cb.execute_target({"600000": 0.2}, dry_run=False,
                            limits=cb.Limits(max_equity_cny=300_000))
    assert out["ok"] is False
    assert out.get("blocked") is True
    assert "上限" in out["reason"]


def test_plan_orders_unaffected_by_equity_cap():
    # 上限守卫在 execute_target 里（需要账户总额），纯算单不应因此改变
    plan, _ = cb.plan_orders({"600000": 0.2}, cash=100_000, equity=200_000,
                             holdings={}, prices={"600000": 10.0})
    assert plan and plan[0]["side"] == "buy"


def test_nav_drawdown_is_per_market(tmp_path: Path):
    """各市场记录天数不同，不能按日相加（会造出假回撤）。"""
    (tmp_path / "nav_history.csv").write_text(
        "date,market,equity,cash,positions\n"
        "2026-11-19,us,100000,0,1\n"
        "2026-11-20,us,90000,0,1\n"
        "2026-11-20,hk,500000,0,1\n"
        "2026-11-21,hk,505000,0,1\n", encoding="utf-8")
    dd = pc.nav_drawdown(tmp_path)
    assert dd["max_drawdown"] == -0.1
    assert dd["per_market"]["us"] == -0.1
    assert dd["per_market"]["hk"] == 0.0


def test_trade_days_unions_all_sources(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pc, "ROOT", tmp_path)
    reports = tmp_path / "reports"; reports.mkdir(parents=True)
    state = tmp_path / "state"; state.mkdir(parents=True)
    (reports / "attribution_2026-11-20.json").write_text("{}", encoding="utf-8")
    (state / "risk_orders_2026-11-21.jsonl").write_text('{"status":"submitted"}\n', encoding="utf-8")
    (state / "action_log.jsonl").write_text('{"date":"2026-11-22"}\n', encoding="utf-8")
    (reports / "equity_curve.json").write_text(json.dumps(
        {"total": {"sharpe": 1.2, "days": 20, "return": 0.08}}), encoding="utf-8")
    (reports / "nav_history.csv").write_text(
        "date,market,equity,cash,positions\n2026-11-23,us,1,0,0\n", encoding="utf-8")
    days = pc.trade_days(reports, state)
    assert {"2026-11-20", "2026-11-21", "2026-11-22", "2026-11-23"} <= days


def test_sharpe_gate_blocks_without_evidence(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pc, "ROOT", tmp_path)
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    sh = pc.realized_sharpe(tmp_path / "reports")
    assert sh["sharpe"] is None


def test_tracking_hit_reads_toplevel_summary(tmp_path: Path):
    """track_actions 的汇总在顶层 total/good/bad/hit_rate，明细在 rows。"""
    (tmp_path / "action_tracking_2026-11-27.json").write_text(json.dumps(
        {"total": 48, "good": 34, "bad": 7, "hit_rate": 0.7083,
         "rows": [{"verdict": "good"}, {"verdict": "bad"}]}), encoding="utf-8")
    hit = pc.tracking_hit(tmp_path)
    assert hit["good"] == 34 and hit["bad"] == 7
    assert hit["actions"] == 41
    assert hit["hit"] == 0.7083
