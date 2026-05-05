param(
    [string]$ShortcutName = "XLENT CV Matcher",
    [string]$Url = "http://127.0.0.1:5173/"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$vbsPath = Join-Path $root "launch-local-hidden.vbs"

if (!(Test-Path $vbsPath)) {
    throw "Fant ikke launcher: $vbsPath"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "$ShortcutName.lnk"

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "$env:WINDIR\System32\wscript.exe"
$shortcut.Arguments = "`"$vbsPath`""
$shortcut.WorkingDirectory = $root
$shortcut.Description = "Start XLENT CV Matcher og aapne $Url"
$shortcut.IconLocation = "$env:WINDIR\System32\SHELL32.dll,220"
$shortcut.Save()

Write-Host "Laget skrivebordsikon: $shortcutPath"

