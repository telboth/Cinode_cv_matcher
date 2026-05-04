param(
    [int]$Tail = 5
)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunDir = Join-Path $RootDir ".run"
$PidFile = Join-Path $RunDir "processes.json"

$ApiUrl = "http://127.0.0.1:8000/health"
$WebUrl = "http://127.0.0.1:5173"

function Write-Section([string]$Title) {
    Write-Host ""
    Write-Host "=== $Title ==="
}

function Get-ProcessStatus([int]$ProcessId) {
    if ($ProcessId -le 0) { return "not-configured" }
    $p = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $p) { return "not-running" }
    return "running"
}

function Get-PortStatus([int]$Port) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $conn) {
        return [pscustomobject]@{ Listening = $false; Pid = $null }
    }
    return [pscustomobject]@{ Listening = $true; Pid = $conn.OwningProcess }
}

function Test-Url([string]$Url) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
        return "ok ($($response.StatusCode))"
    }
    catch {
        return "fail ($($_.Exception.Message))"
    }
}

$state = $null
if (Test-Path $PidFile) {
    try {
        $state = Get-Content -Raw $PidFile | ConvertFrom-Json
    }
    catch {
        $state = $null
    }
}

$apiPid = if ($state -and $state.api -and $state.api.pid) { [int]$state.api.pid } else { 0 }
$webPid = if ($state -and $state.web -and $state.web.pid) { [int]$state.web.pid } else { 0 }
$apiListenerPidState = if ($state -and $state.api -and $state.api.listener_pid) { [int]$state.api.listener_pid } else { 0 }
$webListenerPidState = if ($state -and $state.web -and $state.web.listener_pid) { [int]$state.web.listener_pid } else { 0 }

$apiProcStatus = Get-ProcessStatus -ProcessId $apiPid
$webProcStatus = Get-ProcessStatus -ProcessId $webPid

$apiPort = Get-PortStatus -Port 8000
$webPort = Get-PortStatus -Port 5173

$apiHttp = Test-Url -Url $ApiUrl
$webHttp = Test-Url -Url $WebUrl

Write-Section "Services"
Write-Host ("API  : pid={0} process={1} port-listen={2} port-pid={3} http={4}" -f ($apiPid), $apiProcStatus, $apiPort.Listening, $apiPort.Pid, $apiHttp)
Write-Host ("WEB  : pid={0} process={1} port-listen={2} port-pid={3} http={4}" -f ($webPid), $webProcStatus, $webPort.Listening, $webPort.Pid, $webHttp)
if ($apiListenerPidState -gt 0 -or $webListenerPidState -gt 0) {
    Write-Host ("state.listener_pid: api={0} web={1}" -f $apiListenerPidState, $webListenerPidState)
}
if ($apiPort.Pid -and $apiPid -and ($apiPort.Pid -ne $apiPid)) {
    $apiListenerProc = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f [int]$apiPort.Pid) -ErrorAction SilentlyContinue
    if ($apiListenerProc -and ([int]$apiListenerProc.ParentProcessId -eq $apiPid)) {
        Write-Host "API note: pid er supervisor, port-pid er child worker (forventet)."
    } else {
        Write-Host "API note: pid og port-pid avviker (ikke parent/child). Kjør .\\stop-local.ps1 og start på nytt."
    }
}

Write-Section "Links"
Write-Host "API docs : http://127.0.0.1:8000/docs"
Write-Host "API health: $ApiUrl"
Write-Host "Web app  : $WebUrl"

Write-Section "Logs"
$apiOut = Join-Path $RunDir "api.out.log"
$apiErr = Join-Path $RunDir "api.err.log"
$webOut = Join-Path $RunDir "web.out.log"
$webErr = Join-Path $RunDir "web.err.log"

foreach ($log in @($apiOut, $apiErr, $webOut, $webErr)) {
    if (Test-Path $log) {
        $size = (Get-Item $log).Length
        Write-Host ("{0} (size={1} bytes)" -f $log, $size)
    }
    else {
        Write-Host ("{0} (missing)" -f $log)
    }
}

Write-Section "Log Tail"
foreach ($entry in @(
    @{ Name = "api.err"; Path = $apiErr },
    @{ Name = "web.err"; Path = $webErr }
)) {
    Write-Host "-- $($entry.Name) --"
    if (Test-Path $entry.Path) {
        Get-Content -Path $entry.Path -Tail $Tail
    }
    else {
        Write-Host "(missing)"
    }
}

Write-Section "State"
if ($state) {
    $state | ConvertTo-Json -Depth 5
}
else {
    Write-Host "No valid PID state found at $PidFile"
}

