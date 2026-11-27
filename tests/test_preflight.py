"""开跑前自检的解析与判定单测（不依赖真实 CLI）。"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import preflight as pf  # noqa: E402

NOW = datetime(2026, 11, 27, 18, 0)

SAMPLE = """
Token
Status        valid
DC Region     AP
AccessToken   2026-12-04 16:43  (in 6d 23h)
RefreshToken  2026-12-20 16:43  (in 22d 23h)
"""


def test_parse_token_text_reads_both_expiries():
    tokens = pf.parse_token_text(SAMPLE, NOW)
    # 到期时间是 16:43，NOW 是 18:00，所以 AccessToken 剩 6.9 天而不是整 7 天
    assert tokens["AccessToken"]["days_left"] == 6.9
    assert tokens["RefreshToken"]["days_left"] == 22.9
    assert tokens["AccessToken"]["expires_at"] == "2026-12-04 16:43"


def test_parse_token_text_handles_garbage():
    assert pf.parse_token_text("", NOW) == {}
    assert pf.parse_token_text("AccessToken   不是日期", NOW) == {}


def test_eval_tokens_pass_when_plenty_left():
    out = pf.eval_tokens({"AccessToken": {"days_left": 7.0}, "RefreshToken": {"days_left": 23.0}})
    assert out["status"] == "PASS"


def test_eval_tokens_warns_when_access_almost_expired():
    out = pf.eval_tokens({"AccessToken": {"days_left": 1.0}, "RefreshToken": {"days_left": 23.0}})
    assert out["status"] == "WARN"
    assert "AccessToken" in out["detail"]


def test_eval_tokens_fails_when_refresh_expired():
    out = pf.eval_tokens({"AccessToken": {"days_left": -1.0}, "RefreshToken": {"days_left": -5.0}})
    assert out["status"] == "FAIL"
    assert "重新登录" in out["detail"]


def test_eval_tokens_missing_is_warn():
    assert pf.eval_tokens({})["status"] == "WARN"


def test_disk_report_flags_small_drive(monkeypatch, tmp_path):
    class Usage:
        total = 100 * 1024 ** 3
        used = 99.5 * 1024 ** 3
        free = int(0.5 * 1024 ** 3)

    monkeypatch.setattr(pf.shutil, "disk_usage", lambda p: Usage)
    rows = pf.disk_report([tmp_path])
    assert rows[0]["status"] == "FAIL" and rows[0]["free_gb"] < 1


def test_disk_report_warns_between_thresholds(monkeypatch, tmp_path):
    class Usage:
        total = 100 * 1024 ** 3
        used = 98.5 * 1024 ** 3
        free = int(1.5 * 1024 ** 3)

    monkeypatch.setattr(pf.shutil, "disk_usage", lambda p: Usage)
    assert pf.disk_report([tmp_path])[0]["status"] == "WARN"


def test_run_blocked_when_token_expired(monkeypatch):
    monkeypatch.setattr(pf, "lb_token_text", lambda: "AccessToken   2026-11-01 00:00\n"
                                                   "RefreshToken  2026-11-02 00:00\n")
    report = pf.run(now=NOW)
    assert report["status"] == "BLOCKED"
    assert any("Longbridge" in b for b in report["blocked"])


def test_clean_temp_removes_old_files_only(monkeypatch, tmp_path):
    import os
    import time

    old = tmp_path / "old.tmp"
    new = tmp_path / "new.tmp"
    old.write_text("x" * 1024, encoding="utf-8")
    new.write_text("y", encoding="utf-8")
    past = time.time() - 10 * 86400
    os.utime(old, (past, past))
    monkeypatch.setattr(pf.tempfile, "gettempdir", lambda: str(tmp_path))

    result = pf.clean_temp(min_age_days=3)
    assert result["removed"] == 1
    assert not old.exists()
    assert new.exists()
