# 每日收盘后自动化流程（本地 + 券商模拟盘）
# 用法: powershell -ExecutionPolicy Bypass -File scripts\daily_run.ps1
$ErrorActionPreference = "Continue"
Set-Location "D:\3314\quant-research"
# 临时目录指到工作区：C 盘曾只剩 0.03GB，python 临时文件会把任务打挂
$tmpDir = Join-Path (Get-Location) "tmp"
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
$env:TEMP = $tmpDir
$env:TMP = $tmpDir

$env:PYTHONIOENCODING = "utf-8"
$env:LB_ACCOUNT_KIND = "paper"
$env:LB_ALLOW_EXECUTION = "1"  # 受控执行通道（policy + ledger 保护）
# 风险动作真实执行（仅模拟盘）：止损/替换会真的发单，幂等台账防重复
if ($env:LB_ACCOUNT_KIND -eq "paper") { $env:RISK_ALLOW_EXECUTION = "1" }
if ($NoExecute) { $env:RISK_ALLOW_EXECUTION = "0" }

$logDir = Join-Path (Get-Location) "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logPath = Join-Path $logDir "daily_run_$stamp.log"
try { Start-Transcript -Path $logPath -Append | Out-Null } catch { Write-Host "[warn] 日志转录开启失败: $($_.Exception.Message)" }
Get-ChildItem $logDir -Filter "daily_run_*.log" | Sort-Object LastWriteTime -Descending | Select-Object -Skip 30 | Remove-Item -Force -ErrorAction SilentlyContinue

# ---------- 运行锁：同一时间只允许一个日更流程（15:40/16:40/22:30 会重叠） ----------
$lockPath = Join-Path (Get-Location) "state\daily_run.lock"
New-Item -ItemType Directory -Force -Path (Split-Path $lockPath) | Out-Null
if (Test-Path $lockPath) {
    $otherPid = 0
    try { $otherPid = [int]((Get-Content $lockPath -ErrorAction Stop | Select-Object -First 1)) } catch { $otherPid = 0 }
    $alive = $false
    if ($otherPid -gt 0) { $alive = [bool](Get-Process -Id $otherPid -ErrorAction SilentlyContinue) }
    if ($alive) {
        Write-Host "[skip] 已有日更流程在跑（PID $otherPid），本次不再并发执行"
        exit 3
    }
    Write-Host "[warn] 发现过期锁（PID $otherPid 已退出），接管"
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

Write-Host "=== 0/9 开跑前自检（凭证/磁盘/通道）==="
python scripts/preflight.py
if ($LASTEXITCODE -ne 0) { Write-Host "[warn] 自检有硬阻断项，下单步骤预计会失败，见 reports/preflight.json" }

Write-Host "=== 0b/9 状态备份（丢不起的文件先存一份）==="
Run-Step "backup_state" { python scripts/backup_state.py --keep 14 }

Write-Host "=== 1/9 更新 A 股行情（腾讯；限流自动降并发重试）==="
python scripts/fetch_tx.py --workers 8
if ($LASTEXITCODE -ne 0) {
    Write-Host "[retry] 腾讯限流，60 秒后降并发重试（workers=2）"
    Start-Sleep -Seconds 60
    python scripts/fetch_tx.py --workers 2
}
if ($LASTEXITCODE -ne 0) { $script:failures++; $script:failedSteps += "fetch_tx"; Write-Host "[FAIL] fetch_tx 两次均失败，门禁将阻断下单" }

Write-Host "=== 2/9 东方财富兜底（补腾讯缺口）==="
Run-Step "fetch_eastmoney" { python scripts/fetch_eastmoney_fallback.py }

Write-Host "=== 2b/9 BaoStock 最后兜底 ==="
Run-Step "fetch_baostock" { python scripts/fetch_baostock_fallback.py }

Write-Host "=== 3/9 更新美股/港股行情（Yahoo）==="
Run-Step "fetch_us" { python scripts/fetch_market.py --market us }
Run-Step "fetch_hk" { python scripts/fetch_market.py --market hk }

Write-Host "=== 3b/9 数据门禁（不通过则禁止推进账户与下单）==="
# 只校验本流程会交易的市场（A股/港股）；美股由 21:35 的 us_open_run 单独门禁
python scripts/pre_trade_gate.py --markets cn,hk
if ($LASTEXITCODE -ne 0) { Write-Host "[ABORT] 数据门禁未通过：不推进账户、不下单"; exit 1 }

Write-Host "=== 3c/9 A 股交易通道状态（QMT / 同花顺 / 本地）==="
python -m quant.broker.cn_broker --status

Write-Host "=== 4/9 本地 A 股模拟盘推进 ==="
Run-Step "run_paper" { python scripts/run_paper.py }

Write-Host "=== 6/9 目标快照（周期内复用）==="
Run-Step "snapshot" { python scripts/build_target_snapshot.py }

Write-Host "=== 7b/9 下单前模拟（港股）==="
python scripts/simulate_plan.py --market hk
$simHkOk = ($LASTEXITCODE -eq 0)
if (-not $simHkOk) { Write-Host "[warn] 港股计划模拟暴露问题，本次跳过真实下单" }

Write-Host "=== 8/9 券商模拟盘：建仓 + 再平衡 ==="
Run-Step "paper_live" { python scripts/paper_live.py }


if ($simHkOk) { Run-Step "execute_hk" { python -m quant.automation.execution --market hk --execute } } else { Write-Host "[skip] execute_hk（模拟未通过）" }

Write-Host "=== 8b/9 风险识别 → 动作（默认 dry-run）==="
Run-Step "risk_act_hk" { python scripts/risk_act.py --market hk }
if ($env:RISK_ALLOW_EXECUTION -eq "1") {
    Write-Host "[warn] 风控动作真实执行已开启（港股）"
    Run-Step "risk_act_hk_exec" { python scripts/risk_act.py --market hk --execute }
}

Write-Host "=== 8b/9 第二轮补单（换手上限导致一轮做不完时）==="
if ($simHkOk) {
    python scripts/weight_drift.py --markets hk --strict | Out-Null
    if ($LASTEXITCODE -eq 1) {
        Write-Host "[warn] 仍有漂移，尝试当日第二轮补单（最多两轮）"
        Run-Step "execute_hk_pass2" { python -m quant.automation.execution --market hk --execute --pass 2 }
    }
}

Write-Host "=== 8c/9 执行对账（港股）==="
Run-Step "reconcile_hk" { python scripts/reconcile_today.py --market hk }
Run-Step "weight_drift" { python scripts/weight_drift.py --markets us,hk }
Run-Step "execution_quality" { python scripts/execution_quality.py --days 10 }

Write-Host "=== 8d/9 模型健康检查 ==="
python scripts/health_check.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "[warn] 模型健康降级：建议降低仓位或人工复核（不阻断本次流程）"
}

