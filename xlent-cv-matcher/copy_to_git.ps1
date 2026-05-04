param(
    [string]$CommitMessage = "",
    [switch]$NoPush,
    [switch]$IncludeRepoRootFiles,
    [string]$TargetRemote = "origin",
    [string]$TargetRemoteUrl = "https://github.com/telboth/Cinode_cv_matcher.git",
    [string]$TargetBranch = ""
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

function Assert-RemoteReachable([string]$RemoteUrl) {
    & git ls-remote $RemoteUrl *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Får ikke tilgang til remote repo: $RemoteUrl. Sjekk at repo finnes og at du er logget inn i Git (PAT/credential manager)."
    }
}

function Get-AheadBehind([string]$LocalRef, [string]$RemoteRef) {
    # Returns @{ Ahead = int; Behind = int } or $null when refs cannot be compared.
    $output = (& git rev-list --left-right --count "$LocalRef...$RemoteRef" 2>$null)
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($output)) {
        return $null
    }

    $parts = ($output -split "\s+") | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    if ($parts.Count -lt 2) {
        return $null
    }

    return @{
        Ahead  = [int]$parts[0]
        Behind = [int]$parts[1]
    }
}

function Assert-NoSensitiveFilesInIndex {
    $stagedLines = (& git diff --cached --name-status) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    if (-not $stagedLines) {
        return
    }

    $blocked = @()
    foreach ($line in $stagedLines) {
        $parts = $line -split "`t"
        if ($parts.Count -lt 2) { continue }
        $status = $parts[0]
        $f = $parts[-1]
        if ($status -like "D*") { continue } # allow removals of sensitive files
        if ($f -match '(^|/)\.env(\..*)?$') { $blocked += $f; continue }
        if ($f -match '(^|/)(secrets(\..*)?\.env|.*\.key|.*\.pem)$') { $blocked += $f; continue }
    }
    if ($blocked.Count -gt 0) {
        $joined = ($blocked | Select-Object -Unique) -join ", "
        throw "Sikkerhetsstopp: sensitive filer er staged: $joined. Fjern dem fra git med 'git reset HEAD <fil>' og bruk ekstern secrets-fil."
    }
}

function Try-AutoResolve-EnvMergeConflict {
    $statusLines = (& git status --porcelain) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    if (-not $statusLines) {
        return $false
    }

    $conflictLines = $statusLines | Where-Object { $_ -match '^(AA|AU|UA|DD|DU|UD|UU)\s+' }
    if (-not $conflictLines) {
        return $false
    }

    $envConflictLines = $conflictLines | Where-Object { $_ -match '^(AA|AU|UA|DD|DU|UD|UU)\s+(.+[\\/])?\.env$' }
    $otherConflictLines = @($conflictLines | Where-Object { $_ -notmatch '^(AA|AU|UA|DD|DU|UD|UU)\s+(.+[\\/])?\.env$' })
    if (-not $envConflictLines -or $otherConflictLines.Count -gt 0) {
        return $false
    }

    foreach ($line in $envConflictLines) {
        $path = $line.Substring(3).Trim()
        if ([string]::IsNullOrWhiteSpace($path)) { continue }
        Write-Step "Autoløser .env-konflikt ved å beholde lokal sletting: $path"
        [void](Invoke-Git -GitArgs @("rm", "--", $path))
    }

    [void](Invoke-Git -GitArgs @("commit", "--no-edit"))
    return $true
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
    Write-Step "Target remote: $TargetRemote ($TargetRemoteUrl)"

    $existingRemoteUrl = (& git remote get-url $TargetRemote 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($existingRemoteUrl)) {
        Write-Step "Legger til remote '$TargetRemote'"
        Invoke-Git -GitArgs @("remote", "add", $TargetRemote, $TargetRemoteUrl)
    }
    elseif ($existingRemoteUrl -ne $TargetRemoteUrl) {
        Write-Step "Oppdaterer remote-url for '$TargetRemote'"
        Invoke-Git -GitArgs @("remote", "set-url", $TargetRemote, $TargetRemoteUrl)
    }

    if ($IncludeRepoRootFiles) {
        Write-Step "Stager hele repoet"
        [void](Invoke-Git -GitArgs @("add", "-A"))
    }
    else {
        Write-Step "Stager prosjektmappe: $relativeProjectPath"
        [void](Invoke-Git -GitArgs @("add", "-A", "--", $relativeProjectPath))
    }

    $diffExit = Invoke-Git -GitArgs @("diff", "--cached", "--quiet") -AllowNonZeroExit
    if ($diffExit -eq 0) {
        Write-Step "Ingen endringer å committe."
        exit 0
    }

    Assert-NoSensitiveFilesInIndex

    Write-Step "Oppretter commit"
    [void](Invoke-Git -GitArgs @("commit", "-m", $CommitMessage))

    if ($NoPush) {
        Write-Step "Commit laget. Push hoppet over (-NoPush)."
        exit 0
    }

    $currentBranch = (& git branch --show-current).Trim()
    if ([string]::IsNullOrWhiteSpace($currentBranch)) {
        throw "Kunne ikke finne aktiv branch for push."
    }

    Assert-RemoteReachable -RemoteUrl $TargetRemoteUrl

    Write-Step "Henter siste endringer fra remote"
    [void](Invoke-Git -GitArgs @("fetch", $TargetRemote, "--prune"))

    $remoteBranchToTrack = if ([string]::IsNullOrWhiteSpace($TargetBranch)) { $currentBranch } else { $TargetBranch }
    $remoteRef = "$TargetRemote/$remoteBranchToTrack"
    $aheadBehind = Get-AheadBehind -LocalRef $currentBranch -RemoteRef $remoteRef
    if ($aheadBehind -and $aheadBehind.Behind -gt 0) {
        Write-Step "Remote er foran med $($aheadBehind.Behind) commit(s). Fletter inn $remoteRef før push."
        $mergeExit = Invoke-Git -GitArgs @("merge", "--no-edit", $remoteRef) -AllowNonZeroExit
        if ($mergeExit -ne 0) {
            if (Try-AutoResolve-EnvMergeConflict) {
                Write-Step "Merge-konflikt i .env ble løst automatisk."
            }
            else {
                throw "Merge mot $remoteRef feilet med konflikter som må løses manuelt."
            }
        }
    }

    if ([string]::IsNullOrWhiteSpace($TargetBranch)) {
        Write-Step "Pusher til $TargetRemote/$currentBranch"
        [void](Invoke-Git -GitArgs @("push", "--set-upstream", $TargetRemote, $currentBranch))
    }
    else {
        Write-Step "Pusher HEAD til $TargetRemote/$TargetBranch"
        [void](Invoke-Git -GitArgs @("push", "--set-upstream", $TargetRemote, "HEAD:$TargetBranch"))
    }
    Write-Step "Ferdig."
}
finally {
    Pop-Location
}
