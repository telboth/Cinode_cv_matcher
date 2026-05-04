param(
    [switch]$SkipInstall,
    [switch]$NoWait
)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ApiDir = Join-Path $RootDir "apps\api"
$WebDir = Join-Path $RootDir "apps\web"
$RunDir = Join-Path $RootDir ".run"
$PidFile = Join-Path $RunDir "processes.json"

$ApiPort = 8000
$WebPort = 5173

function Write-Step([string]$Message) {
    Write-Host "[start] $Message"
}

function Wait-ForListenPort([int]$Port, [int]$TimeoutSec = 30) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        if ($conn) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Get-ListenerPid([int]$Port) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $conn) { return $null }
    return [int]$conn.OwningProcess
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
        Stop-Process -Id $childPid -Force -ErrorAction SilentlyContinue
    }
    Stop-Process -Id $RootPid -Force -ErrorAction SilentlyContinue
}

function Stop-PortListeners([int]$Port) {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($conn in $conns) {
        if ($conn.OwningProcess -gt 0) {
            Write-Step "Freeing port $Port (stopping PID $($conn.OwningProcess))"
            Stop-ProcessTree -RootPid ([int]$conn.OwningProcess)
        }
    }
}

function Resolve-NpmPath {
    $cmd = Get-Command npm -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $cmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidates = @(
        (Join-Path $env:ProgramFiles "nodejs\npm.cmd"),
        (Join-Path $env:ProgramFiles "nodejs\npm"),
        (Join-Path $env:APPDATA "npm\npm.cmd")
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }

    return $null
}

function Resolve-NodePath([string]$NpmPath) {
    if ($NpmPath) {
        $npmDir = Split-Path -Parent $NpmPath
        $nodeFromNpmDir = Join-Path $npmDir "node.exe"
        if (Test-Path $nodeFromNpmDir) {
            return $nodeFromNpmDir
        }
    }

    $candidates = @(
        (Join-Path $env:ProgramFiles "nodejs\node.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\nodejs\node.exe")
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }

    $cmd = Get-Command node -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    return $null
}

if (!(Test-Path $ApiDir)) {
    throw "API directory not found: $ApiDir"
}
if (!(Test-Path $WebDir)) {
    throw "Web directory not found: $WebDir"
}

New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

$StopScript = Join-Path $RootDir "stop-local.ps1"
if (Test-Path $StopScript) {
    Write-Step "Stopping previously managed processes (if any)"
    & $StopScript -Quiet
}

# Safety-net: free expected service ports in case an unmanaged/stale process is still running.
Stop-PortListeners -Port $ApiPort
Stop-PortListeners -Port $WebPort

$pythonCmd = Get-Command python -ErrorAction Stop
$npmPath = Resolve-NpmPath
$nodePath = Resolve-NodePath -NpmPath $npmPath

if ($nodePath) {
    $nodeDir = Split-Path -Parent $nodePath
    if ($env:Path -notlike "*$nodeDir*") {
        $env:Path = "$nodeDir;$env:Path"
    }
}

$hasNpm = $null -ne $npmPath
$hasNode = $null -ne $nodePath

if (-not $hasNpm) {
    Write-Warning "npm was not found. Web dependency install may fail."
}
if (-not $hasNode) {
    Write-Warning "node was not found. Web cannot start."
}

$ApiVenvPython = Join-Path $ApiDir ".venv\Scripts\python.exe"
$ApiInstallMarker = Join-Path $ApiDir ".venv\.deps_installed"
$PlaywrightInstallMarker = Join-Path $ApiDir ".venv\.playwright_chromium_installed"

if (!(Test-Path $ApiVenvPython)) {
    Write-Step "Creating API virtual environment"
    & $pythonCmd.Source -m venv (Join-Path $ApiDir ".venv")
}

if (!(Test-Path $ApiInstallMarker)) {
    if ($SkipInstall) {
        Write-Step "Dependencies are missing for API; installing once even with -SkipInstall"
    } else {
        Write-Step "Installing API dependencies"
    }
    Push-Location $ApiDir
    try {
        & $ApiVenvPython -m pip install --upgrade pip
        & $ApiVenvPython -m pip install -e .
    }
    finally {
        Pop-Location
    }
    New-Item -ItemType File -Path $ApiInstallMarker -Force | Out-Null
} elseif (!$SkipInstall) {
    Write-Step "Installing API dependencies"
    Push-Location $ApiDir
    try {
        & $ApiVenvPython -m pip install --upgrade pip
        & $ApiVenvPython -m pip install -e .
    }
    finally {
        Pop-Location
    }
}

$playwrightImportOk = $false
try {
    & $ApiVenvPython -c "import playwright" | Out-Null
    $playwrightImportOk = $true
}
catch {
    $playwrightImportOk = $false
}

if (-not $playwrightImportOk) {
    Write-Step "Installing Playwright Python package"
    Push-Location $ApiDir
    try {
        & $ApiVenvPython -m pip install playwright
    }
    finally {
        Pop-Location
    }
}