Write-Host "=== 8e/9 业绩归因 ==="
Run-Step "attribution" { python scripts/attribution.py }
Run-Step "equity_curve" { python scripts/equity_curve.py }
Run-Step "capital_check" { python scripts/capital_check.py }
Run-Step "track_actions" { python scripts/track_actions.py }

Write-Host "=== 9/9 日度报告 + 自检 ==="
Run-Step "report" { python scripts/lb_daily_report.py }
Run-Step "dashboard" { python scripts/dashboard.py }
Run-Step "post_check" { python scripts/post_daily_check.py }
Write-Host "=== 9a/9 晋级闸门（模拟 → 实钱 条件）==="
python scripts/promotion_check.py

Write-Host "=== 9b/9 自动化看门狗（缺产物/任务没跑会告警）==="
python scripts/automation_watchdog.py
if ($LASTEXITCODE -eq 1) { Write-Host "[warn] 看门狗报异常，见 reports/automation_health_*.json 与 state/ALERT.json" }

Remove-Item $lockPath -Force -ErrorAction SilentlyContinue
if ($script:failures -gt 0) {
    Write-Host ("[RESULT] {0} 步失败: {1}" -f $script:failures, ($script:failedSteps -join ", "))
    Write-Host "[LOG] $logPath"
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}
Write-Host "[RESULT] 全部步骤成功"
Write-Host "[LOG] $logPath"
Remove-Item $lockPath -Force -ErrorAction SilentlyContinue
try { Stop-Transcript | Out-Null } catch { }
exit 0
