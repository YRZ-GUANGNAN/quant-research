"""流水线脚本的接线契约测试。

为什么要测 PowerShell 脚本：它们是**唯一真正下单的入口**，而 pytest 覆盖不到。
这里用文本契约把关键接线钉住——今天刚踩过一次：把 weight_drift 的默认返回值
从 1 改成 0 之后，「第二轮补单」的触发条件永远为假，成了一段死代码，
而没有任何测试会发现。

只钉"必须同时出现"的组合，不钉格式，避免脚本改排版本就红。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _read(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8-sig")


def test_us_run_passes_same_params_to_simulate_and_execute():
    """模拟与执行必须用同一组参数，否则模拟通过的方案和真下的单不是一回事。"""
    s = _read("us_open_run.ps1")
    assert "--vol-target 0.12" in s
    assert s.count("--vol-target 0.12") >= 2          # 模拟 + 执行
    assert "--cash-share 0.35" in s
    assert s.count("--cash-share 0.35") >= 2


def test_pass2_trigger_uses_strict_drift():
    """第二轮补单的触发条件：weight_drift 必须带 --strict，否则恒返回 0、永不触发。"""
    for name, market in (("us_open_run.ps1", "us"), ("daily_run.ps1", "hk")):
        s = _read(name)
        assert f"weight_drift.py --markets {market} --strict" in s, name


def test_daily_gate_covers_only_its_own_markets():
    """日更只交易 A 股/港股；美股数据在 21:35 才更新，不该拖累日更。"""
    s = _read("daily_run.ps1")
    assert "pre_trade_gate.py --markets cn,hk" in s


def test_all_task_scripts_redirect_temp_to_workspace():
    """C 盘曾只剩 0.03GB，python 临时文件会把任务打挂。"""
    for name in ("daily_run.ps1", "us_open_run.ps1", "cn_open_run.ps1", "watchdog_task.ps1"):
        s = _read(name)
        assert "$env:TEMP = $tmpDir" in s, name


def test_execution_switches_default_off_or_paper_only():
    """真实下单开关不能在脚本里无条件打开。"""
    s = _read("us_open_run.ps1")
    # 允许 LB_ALLOW_EXECUTION=1，但必须是模拟账户
    assert '$env:LB_ACCOUNT_KIND = "paper"' in s
    cn = _read("cn_open_run.ps1")
    assert 'CN_ALLOW_EXECUTION = "1"' in cn
    assert 'CN_MAX_EQUITY' in cn       # 模拟阶段资产上限必须存在


def test_simulate_gate_precedes_execution():
    """下单前模拟必须排在真实下单之前（文本顺序检查）。"""
    for name, exec_marker in (("us_open_run.ps1", "exec_us"),
                              ("daily_run.ps1", "execute_hk")):
        s = _read(name)
        sim_idx = s.find("simulate_plan.py")
        exec_idx = s.find(exec_marker)
        assert sim_idx != -1 and exec_idx != -1, name
        assert sim_idx < exec_idx, f"{name}: 模拟必须在下单之前"
