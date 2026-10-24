# 方案 B P0 契约统一与旧写入口止血 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` task-by-task；若运行环境没有该命名 skill，也必须完整遵守同一 checkbox/TDD/分项提交流程。本施工单只授权 P0；每个 P0 分项（P0-A、P0-B、P0-C）先得到预期失败，再做最小实现、验证通过并各自提交。完成 P0-C 后立即停止，不得进入 P1。

**Goal:** 在完全离线、不读取凭证或运行数据的前提下，统一方案 B 仓库契约，封死全部已知旧券商写入口，并让两个旧自动化 wrapper 默认返回阻断状态。

**Architecture:** 用一个无外部依赖的 `legacy_guard.py` 统一退出码 20、阻断消息与异常；旧 CLI 在参数解析和任何外部调用前拒绝 `--execute`，旧 SDK/CLI 写函数在读取 `.env`、导入 SDK、创建 context 或启动 subprocess 前抛出阻断异常。PowerShell wrapper 只保留静态阻断壳。

**Tech Stack:** Python 3.12/3.13、pytest、PowerShell、Git；本阶段不需要 Longbridge SDK、网络、真实数据或任何 secret。

---

## 权威输入与硬边界

- 设计：`docs/superpowers/specs/2026-10-24-quant-safe-automation-plan-b-design.md`
- 总路线图：`docs/superpowers/plans/2026-10-24-quant-safe-automation-plan-b.md`
- 批准起点：`plan-b-approved-20261024`
- 已审核代码基线：`286e9bacfd6e5d080bee3f003be51b02d39dccc6`
- 本轮允许的提交只有 P0-A、P0-B、P0-C。
- 不得 fetch、pull、push、安装包或访问任何业务网络。
- 不得读取 `.env`、OAuth cache、账户、持仓、订单、资产报告或 `D:\3314\quant-research`。
- 不得创建真实 `TradeContext`/`QuoteContext`，不得查询或修改 Windows 定时任务，不得进行 broker write。
- 测试只能使用源码文本、monkeypatch、`tmp_path` 和本地子进程。
- 不得递归枚举/搜索整仓，不得运行 `git grep`，不得浏览 `docs/journal/**`、其他 analysis/report、Git 历史或 Git blob。
- 人工读取范围严格限定如下；`pytest`、`compileall` 与被测 Python import 可以按命令读取其依赖，但遇到需要人工查看的额外文件时必须报告 `BLOCKED_FILE_SCOPE_MISSING` 并停止：
  - 完整读取：本施工单、权威设计、总路线图、`.gitignore`、`quant/broker/lb_sdk.py`、`quant/broker/lb_bridge.py`、`scripts/lb_order_map.py`、`scripts/lb_rebalance.py`、`scripts/lb_fix_hkd.py`、`scripts/lb_reconcile.py`、`scripts/daily_run.ps1`、`scripts/us_open_run.ps1`。
  - 创建后可完整读取：`quant/broker/legacy_guard.py`、`tests/test_repository_contracts.py`、`tests/test_legacy_execute_killswitch.py`、`tests/test_legacy_wrappers.py`。
  - 只允许顺序读取顶部 12 行：`docs/analysis/external-audit-2026-10.md`、`docs/longbridge-setup.md`、`docs/superpowers/specs/2026-10-24-quant-safe-automation-design.md`、`docs/superpowers/plans/2026-10-24-quant-safe-automation-deepseek-handoff.md`、`docs/superpowers/reviews/2026-10-24-lead-review.md`。

## Task 0：批准 tag 与离线基线预检

**Files:**

- Read only: `git status` 与 `git rev-parse` 所需 Git ref metadata，以及三份方案 B 权威文档
- Modify: none
- Test: existing suite only

- [ ] **Step 1: Verify the approved starting point without contacting a remote**

Run:

```powershell
git status --short --branch
$head = git rev-parse HEAD
$tag = git rev-parse "plan-b-approved-20261024^{commit}"
$localMain = git rev-parse origin/main
Write-Output "HEAD=$head"
Write-Output "TAG=$tag"
Write-Output "ORIGIN_MAIN_LOCAL=$localMain"
if ($head -ne $tag -or $head -ne $localMain) { exit 1 }
```

Expected: exit 0；工作树干净；三个 SHA 完全一致。不得运行 `git fetch` 或 `git pull`。

- [ ] **Step 2: Create the isolated implementation branch**

Run:

```powershell
git switch -c deepseek/plan-b
```

Expected: exit 0，当前分支为 `deepseek/plan-b`。若分支已存在或工作树不干净，停止并报告，不得 reset、stash 或删除用户文件。

- [ ] **Step 3: Record the baseline test evidence**

