# A 股开盘任务（工作日 09:35）：数据门禁 → 通道体检 → 实盘风险报告
$ErrorActionPreference = "Continue"
Set-Location "D:\3314\quant-research"
$env:PYTHONIOENCODING = "utf-8"
$env:LB_ACCOUNT_KIND = "paper"
# $env:CN_ALLOW_EXECUTION = "1"   # 解除注释才允许真实 A 股下单

$script:failures = 0
function Run-Step([string]$desc, [scriptblock]$cmd) {
    & $cmd
    if ($LASTEXITCODE -ne 0) { $script:failures++; Write-Host "[FAIL] $desc (exit $LASTEXITCODE)" }
}

Write-Host "=== 1/5 A 股数据门禁 ==="
python scripts/pre_trade_gate.py --markets cn
if ($LASTEXITCODE -ne 0) { Write-Host "[ABORT] A 股数据未就绪，今日不交易"; exit 1 }

Write-Host "=== 2/5 A 股交易通道状态 ==="
python -m quant.broker.cn_broker --status

Write-Host "=== 3/5 实盘资金与风险体检 ==="
Run-Step "cn_risk" { python scripts/cn_risk_report.py }

Write-Host "=== 3b/5 风险识别 → 动作（默认 dry-run）==="
python scripts/risk_act.py --market cn
if ($env:RISK_ALLOW_EXECUTION -eq "1" -and $env:CN_ALLOW_EXECUTION -eq "1") {
    Write-Host "[warn] A 股风控动作真实执行已开启"
    python scripts/risk_act.py --market cn --execute
}

Write-Host "=== 4/5 A 股本地引擎推进（信号侧）==="
Run-Step "run_paper" { python scripts/run_paper.py }

Write-Host "=== 5/5 受控真实下单（默认关闭；需 CN_ALLOW_EXECUTION=1）==="
if ($env:CN_ALLOW_EXECUTION -eq "1") {
    Write-Host "[warn] 真实下单已开启"
    python -m quant.broker.cn_broker --target state/cn_target_live.json --execute
} else {
    Write-Host "[skip] 未设置 CN_ALLOW_EXECUTION=1，今日只做体检与信号，不真实下单"
}

if ($script:failures -gt 0) { Write-Host "[RESULT] $script:failures 步失败"; exit 1 }
Write-Host "[RESULT] A 股开盘流程完成"