if (!(Test-Path $PlaywrightInstallMarker)) {
    Write-Step "Installing Playwright Chromium browser"
    Push-Location $ApiDir
    try {
        & $ApiVenvPython -m playwright install chromium
    }
    finally {
        Pop-Location
    }
    New-Item -ItemType File -Path $PlaywrightInstallMarker -Force | Out-Null
}

$WebNodeModules = Join-Path $WebDir "node_modules"
$WebViteJs = Join-Path $WebDir "node_modules\vite\bin\vite.js"
$canStartWeb = $hasNode

if ($hasNpm -and (!(Test-Path $WebNodeModules) -or !(Test-Path $WebViteJs))) {
    if ($SkipInstall) {
        Write-Warning "Web dependencies are missing and -SkipInstall was used. Web will not start."
        $canStartWeb = $false
    } else {
        Write-Step "Installing web dependencies"
        Push-Location $WebDir
        try {
            try {
                & $npmPath install --no-audit --no-fund
            }
            catch {
                Write-Warning "Web dependency install failed: $($_.Exception.Message)"
                Write-Warning "Likely Windows/OneDrive file permission issue (esbuild)."
                $canStartWeb = $false
            }
        }
        finally {
            Pop-Location
        }
    }
}

if (!(Test-Path $WebViteJs)) {
    Write-Warning "Vite runtime not found at $WebViteJs. Web will not start."
    $canStartWeb = $false
}

$ApiOutLog = Join-Path $RunDir "api.out.log"
$ApiErrLog = Join-Path $RunDir "api.err.log"
$WebOutLog = Join-Path $RunDir "web.out.log"
$WebErrLog = Join-Path $RunDir "web.err.log"

foreach ($logFile in @($ApiOutLog, $ApiErrLog, $WebOutLog, $WebErrLog)) {
    if (Test-Path $logFile) {
        Remove-Item -LiteralPath $logFile -Force -ErrorAction SilentlyContinue
    }
}

if ($nodePath) {
    Write-Step "Using node: $nodePath"
}
if ($npmPath) {
    Write-Step "Using npm: $npmPath"
}

Write-Step "Starting API on http://127.0.0.1:$ApiPort"
$apiProcess = Start-Process `
    -FilePath $ApiVenvPython `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$ApiPort" `
    -WorkingDirectory $ApiDir `
    -WindowStyle Hidden `
    -PassThru `
    -RedirectStandardOutput $ApiOutLog `
    -RedirectStandardError $ApiErrLog

if ($canStartWeb) {
    Write-Step "Starting web on http://127.0.0.1:$WebPort"
    $webProcess = Start-Process `
        -FilePath $nodePath `
        -ArgumentList "`"$WebViteJs`"", "--host", "127.0.0.1", "--port", "$WebPort" `
        -WorkingDirectory $WebDir `
        -WindowStyle Hidden `
        -PassThru `
        -RedirectStandardOutput $WebOutLog `
        -RedirectStandardError $WebErrLog
}

if ($NoWait) {
    $apiReady = $true
    $webReady = if ($canStartWeb) { $true } else { $false }
} else {
    $apiReady = Wait-ForListenPort -Port $ApiPort -TimeoutSec 35
    $webReady = if ($canStartWeb) { Wait-ForListenPort -Port $WebPort -TimeoutSec 35 } else { $false }
}

$state = [ordered]@{
    started_at = (Get-Date).ToString("o")
    root_dir = $RootDir
    api = [ordered]@{
        pid = $apiProcess.Id
        port = $ApiPort
        out_log = $ApiOutLog
        err_log = $ApiErrLog
    }
    web = [ordered]@{
        pid = if ($canStartWeb -and $webProcess) { $webProcess.Id } else { $null }
        port = $WebPort
        out_log = $WebOutLog
        err_log = $WebErrLog
    }
}

# If a service uses a child worker process (common on Windows), store the listener PID.
$apiListenerPid = Get-ListenerPid -Port $ApiPort
$webListenerPid = if ($canStartWeb) { Get-ListenerPid -Port $WebPort } else { $null }
if ($apiListenerPid) {
    $state.api.listener_pid = $apiListenerPid
}
if ($webListenerPid) {
    $state.web.listener_pid = $webListenerPid
}

$state | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 $PidFile

if ((-not $NoWait) -and (-not $apiReady)) {
    Write-Warning "API failed to start. Check logs in $RunDir"
    if (Test-Path $StopScript) {
        & $StopScript -Quiet
    }
    exit 1
}

if ((-not $NoWait) -and $canStartWeb -and -not $webReady) {
    Write-Warning "Web failed to start. API is running. Check logs in $RunDir"
}

Write-Step "Started"
Write-Host "API: http://127.0.0.1:$ApiPort"
if ($canStartWeb -and $webReady) {
    Write-Host "Web: http://127.0.0.1:$WebPort"
} elseif ($canStartWeb) {
    Write-Host "Web: failed to start (see .run\\web.err.log)"
} else {
    Write-Host "Web: not started"
}
Write-Host "Logs: $RunDir"
if ($NoWait) {
    Write-Host "NoWait: startup checks skipped. Run .\\status-local.ps1 for live status."
    return
}

