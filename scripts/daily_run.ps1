# 每日收盘后自动化流程（本地 + 券商模拟盘）
# 用法: powershell -ExecutionPolicy Bypass -File scripts\daily_run.ps1
$ErrorActionPreference = "Continue"
Set-Location "D:\3314\quant-research"
$env:PYTHONIOENCODING = "utf-8"

Write-Host "=== 1/5 更新 A 股行情（腾讯，失败则跳过）==="
python scripts/fetch_tx.py --workers 8

Write-Host "=== 2/5 BaoStock 备用增量补齐 ==="
python scripts/fetch_baostock_fallback.py

Write-Host "=== 3/5 本地 A 股模拟盘推进 ==="
python scripts/run_paper.py

Write-Host "=== 4/5 盘中：用实时报价执行本地挂单 ==="
python scripts/paper_live.py

Write-Host "=== 5/5 券商模拟盘：美港股自动下单（幂等）==="
python scripts/lb_order_map.py --skip-existing --only both

Write-Host "=== 6/6 生成券商日度对照报告 ==="
python scripts/lb_daily_report.py

Write-Host "=== 完成：查看 reports/ 与 logs/lb_order_map_result.json ==="
