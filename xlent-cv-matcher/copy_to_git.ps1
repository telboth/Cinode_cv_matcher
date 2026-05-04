param(
    [string]$CommitMessage = "",
    [switch]$NoPush,
    [switch]$IncludeRepoRootFiles
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) {
    Write-Host "[copy-to-git] $Message"
}

function Invoke-Git([string[]]$GitArgs, [switch]$AllowNonZeroExit) {
    & git @GitArgs
    $exitCode = $LASTEXITCODE
    if (-not $AllowNonZeroExit -and $exitCode -ne 0) {
        throw "Git command failed ($exitCode): git $($GitArgs -join ' ')"
    }
    return $exitCode
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $scriptDir
try {
    $repoRoot = (& git rev-parse --show-toplevel).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($repoRoot)) {
        throw "Fant ikke git-repo. Kjør scriptet fra en mappe som ligger i et git-repo."
    }
}
finally {
    Pop-Location
}

$scriptDirFull = (Resolve-Path $scriptDir).Path
$repoRootFull = (Resolve-Path $repoRoot).Path

if (-not $scriptDirFull.StartsWith($repoRootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Script-mappen ligger ikke inne i repoet."
}

$relativeProjectPath = $scriptDirFull.Substring($repoRootFull.Length).TrimStart("\", "/")
if ([string]::IsNullOrWhiteSpace($relativeProjectPath)) {
    $relativeProjectPath = "."
}

if ([string]::IsNullOrWhiteSpace($CommitMessage)) {
    $CommitMessage = "Update $relativeProjectPath $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
}

Push-Location $repoRootFull
try {
    Write-Step "Repo: $repoRootFull"
    if ($IncludeRepoRootFiles) {
        Write-Step "Stager hele repoet"
        Invoke-Git -GitArgs @("add", "-A")
    }
    else {
        Write-Step "Stager prosjektmappe: $relativeProjectPath"
        Invoke-Git -GitArgs @("add", "-A", "--", $relativeProjectPath)
    }

    $diffExit = Invoke-Git -GitArgs @("diff", "--cached", "--quiet") -AllowNonZeroExit
    if ($diffExit -eq 0) {
        Write-Step "Ingen endringer å committe."
        exit 0
    }

    Write-Step "Oppretter commit"
    Invoke-Git -GitArgs @("commit", "-m", $CommitMessage)

    if ($NoPush) {
        Write-Step "Commit laget. Push hoppet over (-NoPush)."
        exit 0
    }

    $branch = (& git branch --show-current).Trim()
    if ([string]::IsNullOrWhiteSpace($branch)) {
        Write-Warning "Kunne ikke finne aktiv branch. Hopper over push."
        exit 0
    }

    $remotes = & git remote
    if ($LASTEXITCODE -ne 0 -or -not $remotes) {
        Write-Warning "Ingen git remote konfigurert. Commit er laget lokalt."
        exit 0
    }

    $remote = ($remotes | Select-Object -First 1).Trim()
    Write-Step "Pusher til $remote/$branch"
    Invoke-Git -GitArgs @("push", "--set-upstream", $remote, $branch)
    Write-Step "Ferdig."
}
finally {
    Pop-Location
}
