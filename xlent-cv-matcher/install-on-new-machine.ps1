param(
    [switch]$StartAfterInstall,
    [switch]$NoWait,
    [switch]$SkipBuildCheck
)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ApiDir = Join-Path $RootDir "apps\api"
$WebDir = Join-Path $RootDir "apps\web"
$EnvFile = Join-Path $RootDir ".env"
$EnvExample = Join-Path $RootDir ".env.example"

function Write-Step([string]$Message) {
    Write-Host "[install] $Message"
}

function Resolve-Executable([string[]]$Names) {
    foreach ($name in $Names) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            return $cmd.Source
        }
    }
    return $null
}

function Invoke-Native(
    [string]$Exe,
    [string[]]$Args,
    [string]$WorkingDir = ""
) {
    $allArgs = @($Args)
    if ($WorkingDir) {
        Push-Location $WorkingDir
    }
    try {
        & $Exe @allArgs
        $exitCode = $LASTEXITCODE
        if ($null -ne $exitCode -and $exitCode -ne 0) {
            throw "Command failed with exit code $exitCode: $Exe $($allArgs -join ' ')"
        }
    }
    finally {
        if ($WorkingDir) {
            Pop-Location
        }
    }
}

if (!(Test-Path $ApiDir)) {
    throw "API directory not found: $ApiDir"
}
if (!(Test-Path $WebDir)) {
    throw "Web directory not found: $WebDir"
}

Write-Step "Checking prerequisites (Python, Node, npm)"
$pythonExe = Resolve-Executable @("python")
$pythonPrefixArgs = @()
if (-not $pythonExe) {
    $pyLauncher = Resolve-Executable @("py")
    if ($pyLauncher) {
        $pythonExe = $pyLauncher
        $pythonPrefixArgs = @("-3")
    }
}
if (-not $pythonExe) {
    throw "Python was not found. Install Python 3.11+ first."
}

$nodeExe = Resolve-Executable @("node")
if (-not $nodeExe) {
    throw "Node.js was not found. Install Node.js LTS first."
}

$npmExe = Resolve-Executable @("npm.cmd", "npm")
if (-not $npmExe) {
    throw "npm was not found. Install Node.js LTS first."
}

Write-Step "Python: $pythonExe"
Write-Step "Node: $nodeExe"
Write-Step "npm: $npmExe"

if (!(Test-Path $EnvFile)) {
    if (Test-Path $EnvExample) {
        Copy-Item $EnvExample $EnvFile -Force
        Write-Warning "Created .env from .env.example. Fill in required keys before production use."
    }
    else {
        New-Item -ItemType File -Path $EnvFile -Force | Out-Null
        Write-Warning "Created empty .env. Fill in required keys before production use."
    }
}
else {
    Write-Step ".env already exists, keeping current values."
}

$ApiVenvPython = Join-Path $ApiDir ".venv\Scripts\python.exe"
if (!(Test-Path $ApiVenvPython)) {
    Write-Step "Creating API virtual environment"
    $venvPath = Join-Path $ApiDir ".venv"
    $args = @()
    $args += $pythonPrefixArgs
    $args += @("-m", "venv", $venvPath)
    Invoke-Native -Exe $pythonExe -Args $args -WorkingDir $ApiDir
}

Write-Step "Installing API dependencies"
Invoke-Native -Exe $ApiVenvPython -Args @("-m", "pip", "install", "--upgrade", "pip") -WorkingDir $ApiDir
Invoke-Native -Exe $ApiVenvPython -Args @("-m", "pip", "install", "-e", ".") -WorkingDir $ApiDir

Write-Step "Ensuring Playwright Python package is installed"
try {
    Invoke-Native -Exe $ApiVenvPython -Args @("-c", "import playwright") -WorkingDir $ApiDir
}
catch {
    Invoke-Native -Exe $ApiVenvPython -Args @("-m", "pip", "install", "playwright") -WorkingDir $ApiDir
}

$PlaywrightInstallMarker = Join-Path $ApiDir ".venv\.playwright_chromium_installed"
if (!(Test-Path $PlaywrightInstallMarker)) {
    Write-Step "Installing Playwright Chromium browser"
    Invoke-Native -Exe $ApiVenvPython -Args @("-m", "playwright", "install", "chromium") -WorkingDir $ApiDir
    New-Item -ItemType File -Path $PlaywrightInstallMarker -Force | Out-Null
}
else {
    Write-Step "Playwright Chromium already installed (marker found)."
}

Write-Step "Installing web dependencies"
$lockFile = Join-Path $WebDir "package-lock.json"
if (Test-Path $lockFile) {
    Invoke-Native -Exe $npmExe -Args @("ci", "--no-audit", "--no-fund") -WorkingDir $WebDir
}
else {
    Invoke-Native -Exe $npmExe -Args @("install", "--no-audit", "--no-fund") -WorkingDir $WebDir
}

if (-not $SkipBuildCheck) {
    Write-Step "Running web build check"
    Invoke-Native -Exe $npmExe -Args @("run", "build") -WorkingDir $WebDir
}

Write-Step "Installation completed successfully."
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1) Review .env and set required keys (OpenAI/Cinode)."
Write-Host "  2) Start app with: .\start-local.ps1"
Write-Host "  3) Check status with: .\status-local.ps1"
Write-Host ""

if ($StartAfterInstall) {
    $startScript = Join-Path $RootDir "start-local.ps1"
    if (!(Test-Path $startScript)) {
        throw "start-local.ps1 not found at: $startScript"
    }
    Write-Step "Starting local services"
    if ($NoWait) {
        & $startScript -NoWait
    }
    else {
        & $startScript
    }
}