Run:

```powershell
python --version
python -m pytest -q
python -m compileall -q quant scripts
git diff --check
```

Expected: 四条命令均 exit 0；pytest 显示 `42 passed`。任何差异都返回 `BLOCKED_BASELINE_MISMATCH` 并停止。

---

## Task 1：P0-A 仓库契约与根锚定 ignore

**Files:**

- Modify: `.gitignore`
- Create: `tests/test_repository_contracts.py`
- Verify only: `docs/analysis/external-audit-2026-10.md`
- Verify only: `docs/longbridge-setup.md`
- Verify only: `docs/superpowers/specs/2026-10-24-quant-safe-automation-design.md`
- Verify only: `docs/superpowers/plans/2026-10-24-quant-safe-automation-deepseek-handoff.md`
- Verify only: `docs/superpowers/reviews/2026-10-24-lead-review.md`

- [ ] **Step 1: Add the failing repository-contract tests**

Create `tests/test_repository_contracts.py` with this complete content:

```python
from __future__ import annotations

import subprocess
from itertools import islice
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASELINE = "286e9bacfd6e5d080bee3f003be51b02d39dccc6"
SPEC = ROOT / "docs/superpowers/specs/2026-10-24-quant-safe-automation-plan-b-design.md"
MASTER = ROOT / "docs/superpowers/plans/2026-10-24-quant-safe-automation-plan-b.md"
P0_PLAN = ROOT / "docs/superpowers/plans/2026-10-24-quant-safe-automation-plan-b-p0.md"
HISTORICAL = (
    ROOT / "docs/analysis/external-audit-2026-10.md",
    ROOT / "docs/longbridge-setup.md",
    ROOT / "docs/superpowers/specs/2026-10-24-quant-safe-automation-design.md",
    ROOT / "docs/superpowers/plans/2026-10-24-quant-safe-automation-deepseek-handoff.md",
    ROOT / "docs/superpowers/reviews/2026-10-24-lead-review.md",
)


def _git_check_ignore(path: str) -> int:
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", "--", path],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode


def _read_top_lines(path: Path, limit: int = 12) -> str:
    with path.open("r", encoding="utf-8-sig") as handle:
        return "".join(islice(handle, limit))


def _section(text: str, start: str, end: str) -> str:
    _prefix, start_sep, remainder = text.partition(start)
    assert start_sep, start
    body, end_sep, _suffix = remainder.partition(end)
    assert end_sep, end
    return body


def test_runtime_paths_are_root_anchored() -> None:
    lines = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert {"/state/", "/logs/", "/reports/", "/snapshots/", "/data/cache/"} <= lines
    assert not ({"state/", "logs/", "reports/", "snapshots/", "data/cache/"} & lines)


def test_docs_reports_are_not_hidden_by_runtime_ignore() -> None:
    assert _git_check_ignore("reports/probe.json") == 0
    assert _git_check_ignore("docs/superpowers/reports/probe.md") == 1


def test_nested_environment_and_paper_paths_remain_ignored() -> None:
    for path in (
        "nested/.venv/pyvenv.cfg",
        "nested/venv/pyvenv.cfg",
        "nested/.paper/session.json",
    ):
        assert _git_check_ignore(path) == 0


def test_authoritative_metadata_and_execution_contract() -> None:
    spec = SPEC.read_text(encoding="utf-8")
    master = MASTER.read_text(encoding="utf-8")
    spec_top = _read_top_lines(SPEC, 25)
    master_top = _read_top_lines(MASTER, 25)
    p0_top = _read_top_lines(P0_PLAN, 12)
    spec_oauth = _section(spec, "## 11. Longbridge", "## 12. Profiles")
    master_oauth = _section(master, "### P5-A：", "### P5-B：")
    master_cli = _section(master, "### P5-C：", "### P5 阶段出口")
    master_deploy = _section(master, "## 8. Codex lead", "## 9. 每阶段")

    assert f"- 审核基线：`{BASELINE}`" in spec_top
    assert f"> 已审核代码基线：`{BASELINE}`" in master_top
    assert "初次授权范围：**只做 P0" in master_top
    assert "--mode dry-run|connected-dry-run|execute" in master_cli
    assert "OAuthBuilder.build(reject_interactive_callback)" in spec_oauth
    assert "Config.from_oauth" in spec_oauth
    assert "OAuthBuilder.build(reject_interactive_callback)" in master_oauth
    assert "Config.from_oauth" in master_oauth
    assert "policy、enrollment 和 append-only ledger" in master_deploy
    assert "不得进入 P1" in p0_top


def test_historical_documents_have_top_level_superseded_banner() -> None:
    for path in HISTORICAL:
        top = _read_top_lines(path)
        assert any(marker in top for marker in ("历史", "已被取代")), path
        assert "2026-10-24-quant-safe-automation-plan-b" in top, path
```

