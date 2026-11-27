"""状态备份：保留策略与完整性校验。"""
from __future__ import annotations

import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import backup_state as bs  # noqa: E402


def _setup(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    monkeypatch.setattr(bs, "BACKUP_DIR", tmp_path / "backups")
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "target_snapshot.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".paper").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".paper" / "state.json").write_text('{"cash": 1}', encoding="utf-8")


def test_backup_creates_zip_with_members(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    out = bs.backup(date(2026, 11, 27), keep=14)
    assert out["ok"] is True and out["files"] >= 2
    with zipfile.ZipFile(out["path"]) as zf:
        names = zf.namelist()
    assert any("target_snapshot" in n for n in names)
    assert any("state.json" in n for n in names)


def test_backup_prunes_old_files(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    for i in range(5):
        bs.backup(date(2026, 11, 20) + timedelta(days=i), keep=14)
    bs.backup(date(2026, 11, 26), keep=2)
    remaining = sorted(p.name for p in (tmp_path / "backups").glob("state-*.zip"))
    assert len(remaining) == 2
    assert remaining[-1] == "state-2026-11-26.zip"


def test_backup_without_sources_fails_cleanly(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    monkeypatch.setattr(bs, "BACKUP_DIR", tmp_path / "backups")
    out = bs.backup(date(2026, 11, 27), keep=1)
    assert out["ok"] is False and "没有可备份" in out["reason"]


def test_collect_skips_missing_paths(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    files = bs.collect()
    assert all(p.exists() for p in files)
    assert len(files) >= 2
