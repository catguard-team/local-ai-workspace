param(
    [ValidatePattern('^[A-Za-z0-9_./:@-]+$')][string]$ModelKey = 'qwen3.8-27b-uncensored-hauhaucs-aggressive-mtp',
    [ValidateRange(0.0, 1.0)][double]$GpuOffload = 0.25,
    [ValidateRange(2048, 262144)][int]$ContextLength = 32768
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$state = Join-Path $root '.runtime'
$chatPython = Join-Path $root '.venv\Scripts\python.exe'
$lms = Join-Path $env:USERPROFILE '.lmstudio\bin\lms.exe'
Set-Location -LiteralPath $root
if (-not (Test-Path -LiteralPath $chatPython -PathType Leaf)) { throw 'Run bootstrap.ps1 first.' }
if (-not (Test-Path -LiteralPath (Join-Path $state 'sandbox-agent\app.db') -PathType Leaf)) { throw 'Create an account with setup_accounts.py create first.' }
if (-not (Test-Path -LiteralPath $lms -PathType Leaf)) { throw 'Open LM Studio once and install its lms CLI first.' }
function Test-LocalService([string]$url) {
    try { $null = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 3; return $true } catch { return $false }
}
function Invoke-LmsReadinessCommand([string]$arguments, [int]$timeoutSeconds) {
    # Bound only this short-lived CLI probe, never the LM Studio app or a load.
    $probe = New-Object System.Diagnostics.Process
    $probe.StartInfo.FileName = $lms
    $probe.StartInfo.Arguments = $arguments
    $probe.StartInfo.WorkingDirectory = $root
    $probe.StartInfo.UseShellExecute = $false
    $probe.StartInfo.CreateNoWindow = $true
    $probe.StartInfo.RedirectStandardOutput = $true
    $probe.StartInfo.RedirectStandardError = $true
    try {
        $null = $probe.Start()
        $stdout = $probe.StandardOutput.ReadToEndAsync()
        $stderr = $probe.StandardError.ReadToEndAsync()
        if (-not $probe.WaitForExit($timeoutSeconds * 1000)) {
            $probe.Kill()
            $null = $probe.WaitForExit(2000)
            return [pscustomobject]@{ Success = $false; Output = '' }
        }
        if (-not $stdout.Wait(1000) -or -not $stderr.Wait(1000)) {
            return [pscustomobject]@{ Success = $false; Output = '' }
        }
        if ($probe.ExitCode -ne 0) {
            $stderr.Result | Out-File (Join-Path $state 'lm-readiness.log')
        }
        return [pscustomobject]@{ Success = ($probe.ExitCode -eq 0); Output = $stdout.Result }
    } finally { $probe.Dispose() }
}
function Invoke-LmsLoadCommand([string]$arguments, [string]$logName) {
    # Windows PowerShell 5.1 turns redirected native stderr into ErrorRecords.
    # LM Studio writes successful load messages there, so capture both streams
    # directly and let the process exit code determine whether loading failed.
    $loader = New-Object System.Diagnostics.Process
    $loader.StartInfo.FileName = $lms
    $loader.StartInfo.Arguments = $arguments
    $loader.StartInfo.WorkingDirectory = $root
    $loader.StartInfo.UseShellExecute = $false
    $loader.StartInfo.CreateNoWindow = $true
    $loader.StartInfo.RedirectStandardOutput = $true
    $loader.StartInfo.RedirectStandardError = $true
    try {
        $null = $loader.Start()
        $stdout = $loader.StandardOutput.ReadToEndAsync()
        $stderr = $loader.StandardError.ReadToEndAsync()
        $loader.WaitForExit()
        $combinedOutput = $stdout.GetAwaiter().GetResult() + $stderr.GetAwaiter().GetResult()
        [System.IO.File]::WriteAllText((Join-Path $state $logName), $combinedOutput)
        return $loader.ExitCode
    } finally { $loader.Dispose() }
}
function Invoke-PythonCommand([string]$arguments, [string]$logName) {
    # WScript login startup has no console handles. The Windows venv launcher
    # exits before Python starts unless all three standard handles are valid.
    $command = New-Object System.Diagnostics.Process
    $command.StartInfo.FileName = $chatPython
    $command.StartInfo.Arguments = '-X utf8 ' + $arguments
    $command.StartInfo.WorkingDirectory = $root
    $command.StartInfo.UseShellExecute = $false
    $command.StartInfo.CreateNoWindow = $true
    $command.StartInfo.RedirectStandardInput = $true
    $command.StartInfo.RedirectStandardOutput = $true
    $command.StartInfo.RedirectStandardError = $true
    try {
        $null = $command.Start()
        $command.StandardInput.Close()
        $stdout = $command.StandardOutput.ReadToEndAsync()
        $stderr = $command.StandardError.ReadToEndAsync()
        if (-not $command.WaitForExit(60000)) {
            $command.Kill()
            throw 'Short Python startup command timed out.'
        }
        $output = $stdout.GetAwaiter().GetResult()
        $errorOutput = $stderr.GetAwaiter().GetResult()
        [System.IO.File]::WriteAllText((Join-Path $state $logName), $errorOutput)
        return [pscustomobject]@{ ExitCode = $command.ExitCode; Output = $output }
    } finally { $command.Dispose() }
}
$rootHash = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToLowerInvariant()))).Replace('-','').Substring(0,12)
$startupMutex = New-Object System.Threading.Mutex($false, ('Local\LocalAIserverStartup-' + $rootHash))
$startupLockHeld = $false
try {
    try { $startupLockHeld = $startupMutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $startupLockHeld = $true }
    if (-not $startupLockHeld) { Write-Output 'Local AI startup is already running.'; return }
    $controlResult = Invoke-PythonCommand '-c "from server_control import prepare_start; prepare_start()"' 'server-control-error.log'
    if ($controlResult.ExitCode -ne 0) { throw 'Server shutdown is still in progress or its status is invalid. See server-control-error.log.' }

if (-not (Get-Process -Name 'LM Studio' -ErrorAction SilentlyContinue) -and -not (Test-LocalService 'http://127.0.0.1:1234/v1/models')) {
    Start-Process -FilePath (Join-Path $env:LOCALAPPDATA 'Programs\LM Studio\LM Studio.exe') -WindowStyle Hidden
}
# Login may start LM Studio before its SDK is ready. Wait in both cases.
$lmReady = $false
$lmDeadline = [DateTime]::UtcNow.AddSeconds(180)
while ([DateTime]::UtcNow -lt $lmDeadline) {
    $remaining = [Math]::Max(1, [Math]::Min(15, [int]($lmDeadline - [DateTime]::UtcNow).TotalSeconds))
    $probeResult = Invoke-LmsReadinessCommand 'ps --json' $remaining
    $sdkReady = $false
    if ($probeResult.Success) {
        try {
            $loaded = @($probeResult.Output | ConvertFrom-Json)
            $sdkReady = $probeResult.Output.TrimStart().StartsWith('[')
        } catch { $sdkReady = $false }
    }
    if ($sdkReady) {
        if (Test-LocalService 'http://127.0.0.1:1234/v1/models') { $lmReady = $true; break }
        $remaining = [Math]::Max(1, [Math]::Min(15, [int]($lmDeadline - [DateTime]::UtcNow).TotalSeconds))
        $serverResult = Invoke-LmsReadinessCommand 'server start --bind 127.0.0.1 --port 1234' $remaining
        if ($serverResult.Success -and (Test-LocalService 'http://127.0.0.1:1234/v1/models')) { $lmReady = $true; break }
    }
    if ([DateTime]::UtcNow -lt $lmDeadline) { Start-Sleep -Seconds 3 }
}
if (-not $lmReady) { throw 'LM Studio SDK/server did not become ready within 180 seconds. See lm-readiness.log.' }
if (-not ($loaded | Where-Object { $_.identifier -eq 'qwen3.8-27b' })) {
    $loadArguments = 'load ' + $ModelKey + ' --gpu ' + $GpuOffload.ToString([Globalization.CultureInfo]::InvariantCulture) + ' --context-length ' + $ContextLength + ' --parallel 1 --identifier qwen3.8-27b --no-speculative-draft-mtp -y'
    $loadExitCode = Invoke-LmsLoadCommand $loadArguments 'lm-start.log'
    if ($loadExitCode -ne 0) { throw 'Qwen model loading failed. See lm-start.log.' }
}
$loadedResult = Invoke-LmsReadinessCommand 'ps --json' 15
if (-not $loadedResult.Success) { throw 'Could not verify loaded LM Studio models.' }
$loaded = @($loadedResult.Output | ConvertFrom-Json)
if (-not ($loaded | Where-Object { $_.identifier -eq 'qwen3.8-27b' })) { throw 'Required model qwen3.8-27b is not loaded; refusing incomplete startup.' }
if (Test-LocalService 'http://127.0.0.1:8081/api/health') {
    $agentHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8081/api/health' -TimeoutSec 3
    if ($agentHealth.mode -ne 'sandbox-files-only') { throw 'Port 8081 is occupied by an unrestricted agent. Stop it before starting the sandbox.' }
} else {
    $launchResult = Invoke-PythonCommand ('"' + (Join-Path $root 'launch-computer.py') + '"') 'agent-launcher-error.log'
    if ($launchResult.ExitCode -ne 0) { throw 'Restricted agent launcher failed. See agent-launcher-error.log.' }
}
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    if (Test-LocalService 'http://127.0.0.1:8081/api/health') { break }
    Start-Sleep -Seconds 1
}
$agentHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8081/api/health' -TimeoutSec 3
if ($agentHealth.mode -ne 'sandbox-files-only') { throw 'Restricted agent startup failed.' }
if (-not (Test-LocalService 'http://127.0.0.1:8787/api/health')) {
    $launchResult = Invoke-PythonCommand ('"' + (Join-Path $root 'launch-gateway.py') + '"') 'gateway-launcher-error.log'
    if ($launchResult.ExitCode -ne 0) { throw 'Gateway launcher failed. See gateway-launcher-error.log.' }
}
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    if (Test-LocalService 'http://127.0.0.1:8787/api/health') { break }
    Start-Sleep -Seconds 1
}
$gatewayHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8787/api/health' -TimeoutSec 3
if ($gatewayHealth.mode -ne 'sandbox-files-only') { throw 'Gateway is not connected to the restricted service. Restart gateway.py.' }
$vpnResult = Invoke-PythonCommand '-c "import json; from local_urls import vpn_only_access; print(json.dumps(vpn_only_access()))"' 'vpn-config-error.log'
if ($vpnResult.ExitCode -ne 0) { throw 'Invalid VPN-only configuration; startup stopped.' }
$vpnAccess = $vpnResult.Output | ConvertFrom-Json
if ($null -ne $vpnAccess) {
    # The service is installed and set to automatic by the one-time VPN setup.
    # Never start other personal VPN tunnels or Cloudflare tunnels in this mode.
    $wireGuardService = Get-Service -Name 'WireGuardTunnel$localai' -ErrorAction SilentlyContinue
    if ($null -ne $wireGuardService -and $wireGuardService.Status -ne 'Running') {
        try { Start-Service -Name 'WireGuardTunnel$localai' -ErrorAction Stop }
        catch {
            'WireGuard localai service is not running. Its automatic startup or administrator setup must complete; the gateway keeps waiting for the VPN address.' | Add-Content -LiteralPath (Join-Path $state 'vpn-startup.log')
        }
    }
    $vpnAddress = Get-NetIPAddress -IPAddress $vpnAccess.bind_ip -AddressFamily IPv4 -ErrorAction SilentlyContinue
    if ($null -eq $vpnAddress) {
        'WireGuard address is not ready. Loopback services remain available; the gateway will retry its VPN listener automatically.' | Add-Content -LiteralPath (Join-Path $state 'vpn-startup.log')
    }
    Write-Output 'WireGuard transport selected; Cloudflare tunnels are not started.'
} else {
    Write-Output 'Loopback startup selected. Remote transports must be configured and started explicitly.'
}
$writeResult = Invoke-PythonCommand ('"' + (Join-Path $root 'write-access.py') + '"') 'write-access-error.log'
if ($writeResult.ExitCode -ne 0) { throw 'Could not write the current access address.' }
Write-Output $writeResult.Output
('{0:o} startup_completed' -f [DateTime]::UtcNow) | Add-Content -LiteralPath (Join-Path $state 'startup-success.log')
} catch {
    ('{0:o} {1}' -f [DateTime]::UtcNow, $_.Exception.Message) | Add-Content -LiteralPath (Join-Path $state 'startup-error.log')
    throw
} finally {
    if ($startupLockHeld) { $startupMutex.ReleaseMutex() }
    $startupMutex.Dispose()
}
