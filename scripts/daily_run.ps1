# 每日收盘后自动化流程（本地 + 券商模拟盘）
# 用法: powershell -ExecutionPolicy Bypass -File scripts\daily_run.ps1
$ErrorActionPreference = "Continue"
$script:failures = 0
function Run-Step([string]$desc, [scriptblock]$cmd) {
    & $cmd
    if ($LASTEXITCODE -ne 0) { $script:failures++; Write-Host "[FAIL] $desc (exit $LASTEXITCODE)" }
}
Set-Location "D:\3314\quant-research"
$env:PYTHONIOENCODING = "utf-8"
$env:LB_ACCOUNT_KIND = "paper"  # 代码级护栏：仅允许模拟账户下单

Write-Host "=== 1/5 更新 A 股行情（腾讯，失败则跳过）==="
$script:failures = 0
python scripts/fetch_tx.py --workers 8
if ($LASTEXITCODE -ne 0) {
    Write-Host "[retry] 腾讯限流，60 秒后降并发重试（workers=2）"
    Start-Sleep -Seconds 60
    python scripts/fetch_tx.py --workers 2
}
if ($LASTEXITCODE -ne 0) { $script:failures++; Write-Host "[FAIL] fetch_tx 两次均失败，后续门禁会阻断下单" }

Write-Host "=== 2/5 BaoStock 备用增量补齐 ==="
Run-Step "fetch_baostock" { python scripts/fetch_baostock_fallback.py }

Write-Host "=== 2b/5 更新美股/港股行情（Yahoo）==="
Run-Step "fetch_us" { python scripts/fetch_market.py --market us }
Run-Step "fetch_hk" { python scripts/fetch_market.py --market hk }

Write-Host "=== 3/5 本地 A 股模拟盘推进 ==="
Run-Step "run_paper" { python scripts/run_paper.py }

Write-Host "=== 4/5 盘中：用实时报价执行本地挂单 ==="
Run-Step "paper_live" { python scripts/paper_live.py }

Write-Host "=== 5/5 券商模拟盘：美港股自动下单（幂等）==="
Run-Step "order_map" { python scripts/lb_order_map.py --skip-existing --only both --execute }
Run-Step "rebalance_hk" { python scripts/lb_rebalance.py --market hk --execute }

Write-Host "=== 6/7 生成券商日度对照报告 ==="
Run-Step "report" { python scripts/lb_daily_report.py }

Write-Host "=== 7/7 运行后自检 ==="
Run-Step "post_check" { python scripts/post_daily_check.py }

Write-Host "=== 完成：查看 reports/ 与 logs/lb_order_map_result.json ==="

if ($script:failures -gt 0) { Write-Host "[RESULT] $script:failures 步失败"; exit 1 }
Write-Host "[RESULT] 全部步骤成功"
