<#
.SYNOPSIS
    Fire a batch of synthetic hooks and decide them from your phone.

.DESCRIPTION
    Requires `agentd run --lan` (same Wi-Fi) or `agentd run --tunnel` (anywhere)
    in another terminal.

      1. checks the daemon is up
      2. prints the URL to open on your phone
      3. fires three hooks in the background (they block)
      4. waits for you to decide them on the phone
      5. prints what each hook received

    The three hooks cover both agents and both risk levels, so you can try the
    plain tap (medium) and the press-and-hold (high, D-004).

    The phone URL comes from the daemon itself (`agentd tunnel` for the public
    URL, `agentd.netinfo` for the LAN one), so there is one source of truth.

.PARAMETER Tunnel
    Print the public ngrok URL instead of the LAN one. Needs `agentd run
    --tunnel` (or a separately started `ngrok http 47800`).

.EXAMPLE
    .\phone-demo.ps1

.EXAMPLE
    .\phone-demo.ps1 -TimeoutSeconds 120

.EXAMPLE
    .\phone-demo.ps1 -Tunnel
#>
[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 300,
    [switch]$Tunnel
)

$ErrorActionPreference = 'Stop'

$base = 'http://127.0.0.1:47800'

Write-Host '== 1. checking the daemon ==' -ForegroundColor Cyan
try {
    $health = Invoke-RestMethod -Uri "$base/v1/health" -TimeoutSec 3
    Write-Host "   ok: agentd $($health.version)" -ForegroundColor Green
}
catch {
    Write-Host '   agentd is not running.' -ForegroundColor Red
    Write-Host '   start it with:  agentd run --lan      (same Wi-Fi)' -ForegroundColor Yellow
    Write-Host '              or:  agentd run --tunnel   (from anywhere)' -ForegroundColor Yellow
    exit 1
}

Write-Host '== 2. open this on your phone ==' -ForegroundColor Cyan
$tokenFile = Join-Path $env:USERPROFILE '.agentlink\local_api_token'

if ($Tunnel) {
    $out = & agentd tunnel 2>&1
    $match = $out | Select-String -Pattern 'https?://\S+/#t=\S+' | Select-Object -First 1
    if ($match) {
        Write-Host "   $($match.Matches[0].Value)" -ForegroundColor Green
        Write-Host '   (public URL - works from anywhere)' -ForegroundColor DarkGray
    }
    else {
        Write-Host '   no ngrok tunnel found.' -ForegroundColor Yellow
        Write-Host '   start one with:  agentd run --tunnel' -ForegroundColor Yellow
    }
}
elseif (Test-Path $tokenFile) {
    $token = (Get-Content $tokenFile -Raw).Trim()
    # One source of truth for the LAN address: agentd.netinfo.
    $ip = (& python -c "from agentd import netinfo; print(netinfo.lan_ip() or '')" 2>$null |
        Select-Object -First 1)
    if ($ip) {
        Write-Host "   http://$($ip.Trim()):47800/#t=$token" -ForegroundColor Green
        Write-Host '   (same Wi-Fi as this PC)' -ForegroundColor DarkGray
    }
    else {
        Write-Host '   could not detect a LAN address' -ForegroundColor Yellow
    }
}
else {
    Write-Host "   no token file at $tokenFile - run 'agentd run' once" -ForegroundColor Yellow
}

$fakeHook = Join-Path $PSScriptRoot 'fake-hook.ps1'

$cases = @(
    @{ Agent = 'codex'; Tool = 'shell'; Command = 'npm test'; Label = 'medium - tap Allow' }
    @{ Agent = 'codex'; Tool = 'shell'; Command = 'rm -rf build/'; Label = 'high - press and hold' }
    @{ Agent = 'codex'; Tool = 'shell'; Command = 'git push --force origin main'; Label = 'high - press and hold' }
)

Write-Host '== 3. firing hooks (they block until you decide) ==' -ForegroundColor Cyan
$jobs = @()
foreach ($case in $cases) {
    $jobs += Start-Job -ScriptBlock {
        param($hook, $agent, $tool, $cmd)
        & $hook -Agent $agent -Tool $tool -Command $cmd
    } -ArgumentList $fakeHook, $case.Agent, $case.Tool, $case.Command
    Write-Host "   $($case.Agent.PadRight(6)) $($case.Command.PadRight(28)) [$($case.Label)]" -ForegroundColor DarkGray
}

Write-Host '== 4. waiting for your decisions ==' -ForegroundColor Cyan
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    $pending = @(agentlink-sim list --state pending --json | ConvertFrom-Json)
    $done = @($jobs | Where-Object { $_.State -ne 'Running' }).Count
    Write-Host "   pending: $($pending.Count)   decided: $done/$($jobs.Count)" -ForegroundColor DarkGray
    if ($done -eq $jobs.Count) { break }
}

Write-Host '== 5. what each hook received ==' -ForegroundColor Cyan
foreach ($job in $jobs) {
    if ($job.State -eq 'Running') {
        Write-Host '   (still waiting - timed out)' -ForegroundColor Yellow
        Stop-Job $job -ErrorAction SilentlyContinue
    }
    else {
        Receive-Job $job | ForEach-Object { Write-Host "   $_" }
    }
    Remove-Job $job -Force -ErrorAction SilentlyContinue
}

Write-Host '== 6. audit trail ==' -ForegroundColor Cyan
agentlink-sim list --limit 5
