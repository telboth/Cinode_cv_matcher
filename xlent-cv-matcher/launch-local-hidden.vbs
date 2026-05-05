Option Explicit

Dim fso, shell, repoDir, psCommand
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

repoDir = fso.GetParentFolderName(WScript.ScriptFullName)

psCommand = "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & repoDir & "\start-local.ps1"" -NoWait"
shell.Run psCommand, 0, False

' Give API/web a moment to start before opening browser.
WScript.Sleep 2500
shell.Run "http://127.0.0.1:5173/", 1, False

