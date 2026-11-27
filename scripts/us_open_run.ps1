param(
    [switch]$NoExecute   # 冒烟测试用：强制关闭真实下单
)

# 美股开盘任务（工作日 21:35 触发，脚本自己等到真实开盘）：门禁 → 下单 → 风控 → 对账 → 报告
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\us_open_run.ps1
$ErrorActionPreference = "Continue"

Set-Location "D:\3314\quant-research"
$env:PYTHONIOENCODING = "utf-8"
$env:LB_ACCOUNT_KIND = "paper"
$env:LB_ALLOW_EXECUTION = "1"
# 风险动作真实执行（仅模拟盘）：止损/替换会真的发单，幂等台账防重复
if ($env:LB_ACCOUNT_KIND -eq "paper") { $env:RISK_ALLOW_EXECUTION = "1" }
if ($NoExecute) { $env:RISK_ALLOW_EXECUTION = "0" }
if ($NoExecute) { $env:LB_ALLOW_EXECUTION = "0" }    # 受控执行通道（policy + ledger 保护）

$logDir = Join-Path (Get-Location) "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logPath = Join-Path $logDir ("us_open_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
try { Start-Transcript -Path $logPath -Append | Out-Null } catch { Write-Host "[warn] 日志转录开启失败: $($_.Exception.Message)" }
Get-ChildItem $logDir -Filter "us_open_*.log" | Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 30 | Remove-Item -Force -ErrorAction SilentlyContinue

# ---------- 运行锁：与日更共用一把锁，避免 21:35 与 22:30 两路同时下单 ----------
$lockPath = Join-Path (Get-Location) "state\daily_run.lock"
New-Item -ItemType Directory -Force -Path (Split-Path $lockPath) | Out-Null
if (Test-Path $lockPath) {
    $otherPid = 0
    try { $otherPid = [int]((Get-Content $lockPath -ErrorAction Stop | Select-Object -First 1)) } catch { $otherPid = 0 }
    if ($otherPid -gt 0 -and [bool](Get-Process -Id $otherPid -ErrorAction SilentlyContinue)) {
        Write-Host "[skip] 已有流程在跑（PID $otherPid），本次不并发执行"
        exit 3
    }
}
Set-Content -Path $lockPath -Value $PID -Encoding ascii

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

Write-Host "=== 1/8 更新美股行情（Yahoo）==="
Run-Step "fetch_us" { python scripts/fetch_market.py --market us }

Write-Host "=== 2/8 数据门禁（美股）==="
python scripts/pre_trade_gate.py --markets us
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ABORT] 门禁未通过，今晚不下单"
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}

Write-Host "=== 3/8 等待美股进入交易时段（自动跨夏令时）==="
python scripts/wait_for_session.py --market US --max-wait 4800
if ($LASTEXITCODE -ne 0) {
    Write-Host "[skip] 美股未开盘或等待超时，跳过下单（次日补跑靠 snapshot 周期内幂等）"
}

Write-Host "=== 4/8 美股下单 + 再平衡（35% 现金份额）==="
Run-Step "exec_us" { python -m quant.automation.execution --market us --execute --cash-share 0.35 }

Write-Host "=== 4b/8 A 股代理袖套（ASHR.US，替代无法交易的沪深港通）==="
Run-Step "exec_cn_proxy" { python -m quant.automation.execution --market cn_proxy --execute --cash-share 0.65 }

Write-Host "=== 5/8 风险识别 → 动作（默认 dry-run，开启 RISK_ALLOW_EXECUTION 才真实执行）==="
Run-Step "risk_act_us" { python scripts/risk_act.py --market us }
if ($env:RISK_ALLOW_EXECUTION -eq "1") {
    Write-Host "[warn] 风控动作真实执行已开启（美股）"
    Run-Step "risk_act_us_exec" { python scripts/risk_act.py --market us --execute }
}

Write-Host "=== 6/8 执行对账（期望 vs 实际）==="
Run-Step "reconcile_us" { python scripts/reconcile_today.py --market us }

Write-Host "=== 7/8 模型健康 + 业绩归因 ==="
python scripts/health_check.py
if ($LASTEXITCODE -ne 0) { Write-Host "[warn] 模型健康降级（不阻断，降仓由 health 闭环处理）" }
Run-Step "attribution_us" { python scripts/attribution.py }

Write-Host "=== 8/8 日度报告 + 动作追踪 + 绩效面板 ==="
Run-Step "report_us" { python scripts/lb_daily_report.py }
Run-Step "track_actions_us" { python scripts/track_actions.py }
Run-Step "dashboard_us" { python scripts/dashboard.py }

Remove-Item $lockPath -Force -ErrorAction SilentlyContinue
if ($script:failures -gt 0) {
    Write-Host ("[RESULT] {0} 步失败: {1}" -f $script:failures, ($script:failedSteps -join ", "))
    Write-Host "[LOG] $logPath"
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}
Write-Host "[RESULT] 美股开盘流程完成"
Write-Host "[LOG] $logPath"
Remove-Item $lockPath -Force -ErrorAction SilentlyContinue
try { Stop-Transcript | Out-Null } catch { }
exit 0