- [ ] **Step 2: Run the new test and confirm the intended failure**

Run:

```powershell
python -m pytest tests/test_repository_contracts.py -q
```

Expected: non-zero；至少 `test_runtime_paths_are_root_anchored` 因当前 `.gitignore` 使用未锚定的 `state/`、`reports/` 等条目而失败。若测试意外全绿，停止并报告 `BLOCKED_TEST_NOT_RED`。

- [ ] **Step 3: Replace `.gitignore` with the normalized safe form**

Use this complete content:

```gitignore
# Python bytecode and local environments
__pycache__/
*.py[cod]
.venv/
venv/

# Secrets and OS metadata
.env
.DS_Store
Thumbs.db

# Reproducible market-data caches
/data/cache/
data/raw/

# Local paper/runtime state; never commit
.paper/
/state/
/logs/
/reports/
/snapshots/
*.log
```

Do not add an ignore rule for `docs/superpowers/reports/`.

- [ ] **Step 4: Run the targeted and fixed validation suite**

Run:

```powershell
python -m pytest tests/test_repository_contracts.py -q
python -m pytest -q
python -m compileall -q quant scripts
git diff --check
```

Expected: every command exits 0；the targeted test ends with `5 passed`；the full suite ends at `[100%]`。

- [ ] **Step 5: Commit P0-A only**

Run:

```powershell
git add .gitignore tests/test_repository_contracts.py
git diff --cached --check
git commit -m "chore: 统一方案B仓库安全契约"
```

Expected: exit 0；commit只包含上述两个文件。不要修改两份权威文档或五份历史文档，它们已由批准 tag 固定。

---

## Task 2：P0-B 统一旧写阻断原语与模块级 kill-switch

**Files:**

- Create: `quant/broker/legacy_guard.py`
- Modify: `quant/broker/lb_sdk.py`
- Modify: `quant/broker/lb_bridge.py`
- Create: `tests/test_legacy_execute_killswitch.py`

- [ ] **Step 1: Add failing tests for direct legacy broker writes**

Create `tests/test_legacy_execute_killswitch.py` with this initial content:

```python
from __future__ import annotations

import builtins
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from quant.broker import lb_bridge, lb_sdk
from quant.broker.legacy_guard import LEGACY_BROKER_WRITE_EXIT, LegacyBrokerWriteBlocked


ROOT = Path(__file__).resolve().parents[1]
EXECUTE_TOKENS = ("--e", "--ex", "--exec", "--execute", "--execute=true")


def _load_script(name: str) -> ModuleType:
    path = ROOT / "scripts" / name
    module_name = f"p0_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _boom(*_args, **_kwargs):
    raise AssertionError("external broker path was reached")


def test_lb_sdk_submit_blocks_before_env_config_context_or_sdk_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def guarded_import(name: str, *args, **kwargs):
        if name == "longbridge" or name.startswith("longbridge."):
            raise AssertionError("Longbridge SDK import was reached")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(lb_sdk, "load_env", _boom)
    monkeypatch.setattr(lb_sdk, "_config", _boom)
    monkeypatch.setattr(lb_sdk, "trade_context", _boom)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    with pytest.raises(LegacyBrokerWriteBlocked) as caught:
        lb_sdk.submit_order("AAPL.US", "buy", 1, 100.0)
    assert caught.value.exit_code == LEGACY_BROKER_WRITE_EXIT == 20


@pytest.mark.parametrize("function_name", ["order_buy", "order_sell"])
def test_lb_bridge_write_functions_block_before_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    function_name: str,
) -> None:
    monkeypatch.setattr(lb_bridge, "_json", _boom)
    with pytest.raises(LegacyBrokerWriteBlocked) as caught:
        getattr(lb_bridge, function_name)("AAPL.US", 1, 100.0, "LO")
    assert caught.value.exit_code == 20


@pytest.mark.parametrize("token", EXECUTE_TOKENS)
def test_lb_bridge_run_blocks_execute_tokens_before_path_or_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    token: str,
) -> None:
    monkeypatch.setattr(lb_bridge, "LB_BIN", tmp_path / "missing-longbridge.exe")
    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    with pytest.raises(LegacyBrokerWriteBlocked) as caught:
        lb_bridge._run(["order", "buy", "AAPL.US", token])
    assert caught.value.exit_code == 20
```

- [ ] **Step 2: Run the test and confirm import failure**

Run:

```powershell
python -m pytest tests/test_legacy_execute_killswitch.py -q
```

