param(
    [switch]$NoExecute   # 冒烟测试用：强制关闭真实下单
)

# A 股开盘任务（工作日 09:35）：数据门禁 → 通道体检 → 真实再平衡（受限额保护）
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\cn_open_run.ps1
$ErrorActionPreference = "Continue"

Set-Location "D:\3314\quant-research"
# 临时目录指到工作区：C 盘曾只剩 0.03GB，python 临时文件会把任务打挂
$tmpDir = Join-Path (Get-Location) "tmp"
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
$env:TEMP = $tmpDir
$env:TMP = $tmpDir

$env:PYTHONIOENCODING = "utf-8"
$env:LB_ACCOUNT_KIND = "paper"
$env:CN_ALLOW_EXECUTION = "1"
# 模拟阶段（先模拟稳定再实钱）：账户总资产超过 30 万就不许下单，避免误把真实大账户当模拟跑
$env:CN_STAGE = "sim"
$env:CN_MAX_EQUITY = "300000"
# 模拟账户没有防拷贝风控，允许用剪贴板读持仓/委托；真实账户请保持关闭
$env:CN_ALLOW_CLIPBOARD = "1"
if ($NoExecute) { $env:CN_ALLOW_EXECUTION = "0" }      # 真实下单总开关（另有单笔/单日/单票/整手/现金多重限额）
# $env:RISK_ALLOW_EXECUTION = "1"  # 风控动作走 Longbridge，A 股不适用；A 股靠目标再平衡做减仓/清仓

$logDir = Join-Path (Get-Location) "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logPath = Join-Path $logDir ("cn_open_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
try { Start-Transcript -Path $logPath -Append | Out-Null } catch { Write-Host "[warn] 日志转录开启失败: $($_.Exception.Message)" }
Get-ChildItem $logDir -Filter "cn_open_*.log" | Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 30 | Remove-Item -Force -ErrorAction SilentlyContinue

$script:failures = 0
$script:failedSteps = @()
function Run-Step([string]$desc, [scriptblock]$cmd) {
    $t0 = Get-Date
    & $cmd
    $code = $LASTEXITCODE
    $ms = [int]((Get-Date) - $t0).TotalMilliseconds
    if ($code -eq 3) {
        Write-Host ("[{0}] [skip] {1}（预期内跳过：非交易时段/通道未就绪/已提交）" -f (Get-Date -Format "HH:mm:ss"), $desc)
        return
    }
    if ($code -ne 0) {
        $script:failures++
        $script:failedSteps += $desc
        Write-Host ("[{0}] [FAIL] {1} (exit {2}, {3}ms)" -f (Get-Date -Format "HH:mm:ss"), $desc, $code, $ms)
    } else {
        Write-Host ("[{0}] [ok] {1} ({2}ms)" -f (Get-Date -Format "HH:mm:ss"), $desc, $ms)
    }
}

Write-Host "=== 1/7 A 股数据门禁 ==="
python scripts/pre_trade_gate.py --markets cn
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ABORT] A 股数据未就绪，今日不交易"
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}

Write-Host "=== 2/7 A 股交易通道状态 ==="
Run-Step "channel" { python -m quant.broker.cn_broker --status }

Write-Host "=== 3/7 实盘资金与风险体检 ==="
Run-Step "cn_risk" { python scripts/cn_risk_report.py }

Write-Host "=== 3b/7 风控动作（本地计算，不下单；A 股减仓由目标再平衡完成）==="
python scripts/risk_act.py --market cn

Write-Host "=== 4/7 A 股本地引擎推进（信号侧）==="
Run-Step "run_paper" { python scripts/run_paper.py }

Write-Host "=== 5/7 生成实盘目标权重（模型共识前 N 只，小账户版）==="
Run-Step "build_target" { python scripts/build_cn_target_live.py --top 6 --exposure 0.70 }

Write-Host "=== 6/7 对照真实账户算单（dry-run，不下单）==="
Run-Step "cn_preview" { python -m quant.broker.cn_broker --target state/cn_target_live.json }

Write-Host "=== 7/7 真实再平衡（先卖后买，限额+T+1+幂等保护）==="
if ($env:CN_ALLOW_EXECUTION -eq "1") {
    Write-Host "[warn] A 股真实下单已开启（单笔<=10万，单日<=50万，单票<=30%，现金留底 3%）"
    Run-Step "cn_execute" { python -m quant.broker.cn_broker --target state/cn_target_live.json --execute }
} else {
    Write-Host "[skip] CN_ALLOW_EXECUTION 未开，今日只做体检与信号"
}

if ($script:failures -gt 0) {
    Write-Host ("[RESULT] {0} 步失败: {1}" -f $script:failures, ($script:failedSteps -join ", "))
    Write-Host "[LOG] $logPath"
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}
Write-Host "[RESULT] A 股开盘流程完成"
Write-Host "[LOG] $logPath"
try { Stop-Transcript | Out-Null } catch { }
exit 0
