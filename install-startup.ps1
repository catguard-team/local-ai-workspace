[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Remove,
    [ValidatePattern('^[A-Za-z0-9_./:@-]+$')][string]$ModelKey = 'qwen3.8-27b-uncensored-hauhaucs-aggressive-mtp',
    [ValidateRange(0.0, 1.0)][double]$GpuOffload = 0.25,
    [ValidateRange(2048, 262144)][int]$ContextLength = 32768
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$hash = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToLowerInvariant()))).Replace('-','').Substring(0,12)
$startupFolder = [Environment]::GetFolderPath('Startup')
$entry = Join-Path $startupFolder ("Local AI Server-$hash.vbs")
if ($Remove) {
    if ((Test-Path -LiteralPath $entry -PathType Leaf) -and $PSCmdlet.ShouldProcess($entry, 'Remove this installation login entry')) {
        Remove-Item -LiteralPath $entry
    }
    return
}
if (-not (Test-Path -LiteralPath (Join-Path $root '.venv\Scripts\python.exe') -PathType Leaf)) { throw 'Run bootstrap.ps1 first.' }
if (-not (Test-Path -LiteralPath (Join-Path $root '.runtime\sandbox-agent\app.db') -PathType Leaf)) { throw 'Create a local account before installing login startup.' }
$powershell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$gpu = $GpuOffload.ToString([Globalization.CultureInfo]::InvariantCulture)
$command = '"' + $powershell + '" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $root 'start.ps1') + '" -ModelKey ' + $ModelKey + ' -GpuOffload ' + $gpu + ' -ContextLength ' + $ContextLength
$vbs = 'Set shell = CreateObject("WScript.Shell")' + "`r`n" + 'shell.Run "' + $command.Replace('"','""') + '", 0, False' + "`r`n"
if ($PSCmdlet.ShouldProcess($entry, 'Install current-user login startup')) {
    [IO.File]::WriteAllText($entry, $vbs, [Text.Encoding]::Unicode)
    Write-Output ('Installed login startup: ' + $entry)
    Write-Output 'Windows login is required. This does not enable automatic Windows login or restart the PC.'
}