Expected: collection fails because `quant.broker.legacy_guard` does not exist. This is the required RED state.

- [ ] **Step 3: Create the dependency-free guard module**

Create `quant/broker/legacy_guard.py` with this complete content:

```python
"""Fail-closed guard for broker write paths superseded by automation Plan B."""

from __future__ import annotations

from collections.abc import Sequence


LEGACY_BROKER_WRITE_EXIT = 20
BLOCKED_LEGACY_EXECUTE = "BLOCKED_LEGACY_EXECUTE"


class LegacyBrokerWriteBlocked(RuntimeError):
    """Raised before any external side effect on a superseded write path."""

    exit_code = LEGACY_BROKER_WRITE_EXIT


def block_legacy_broker_write(operation: str) -> None:
    raise LegacyBrokerWriteBlocked(f"{BLOCKED_LEGACY_EXECUTE}: {operation}")


def _is_execute_token(token: str) -> bool:
    option = token.partition("=")[0]
    return (
        option == "--execute"
        or (
            option.startswith("--")
            and len(option) > 2
            and "--execute".startswith(option)
        )
    )


def reject_execute_argv(argv: Sequence[str]) -> int | None:
    if not any(_is_execute_token(token) for token in argv):
        return None
    print(BLOCKED_LEGACY_EXECUTE)
    return LEGACY_BROKER_WRITE_EXIT


def block_execute_tokens(tokens: Sequence[str], operation: str) -> None:
    if any(_is_execute_token(token) for token in tokens):
        block_legacy_broker_write(operation)
```

- [ ] **Step 4: Replace direct module write functions with immediate guards**

In `quant/broker/lb_sdk.py`, import the guard near the top and replace the complete `submit_order()` body with:

```python
from quant.broker.legacy_guard import block_legacy_broker_write


def submit_order(
    symbol: str,
    side: str,
    quantity: int,
    price: float | None = None,
    order_type: str = "LO",
    remark: str = "quant-research",
):
    """Block the superseded Legacy SDK write path before reading credentials."""
    del symbol, side, quantity, price, order_type, remark
    block_legacy_broker_write("quant.broker.lb_sdk.submit_order")
```

Delete the old `SubmitOrderOptions` construction loop. Do not call `load_env()`, `_config()` or `trade_context()` from this function.

In `quant/broker/lb_bridge.py`, import the same guard and replace the two complete functions with:

```python
from quant.broker.legacy_guard import block_execute_tokens, block_legacy_broker_write


def order_buy(
    symbol: str,
    quantity: int,
    price: float | None = None,
    order_type: str = "MO",
) -> dict:
    del symbol, quantity, price, order_type
    block_legacy_broker_write("quant.broker.lb_bridge.order_buy")


def order_sell(
    symbol: str,
    quantity: int,
    price: float | None = None,
    order_type: str = "MO",
) -> dict:
    del symbol, quantity, price, order_type
    block_legacy_broker_write("quant.broker.lb_bridge.order_sell")
```

Before changing code below, replace the entire module docstrings (from the first `"""` through the closing `"""` before `from __future__`) so they no longer advertise a usable legacy write channel. In `quant/broker/lb_sdk.py`, use this complete replacement:

```text
Longbridge Legacy SDK 兼容模块（只读函数保留；写入入口已永久阻断）。

`submit_order()` 仅抛出 `LegacyBrokerWriteBlocked(exit_code=20)`；本模块不再是交易通道，也不提供解锁方式。账户/报价读取函数只供历史兼容测试使用，canonical automation 不得导入本模块。
```

In `quant/broker/lb_bridge.py`, use this complete replacement:

```text
Longbridge CLI Legacy 兼容模块（只读函数保留；任何 order buy/sell 写入已永久阻断）。

`_run()` 只允许历史只读查询；所有 execute/cancel/write token 在启动 CLI 前返回 `BLOCKED_LEGACY_EXECUTE`。canonical automation 不得调用本模块。
```

Do not leave usage examples that show `--execute`, `order buy`, `order sell`, or a claim that this module is an active trading channel.

Also replace `_run()` with this complete implementation so every execute spelling is rejected before `LB_BIN.exists()`:

```python
def _run(args: list[str], *, timeout: int = 40) -> str:
    block_execute_tokens(args, "quant.broker.lb_bridge._run")
    if not LB_BIN.exists():
        raise LongbridgeError(f"未找到 longbridge CLI: {LB_BIN}")
    cmd = [str(LB_BIN), *args, "--format", "json"]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise LongbridgeError((proc.stderr or proc.stdout or "").strip()[:500])
    return proc.stdout.strip()
```

- [ ] **Step 5: Run the direct-write tests**

