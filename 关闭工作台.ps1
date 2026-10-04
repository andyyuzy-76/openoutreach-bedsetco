$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskPidPath = Join-Path $taskRoot 'data\workbench.pid'
if (-not (Test-Path -LiteralPath $taskPidPath)) { Write-Host '工作台未记录运行进程。'; exit }
$taskProcessId = [int](Get-Content -LiteralPath $taskPidPath -Raw)
$taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$taskProcessId"
$taskAppPath = Join-Path $taskRoot 'bedsetco.py'
if ($taskProcess -and $taskProcess.CommandLine.Contains($taskAppPath)) {
    Stop-Process -Id $taskProcessId
    Write-Host '本地工作台已关闭。'
} else { Write-Host '未发现对应工作台进程，未停止其他程序。' }
