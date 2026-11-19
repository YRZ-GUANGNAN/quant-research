# 美股开盘后任务（工作日 21:35）
# 用法: powershell -ExecutionPolicy Bypass -File scripts\us_open_run.ps1
$ErrorActionPreference = "Continue"
Set-Location "D:\3314\quant-research"
$env:PYTHONIOENCODING = "utf-8"
$env:LB_ACCOUNT_KIND = "paper"
$env:LB_ALLOW_EXECUTION = "1"
$script:failures = 0
function Run-StepLocal([string]$desc, [scriptblock]$cmd) {
    & $cmd
    if ($LASTEXITCODE -ne 0) { $script:failures++; Write-Host "[FAIL] $desc (exit $LASTEXITCODE)" }
}

Write-Host "=== 1/4 更新美股行情（Yahoo）==="
python scripts/fetch_market.py --market us

Write-Host "=== 2/4 数据门禁（美股）==="
python scripts/pre_trade_gate.py --markets us
if ($LASTEXITCODE -ne 0) { Write-Host "[ABORT] 门禁未通过"; exit 1 }

Write-Host "=== 3/4 美股下单 + 再平衡 ==="
python -m quant.automation.execution --market us --execute --cash-share 0.35
Write-Host "=== 3b/5 A 股代理袖套（ASHR.US，替代无法交易的沪深港通）==="
python -m quant.automation.execution --market cn_proxy --execute --cash-share 0.65

Write-Host "=== 3c/5 风险识别 → 动作（默认 dry-run）==="
python scripts/risk_act.py --market us
if ($env:RISK_ALLOW_EXECUTION -eq "1") {
    Write-Host "[warn] 风控动作真实执行已开启（美股）"
    python scripts/risk_act.py --market us --execute
}

Write-Host "=== 3d/5 执行对账（期望 vs 实际）==="
Run-StepLocal "reconcile_us" { python scripts/reconcile_today.py --market us }

Write-Host "=== 4/4 更新日度报告 ==="
python scripts/lb_daily_report.py