Run:

```powershell
python -m pytest tests/test_legacy_execute_killswitch.py -q
```

Expected: exit 0；`8 passed`；no `.env` read、SDK import/context or subprocess is reached by a write path。

P0-B 的 Task 2 与 Task 3 合并为同一个提交边界；不要在此处创建半成品 commit。完成 Step 8 的完整验证后，再按 Step 9 提交 P0-B。

---

## Task 3：P0-B 旧脚本 execute/cancel 入口最早阻断

**Files:**

- Modify: `scripts/lb_order_map.py`
- Modify: `scripts/lb_rebalance.py`
- Modify: `scripts/lb_fix_hkd.py`
- Modify: `scripts/lb_reconcile.py`
- Modify: `tests/test_legacy_execute_killswitch.py`

- [ ] **Step 1: Extend the test file with CLI and low-level zero-call cases**

Append this complete block to `tests/test_legacy_execute_killswitch.py`:

```python
@pytest.mark.parametrize(
    ("script_name", "base_argv", "external_names"),
    [
        ("lb_order_map.py", [], ("cli_json", "net_assets_usd")),
        ("lb_rebalance.py", ["--market", "us"], ("cli_json", "market_sessions")),
        ("lb_fix_hkd.py", [], ("cli", "hkd_cash")),
        ("lb_reconcile.py", [], ("cli_json",)),
    ],
)
@pytest.mark.parametrize("execute_token", EXECUTE_TOKENS)
def test_legacy_cli_execute_returns_20_before_external_calls(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    script_name: str,
    base_argv: list[str],
    external_names: tuple[str, ...],
    execute_token: str,
) -> None:
    module = _load_script(script_name)
    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    for name in external_names:
        if hasattr(module, name):
            monkeypatch.setattr(module, name, _boom)

    assert module.main([*base_argv, execute_token]) == 20
    assert "BLOCKED_LEGACY_EXECUTE" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("script_name", "base_argv"),
    [
        ("lb_order_map.py", []),
        ("lb_rebalance.py", ["--market", "us"]),
        ("lb_fix_hkd.py", []),
        ("lb_reconcile.py", []),
    ],
)
@pytest.mark.parametrize("execute_token", ["--execute", "--exec"])
def test_legacy_cli_subprocess_returns_20_without_external_binary(
    tmp_path: Path,
    script_name: str,
    base_argv: list[str],
    execute_token: str,
) -> None:
    env = os.environ.copy()
    for key in (
        "LONGBRIDGE_APP_KEY",
        "LONGBRIDGE_APP_SECRET",
        "LONGBRIDGE_ACCESS_TOKEN",
    ):
        env.pop(key, None)
    env.pop("PYTHONPATH", None)
    env.pop("LB_ACCOUNT_KIND", None)
    env["LOCALAPPDATA"] = str(tmp_path / "missing-localappdata")
    env["LONGBRIDGE_BIN"] = str(tmp_path / "missing-longbridge.exe")
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(ROOT / "scripts" / script_name),
            *base_argv,
            execute_token,
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 20
    assert "BLOCKED_LEGACY_EXECUTE" in result.stdout + result.stderr


def test_order_map_internal_submit_blocks_before_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("lb_order_map.py")
    monkeypatch.setattr(module, "cli_json", _boom)
    with pytest.raises(LegacyBrokerWriteBlocked):
        module.submit(
            [{"symbol": "AAPL.US", "side": "buy", "quantity": 1, "price": 100.0}],
            dry_run=False,
        )


@pytest.mark.parametrize(
    ("script_name", "function_name"),
    [
        ("lb_order_map.py", "cli_json"),
        ("lb_fix_hkd.py", "cli"),
        ("lb_reconcile.py", "cli_json"),
    ],
)
@pytest.mark.parametrize("execute_token", EXECUTE_TOKENS)
def test_low_level_execute_tokens_block_before_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    script_name: str,
    function_name: str,
    execute_token: str,
) -> None:
    module = _load_script(script_name)
    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    with pytest.raises(LegacyBrokerWriteBlocked):
        getattr(module, function_name)(["order", "cancel", "x", execute_token])


def test_reconcile_default_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_script("lb_reconcile.py")
    calls: list[list[str]] = []

    def fake_cli(args: list[str]):
        calls.append(args)
        return []

    monkeypatch.setattr(module, "cli_json", fake_cli)
    assert module.main([]) == 0
    assert calls == [["order"]]
```

- [ ] **Step 2: Run the extended test and confirm the intended failures**

Run:

```powershell
python -m pytest tests/test_legacy_execute_killswitch.py -q
```

