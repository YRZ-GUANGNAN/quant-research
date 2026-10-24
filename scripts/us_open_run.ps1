# 美股开盘后任务（工作日 21:35）：补美股袖子 + 更新报告
$ErrorActionPreference = "Continue"
Set-Location "D:\3314\quant-research"
$env:PYTHONIOENCODING = "utf-8"
$env:LB_ACCOUNT_KIND = "paper"  # 代码级护栏：仅允许模拟账户下单
Write-Host "=== 更新美股行情（Yahoo）==="
python scripts/fetch_market.py --market us

Write-Host "=== 数据门禁（美股）==="
python scripts/pre_trade_gate.py --markets us
if ($LASTEXITCODE -ne 0) { Write-Host "[ABORT] 数据门禁未通过，禁止下单"; exit 1 }

Write-Host "=== 美股开盘：US 袖子下单（幂等）==="
python scripts/lb_order_map.py --skip-existing --only us --execute
python scripts/lb_rebalance.py --market us --execute
Write-Host "=== 更新日度报告 ==="
python scripts/lb_daily_report.py
