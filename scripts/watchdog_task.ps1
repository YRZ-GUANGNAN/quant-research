# 看门狗任务包装：给 Windows 调度器用（独立于 daily_run，主流程没跑也能发现）
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\watchdog_task.ps1
$ErrorActionPreference = "Continue"
Set-Location "D:\3314\quant-research"
$env:PYTHONIOENCODING = "utf-8"

$logDir = Join-Path (Get-Location) "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("watchdog_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")

python scripts/automation_watchdog.py *>&1 | Tee-Object -FilePath $log
$code = $LASTEXITCODE

Get-ChildItem $logDir -Filter "watchdog_*.log" | Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 20 | Remove-Item -Force -ErrorAction SilentlyContinue

if ($code -eq 1) { Write-Host "[RESULT] 看门狗发现异常，见 state/ALERT.json"; exit 1 }
if ($code -eq 2) { Write-Host "[RESULT] 非工作日，跳过"; exit 0 }
Write-Host "[RESULT] 自动化正常"
exit 0
