# 美股开盘后任务（工作日 21:35）：补美股袖子 + 更新报告
$ErrorActionPreference = "Continue"
Set-Location "D:\3314\quant-research"
$env:PYTHONIOENCODING = "utf-8"
Write-Host "=== 美股开盘：US 袖子下单（幂等）==="
python scripts/lb_order_map.py --skip-existing --only us --execute
python scripts/lb_rebalance.py --market us --execute
Write-Host "=== 更新日度报告 ==="
python scripts/lb_daily_report.py