Expected: non-zero because current `main()` functions do not accept `argv`, direct submit is not blocked, and low-level CLI helpers do not reject execute tokens.

- [ ] **Step 3: Update `lb_order_map.py` with an exact two-layer parser guard**

Immediately after `from pathlib import Path`, add `from collections.abc import Sequence`. Immediately after the existing repository-root `sys.path.insert(...)`, add this import block:

```python
from quant.broker.legacy_guard import (  # noqa: E402
    BLOCKED_LEGACY_EXECUTE,
    LEGACY_BROKER_WRITE_EXIT,
    block_execute_tokens,
    block_legacy_broker_write,
    reject_execute_argv,
)
```

Replace the current `main()` header, parser and `LB_ACCOUNT_KIND`/`paper_config()` guard—from `def main()` through the line immediately before `total = net_assets_usd()`—with this complete block:

```python
def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    blocked = reject_execute_argv(raw_argv)
    if blocked is not None:
        return blocked

    ap = argparse.ArgumentParser(
        description="模型 v4 美港股 → 券商模拟盘下单（旧写入口已禁用）",
        allow_abbrev=False,
    )
    ap.add_argument("--execute", action="store_true", help="已禁用；返回 20")
    ap.add_argument("--dry-run", action="store_true", help="兼容旧参数")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--us-weight", type=float, default=0.75)
    ap.add_argument("--only", choices=["us", "hk", "sg", "both"], default="both")
    ap.add_argument("--skip-existing", action="store_true", help="跳过今日已有订单的标的")
    args = ap.parse_args(raw_argv)
    if args.execute:
        print(BLOCKED_LEGACY_EXECUTE)
        return LEGACY_BROKER_WRITE_EXIT
    args.dry_run = True

    total = net_assets_usd()
```

This replacement deletes the old environment-variable and SDK paper checks. Leave the body after `total = net_assets_usd()` byte-for-byte unchanged in this step.
Replace the module docstring usage lines with only `python scripts/lb_order_map.py --dry-run` and label the output `legacy preview only`; remove examples that imply `--execute` or real submission.

- [ ] **Step 4: Update `lb_rebalance.py` with its exact parser guard**

Replace the inclusive block from `import argparse, json, os, sys` through `sys.path.insert(0, str(ROOT / "scripts"))` with this complete block:

```python
import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from quant.broker.legacy_guard import (  # noqa: E402
    BLOCKED_LEGACY_EXECUTE,
    LEGACY_BROKER_WRITE_EXIT,
    reject_execute_argv,
)
```

Keep the existing `from lb_order_map import ...` immediately after that block. Replace the current `main()` header, parser and `LB_ACCOUNT_KIND`/`paper_config()` guard—from `def main()` through the line immediately before `sess = market_sessions()`—with:

```python
def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    blocked = reject_execute_argv(raw_argv)
    if blocked is not None:
        return blocked

    ap = argparse.ArgumentParser(
        description="旧再平衡预览入口（写入已禁用）",
        allow_abbrev=False,
    )
    ap.add_argument("--market", choices=["us", "hk"], required=True)
    ap.add_argument("--execute", action="store_true", help="已禁用；返回 20")
    ap.add_argument("--threshold", type=float, default=0.15)
    args = ap.parse_args(raw_argv)
    if args.execute:
        print(BLOCKED_LEGACY_EXECUTE)
        return LEGACY_BROKER_WRITE_EXIT

    sess = market_sessions()
```

Leave the body after `sess = market_sessions()` unchanged. Its later execute branch remains unreachable through this parser, and imported `cli_json()` receives the independent low-level guard in Step 7.
Replace the module docstring usage section with `python scripts/lb_rebalance.py --market us --dry-run` and `python scripts/lb_rebalance.py --market hk --dry-run`; state that both are legacy previews and that no execute mode exists.

- [ ] **Step 5: Add root bootstrap and the exact guard to `lb_fix_hkd.py`**

Replace its import/bootstrap block from `import argparse, json, os, subprocess, sys` through the `LB = ...` assignment with:

```python
import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant.broker.legacy_guard import (  # noqa: E402
    BLOCKED_LEGACY_EXECUTE,
    LEGACY_BROKER_WRITE_EXIT,
    block_execute_tokens,
    reject_execute_argv,
)

LB = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "longbridge" / "longbridge.exe"
```

Replace the current `main()` header and parser—from `def main()` through `args = ap.parse_args()`—with:

```python
def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    blocked = reject_execute_argv(raw_argv)
    if blocked is not None:
        return blocked

    ap = argparse.ArgumentParser(
        description="旧港币现金补正预览入口（写入已禁用）",
        allow_abbrev=False,
    )
    ap.add_argument("--execute", action="store_true", help="已禁用；返回 20")
    ap.add_argument("--target-buffer", type=float, default=0.05)
    args = ap.parse_args(raw_argv)
    if args.execute:
        print(BLOCKED_LEGACY_EXECUTE)
        return LEGACY_BROKER_WRITE_EXIT
```

Leave the body beginning at `cash = hkd_cash()` unchanged; the later write branch is unreachable and `cli()` receives a second low-level guard in Step 7.
Replace the module docstring usage section with `python scripts/lb_fix_hkd.py --dry-run` and explicitly state that the old sell/execute mode is disabled.

- [ ] **Step 6: Make `lb_reconcile.py` explicitly read-only with root bootstrap**

Change the module docstring usage section so both documented invocations are previews:

```text
用法：
    python scripts/lb_reconcile.py             # 只读预览（默认）
    python scripts/lb_reconcile.py --dry-run   # 兼容写法；仍为只读预览
```

Replace its import/bootstrap block from `import argparse` through the `LB = ...` assignment with:

```python
import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant.broker.legacy_guard import (  # noqa: E402
    BLOCKED_LEGACY_EXECUTE,
    LEGACY_BROKER_WRITE_EXIT,
    block_execute_tokens,
    reject_execute_argv,
)

LB = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "longbridge" / "longbridge.exe"
```

Replace its current `main()` header and parser with:

```python
def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    blocked = reject_execute_argv(raw_argv)
    if blocked is not None:
        return blocked

    ap = argparse.ArgumentParser(
        description="重复挂单只读预览（旧撤单入口已禁用）",
        allow_abbrev=False,
    )
    ap.add_argument("--dry-run", action="store_true", help="兼容参数；当前始终只读")
    ap.add_argument("--execute", action="store_true", help="已禁用；返回 20")
    args = ap.parse_args(raw_argv)
    if args.execute:
        print(BLOCKED_LEGACY_EXECUTE)
        return LEGACY_BROKER_WRITE_EXIT
    args.dry_run = True
```

Leave the body beginning at `orders = cli_json(["order"]) or []` unchanged. Because `args.dry_run` is always true, its cancel branch cannot be reached from `main()`.

- [ ] **Step 7: Guard every low-level write-bearing helper**

At the first line of `lb_order_map.cli_json()` add:

```python
block_execute_tokens(args, "scripts.lb_order_map.cli_json")
```

At the first line of `lb_order_map.submit()` add:

```python
if not dry_run:
    block_legacy_broker_write("scripts.lb_order_map.submit")
```

At the first line of `lb_fix_hkd.cli()` add:

```python
block_execute_tokens(args, "scripts.lb_fix_hkd.cli")
```

At the first line of `lb_reconcile.cli_json()` add:

```python
block_execute_tokens(args, "scripts.lb_reconcile.cli_json")
```

`lb_rebalance.py` imports `cli_json` from `lb_order_map.py`, so it inherits that low-level guard. Do not add any other write path or bypass.

- [ ] **Step 8: Run P0-B tests and fixed validation**

Run:

```powershell
python -m pytest tests/test_legacy_execute_killswitch.py -q
python -m pytest -q
python -m compileall -q quant scripts
git diff --check
```

Expected: every command exits 0；the targeted suite ends at `[100%]`；no test imports Longbridge or starts a broker subprocess.

- [ ] **Step 9: Commit all P0-B changes together (唯一 P0-B commit)**

Run:

```powershell
git add quant/broker/legacy_guard.py quant/broker/lb_sdk.py quant/broker/lb_bridge.py scripts/lb_order_map.py scripts/lb_rebalance.py scripts/lb_fix_hkd.py scripts/lb_reconcile.py tests/test_legacy_execute_killswitch.py
git diff --cached --check
git commit -m "fix: 禁用旧券商写入入口"
```

Expected: exit 0；commit只包含 P0-B 的八个文件。

---

## Task 4：P0-C 旧 PowerShell wrapper 默认阻断

**Files:**

- Modify: `scripts/daily_run.ps1`
- Modify: `scripts/us_open_run.ps1`
- Create: `tests/test_legacy_wrappers.py`

- [ ] **Step 1: Add the failing static and process tests**

Create `tests/test_legacy_wrappers.py` with this complete content:

