# 每日收盘后自动化流程（本地 + 券商模拟盘）
# 用法: powershell -ExecutionPolicy Bypass -File scripts\daily_run.ps1
$ErrorActionPreference = "Continue"
Set-Location "D:\3314\quant-research"
$env:PYTHONIOENCODING = "utf-8"
$env:LB_ACCOUNT_KIND = "paper"
$env:LB_ALLOW_EXECUTION = "1"  # 受控执行通道（policy + ledger 保护）

$script:failures = 0
function Run-Step([string]$desc, [scriptblock]$cmd) {
    & $cmd
    if ($LASTEXITCODE -ne 0) { $script:failures++; Write-Host "[FAIL] $desc (exit $LASTEXITCODE)" }
}

Write-Host "=== 1/9 更新 A 股行情（腾讯；限流自动降并发重试）==="
python scripts/fetch_tx.py --workers 8
if ($LASTEXITCODE -ne 0) {
    Write-Host "[retry] 腾讯限流，60 秒后降并发重试（workers=2）"
    Start-Sleep -Seconds 60
    python scripts/fetch_tx.py --workers 2
}
if ($LASTEXITCODE -ne 0) { $script:failures++; Write-Host "[FAIL] fetch_tx 两次均失败，门禁将阻断下单" }

Write-Host "=== 2/9 东方财富兜底（补腾讯缺口）==="
Run-Step "fetch_eastmoney" { python scripts/fetch_eastmoney_fallback.py }

Write-Host "=== 2b/9 BaoStock 最后兜底 ==="
Run-Step "fetch_baostock" { python scripts/fetch_baostock_fallback.py }

Write-Host "=== 3/9 更新美股/港股行情（Yahoo）==="
Run-Step "fetch_us" { python scripts/fetch_market.py --market us }
Run-Step "fetch_hk" { python scripts/fetch_market.py --market hk }

Write-Host "=== 3b/9 数据门禁（不通过则禁止推进账户与下单）==="
python scripts/pre_trade_gate.py
if ($LASTEXITCODE -ne 0) { Write-Host "[ABORT] 数据门禁未通过：不推进账户、不下单"; exit 1 }

Write-Host "=== 3c/9 A 股交易通道状态（QMT / 同花顺 / 本地）==="
python -m quant.broker.cn_broker --status

Write-Host "=== 4/9 本地 A 股模拟盘推进 ==="
Run-Step "run_paper" { python scripts/run_paper.py }

Write-Host "=== 6/9 目标快照（周期内复用）==="
Run-Step "snapshot" { python scripts/build_target_snapshot.py }

Write-Host "=== 8/9 券商模拟盘：建仓 + 再平衡 ==="
Run-Step "paper_live" { python scripts/paper_live.py }


Run-Step "execute_hk" { python -m quant.automation.execution --market hk --execute }

Write-Host "=== 8b/9 风险识别 → 动作（默认 dry-run）==="
Run-Step "risk_act_hk" { python scripts/risk_act.py --market hk }
if ($env:RISK_ALLOW_EXECUTION -eq "1") {
    Write-Host "[warn] 风控动作真实执行已开启（港股）"
    Run-Step "risk_act_hk_exec" { python scripts/risk_act.py --market hk --execute }
}

Write-Host "=== 8c/9 执行对账（港股）==="
Run-Step "reconcile_hk" { python scripts/reconcile_today.py --market hk }

Write-Host "=== 8d/9 模型健康检查 ==="
python scripts/health_check.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "[warn] 模型健康降级：建议降低仓位或人工复核（不阻断本次流程）"
}

Write-Host "=== 8e/9 业绩归因 ==="
Run-Step "attribution" { python scripts/attribution.py }
Run-Step "track_actions" { python scripts/track_actions.py }

Write-Host "=== 9/9 日度报告 + 自检 ==="
Run-Step "report" { python scripts/lb_daily_report.py }
Run-Step "post_check" { python scripts/post_daily_check.py }

if ($script:failures -gt 0) { Write-Host "[RESULT] $script:failures 步失败"; exit 1 }
Write-Host "[RESULT] 全部步骤成功"
