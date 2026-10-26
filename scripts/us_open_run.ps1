# 美股开盘后任务（工作日 21:35）
# 用法: powershell -ExecutionPolicy Bypass -File scripts\us_open_run.ps1
$ErrorActionPreference = "Continue"
Set-Location "D:\3314\quant-research"
$env:PYTHONIOENCODING = "utf-8"
$env:LB_ACCOUNT_KIND = "paper"

Write-Host "=== 1/4 更新美股行情（Yahoo）==="
python scripts/fetch_market.py --market us

Write-Host "=== 2/4 数据门禁（美股）==="
python scripts/pre_trade_gate.py --markets us
if ($LASTEXITCODE -ne 0) { Write-Host "[ABORT] 门禁未通过"; exit 1 }

Write-Host "=== 3/4 美股下单 + 再平衡 ==="
python scripts/lb_order_map.py --skip-existing --only us --execute
python scripts/lb_rebalance.py --market us --execute

Write-Host "=== 4/4 更新日度报告 ==="
python scripts/lb_daily_report.py
