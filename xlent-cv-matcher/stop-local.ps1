param(
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunDir = Join-Path $RootDir ".run"
$PidFile = Join-Path $RunDir "processes.json"

function Write-Step([string]$Message) {
    if (-not $Quiet) {
        Write-Host "[stop] $Message"
    }
}

function Stop-IfExists([int]$ProcessId) {
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -ne $proc) {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        Write-Step "Stopped PID $ProcessId"
    }
}

function Get-DescendantProcessIds([int]$RootPid) {
    $all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
    if (-not $all) { return @() }
    $childrenByParent = @{}
    foreach ($p in $all) {
        $ppid = [int]$p.ParentProcessId
        if (-not $childrenByParent.ContainsKey($ppid)) {
            $childrenByParent[$ppid] = New-Object System.Collections.Generic.List[int]
        }
        $childrenByParent[$ppid].Add([int]$p.ProcessId)
    }
    $result = New-Object System.Collections.Generic.List[int]
    $stack = New-Object System.Collections.Generic.Stack[int]
    $stack.Push($RootPid)
    while ($stack.Count -gt 0) {
        $current = $stack.Pop()
        if ($childrenByParent.ContainsKey($current)) {
            foreach ($child in $childrenByParent[$current]) {
                if (-not $result.Contains($child)) {
                    $result.Add($child)
                    $stack.Push($child)
                }
            }
        }
    }
    return $result.ToArray()
}

function Stop-ProcessTree([int]$RootPid) {
    if ($RootPid -le 0) { return }
    $desc = Get-DescendantProcessIds -RootPid $RootPid
    foreach ($childPid in ($desc | Sort-Object -Descending)) {
        Stop-IfExists -ProcessId $childPid
    }
    Stop-IfExists -ProcessId $RootPid
}

function Stop-WorkspaceServiceProcesses {
    $rootEsc = [regex]::Escape($RootDir)
    $candidates = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $cmd = $_.CommandLine
        if (-not $cmd) { return $false }
        ($cmd -match $rootEsc) -and (
            ($cmd -match "uvicorn\s+app\.main:app") -or
            ($cmd -match "node_modules\\vite\\bin\\vite\.js")
        )
    }
    foreach ($p in $candidates) {
        Stop-ProcessTree -RootPid ([int]$p.ProcessId)
    }
}

if (Test-Path $PidFile) {
    $state = $null
    try {
        $state = Get-Content -Raw $PidFile | ConvertFrom-Json
    }
    catch {
        Write-Step "Could not read PID file, continuing with port-based cleanup"
    }

    if ($state) {
        if ($state.api -and $state.api.pid) {
            Stop-ProcessTree -RootPid ([int]$state.api.pid)
        }
        if ($state.web -and $state.web.pid) {
            Stop-ProcessTree -RootPid ([int]$state.web.pid)
        }
    }

    if (Test-Path $PidFile) {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        Write-Step "Removed PID file"
    }
} else {
    Write-Step "No PID file found"
}

foreach ($port in @(8000, 5173)) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($conn in $conns) {
        if ($conn.OwningProcess -gt 0) {
            Stop-ProcessTree -RootPid $conn.OwningProcess
        }
    }
}

# Final workspace cleanup in case supervisors/children are detached from PID-file ownership.
Stop-WorkspaceServiceProcesses

Write-Step "Done"
