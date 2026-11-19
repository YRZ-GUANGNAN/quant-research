"""回归保护：禁止在自动化/券商层新增未标注的静默异常处理。

规则：`except ...: pass` 或 `except ...: continue` 必须显式标注
`# noqa: silent-ok`（并说明原因），否则测试失败。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERN = re.compile(r"except[^\n]*:\s*\n\s*(pass|continue)\s*(?:\n|$)", re.M)
TARGETS = [ROOT / "quant" / "automation", ROOT / "quant" / "broker"]


def _offenders() -> list[str]:
    bad: list[str] = []
    for base in TARGETS:
        for p in base.rglob("*.py"):
            text = p.read_text(encoding="utf-8-sig", errors="replace")
            lines = text.splitlines()
            for m in PATTERN.finditer(text):
                ln = text[:m.start()].count("\n") + 1
                context = "\n".join(lines[max(0, ln - 3):ln + 1])
                if "silent-ok" in context:
                    continue
                bad.append(f"{p.relative_to(ROOT)}:{ln}")
    return bad


def test_no_unmarked_silent_except_in_automation_layers() -> None:
    offenders = _offenders()
    assert not offenders, (
        "以下位置存在未标注的静默异常处理，请改为记录日志或标注 # noqa: silent-ok：\n"
        + "\n".join(offenders)
    )
