param([string]$Node = 'node')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run bootstrap.ps1 first.' }
$null = Get-Command $Node -ErrorAction Stop
Push-Location $root
try {
    & $python -X utf8 -m unittest discover -p 'test_*.py' -q
    if ($LASTEXITCODE -ne 0) { throw 'Application tests failed.' }
    & $python -X utf8 -m unittest discover -s deploy/aws-relay -p test_relay.py -q
    if ($LASTEXITCODE -ne 0) { throw 'Relay tests failed.' }
    & $Node test_queue_ui.cjs
    if ($LASTEXITCODE -ne 0) { throw 'UI tests failed.' }
    Get-ChildItem -LiteralPath $root,$PSScriptRoot -Filter '*.ps1' -File | ForEach-Object {
        $parseErrors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$null, [ref]$parseErrors)
        if ($parseErrors.Count) { throw "PowerShell syntax error in $($_.Name)" }
    }
    & $python -X utf8 scripts/check_release.py
    if ($LASTEXITCODE -ne 0) { throw 'Public source check failed.' }
    & $python -X utf8 scripts/build_release.py
    if ($LASTEXITCODE -ne 0) { throw 'Source archive build failed.' }
    Write-Output 'All local checks passed; no hosted CI was used.'
} finally { Pop-Location }
