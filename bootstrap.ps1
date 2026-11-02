param(
    [string]$Python,
    [switch]$SkipAccountSetup
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location -LiteralPath $root
if (-not [Environment]::Is64BitOperatingSystem) { throw 'Windows x64 is required.' }

# Keep bootstrap diagnostics private before installing or provisioning accounts.
& (Join-Path $root 'scripts\protect-runtime.ps1')
$log = Join-Path $root '.runtime\bootstrap.log'

function ConvertTo-NativeArgument([string]$value) {
    if ($value.Length -gt 0 -and $value -notmatch '[\s"]') { return $value }
    $escaped = [regex]::Replace($value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Invoke-SetupProcess([string]$file, [string[]]$arguments) {
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo.FileName = $file
    $process.StartInfo.Arguments = (($arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' ')
    $process.StartInfo.WorkingDirectory = $root
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    $process.StartInfo.RedirectStandardInput = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    $process.StartInfo.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
    $process.StartInfo.StandardErrorEncoding = New-Object Text.UTF8Encoding($false)
    try {
        $null = $process.Start()
        $process.StandardInput.Close()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $output = $stdout.GetAwaiter().GetResult()
        $errorOutput = $stderr.GetAwaiter().GetResult()
        [IO.File]::AppendAllText($log, $output + $errorOutput, (New-Object Text.UTF8Encoding($false)))
        if ($process.ExitCode -ne 0) { throw "Setup command failed (exit $($process.ExitCode)). See .runtime\bootstrap.log." }
    } finally { $process.Dispose() }
}

if ($Python) {
    $sourcePython = (Get-Item -LiteralPath $Python -ErrorAction Stop).FullName
    $sourceArguments = @('-X', 'utf8', (Join-Path $root 'scripts\stage-python.py'))
} else {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if (-not $launcher) { throw 'Install complete Python 3.12 x64, then run bootstrap.ps1 -Python C:\Path\To\python.exe.' }
    $sourcePython = $launcher.Source
    $sourceArguments = @('-3.12', '-X', 'utf8', (Join-Path $root 'scripts\stage-python.py'))
}

Write-Host 'Preparing a standalone Python runtime in this repository...'
Invoke-SetupProcess $sourcePython $sourceArguments
$runtimePython = Join-Path $root 'bin\python312\python.exe'
$venv = Join-Path $root '.venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venv)) {
    Invoke-SetupProcess $runtimePython @('-X', 'utf8', '-m', 'venv', '--copies', $venv)
}
Invoke-SetupProcess $venvPython @('-X', 'utf8', (Join-Path $root 'scripts\stage-python.py'), '--check-venv')
Write-Host 'Installing the pinned application dependencies...'
Invoke-SetupProcess $venvPython @('-X', 'utf8', '-m', 'pip', 'install', '--disable-pip-version-check', '-r', (Join-Path $root 'requirements.txt'), '-c', (Join-Path $root 'requirements-lock.txt'))
foreach ($path in @((Join-Path $root 'agent'), (Join-Path $root '.runtime\sandbox-agent\uploads'))) {
    $null = New-Item -ItemType Directory -Path $path -Force
    if ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Runtime and agent directories must not be filesystem links.' }
}
Write-Host 'Dependencies are ready. No model was downloaded and no server was started.'
if (-not $SkipAccountSetup) {
    # This final command is intentionally interactive; passwords never become arguments.
    $savedErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $venvPython -X utf8 (Join-Path $root 'setup_accounts.py') create
        $accountExitCode = $LASTEXITCODE
    } finally { $ErrorActionPreference = $savedErrorPreference }
    if ($accountExitCode -ne 0) { throw 'Account setup did not finish. Run .venv\Scripts\python.exe setup_accounts.py create in an interactive terminal.' }
}
Write-Host 'Next: configure/load your model in LM Studio, then run start.ps1. See README.md.'