```python
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WRAPPERS = (
    ROOT / "scripts/daily_run.ps1",
    ROOT / "scripts/us_open_run.ps1",
)


@pytest.mark.parametrize("path", WRAPPERS)
def test_legacy_wrapper_is_a_static_fail_closed_shell(path: Path) -> None:
    text = path.read_text(encoding="utf-8-sig")
    assert '$ErrorActionPreference = "Stop"' in text
    assert "BLOCKED_LEGACY_WRAPPER" in text
    assert "exit 20" in text
    for forbidden in (
        "lb_order_map.py",
        "lb_rebalance.py",
        "--execute",
        "LB_ACCOUNT_KIND",
        "Set-Location",
        '$ErrorActionPreference = "Continue"',
    ):
        assert forbidden not in text


@pytest.mark.parametrize("path", WRAPPERS)
def test_legacy_wrapper_returns_20_without_running_children(path: Path) -> None:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell is unavailable on this test host")
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(path)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 20
    assert "BLOCKED_LEGACY_WRAPPER" in result.stderr + result.stdout
```

- [ ] **Step 2: Run the test and confirm both old wrappers fail the contract**

Run:

```powershell
python -m pytest tests/test_legacy_wrappers.py -q
```

Expected: non-zero；the current wrappers still contain `Continue`、`Set-Location`、`LB_ACCOUNT_KIND` and old `--execute` calls.

- [ ] **Step 3: Replace both wrappers with the complete fail-closed shell**

Use this complete content for both `scripts/daily_run.ps1` and `scripts/us_open_run.ps1`:

```powershell
$ErrorActionPreference = "Stop"

[Console]::Error.WriteLine(
    "BLOCKED_LEGACY_WRAPPER: 旧自动化入口已停用；等待方案 B canonical trade_cycle.py 经审核交付。"
)
exit 20
```

Do not retain old data refresh, OAuth, report, order, hard-coded working directory or environment-variable logic. Do not query or modify Windows tasks.

- [ ] **Step 4: Run P0-C and full validation**

Run:

```powershell
python -m pytest tests/test_legacy_wrappers.py -q
python -m pytest -q
python -m compileall -q quant scripts
git diff --check
```

Expected: every command exits 0；the targeted suite reports `4 passed` when PowerShell is available, otherwise `2 passed, 2 skipped`；the full suite ends at `[100%]`。

- [ ] **Step 5: Commit P0-C only**

Run:

```powershell
git add scripts/daily_run.ps1 scripts/us_open_run.ps1 tests/test_legacy_wrappers.py
git diff --cached --check
git commit -m "fix: 让旧自动化包装器默认阻断"
```

Expected: exit 0；commit只包含两个 wrapper 和一个测试文件。

---

## Task 5：P0 阶段出口与交付报告

**Files:**

- Modify: none
- Test: all P0 targeted tests and full suite

- [ ] **Step 1: Run the P0 targeted suites**

Run:

```powershell
python -m pytest tests/test_repository_contracts.py tests/test_legacy_execute_killswitch.py tests/test_legacy_wrappers.py -q
```

Expected: exit 0；all tests pass or only the two documented PowerShell process cases skip on hosts without PowerShell.

- [ ] **Step 2: Prove the wrappers contain no execution tokens**

Run:

```powershell
$null = rg -n -e "--execute" -e "lb_order_map.py" -e "lb_rebalance.py" -e "LB_ACCOUNT_KIND" -e "Set-Location" scripts/daily_run.ps1 scripts/us_open_run.ps1
$wrapperRgExit = $LASTEXITCODE
if ($wrapperRgExit -eq 0) { throw "旧 wrapper 仍包含被禁止的执行 token" }
if ($wrapperRgExit -gt 1) { exit $wrapperRgExit }
exit 0
```

Expected: exit 0；`rg` 本身为 1（无匹配），由脚本转换成 PASS。

- [ ] **Step 3: Run the complete offline verification**

Run:

```powershell
python -m pytest -q
python -m compileall -q quant scripts
git diff --check
git status --short --branch
git log -3 --oneline
```

Expected: tests、compileall、diff check 都 exit 0；工作树干净；最新三条 commit 依次对应 P0-C、P0-B、P0-A；分支仍为 `deepseek/plan-b`。

- [ ] **Step 4: Deliver the evidence and stop**

Use this exact report shape:

```text
阶段：P0
状态：PASS / BLOCKED / FAIL
分支：deepseek/plan-b
P0-A 提交 SHA：
P0-B 提交 SHA：
P0-C 提交 SHA：
修改文件：
新增测试及目的：
验证命令、退出码、结果：
安全声明：未读取凭证；未访问任何网络；未创建真实 TradeContext/QuoteContext；
          未访问 D 盘运行数据；未查询或修改定时任务；未进行 broker write。
仍未完成或需 Codex lead 的事项：P1 尚无批准施工单。
下一阶段：未开始，等待 Codex 审核。
```

After emitting the report, stop. Do not push, merge, edit `main`, run OAuth, inspect accounts, access runtime data, create a P1 plan, or begin P1.
