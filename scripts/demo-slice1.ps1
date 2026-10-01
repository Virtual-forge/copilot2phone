<#
.SYNOPSIS
    End-to-end demo of the slice 1 approval loop.

.DESCRIPTION
    Requires `agentd run` in another terminal.

      1. checks the daemon is up
      2. fires a synthetic Cline hook in the background (it blocks)
      3. waits for the approval to appear
      4. approves (or denies) it
      5. prints the hook's response

.EXAMPLE
    .\demo-slice1.ps1

.EXAMPLE
    .\demo-slice1.ps1 -Command "git push --force" -Deny
#>
[CmdletBinding()]
param(
    [string]$Command = 'rm -rf build/',

    [switch]$Deny
)

$ErrorActionPreference = 'Stop'

Write-Host '== 1. checking the daemon ==' -ForegroundColor Cyan
try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:47800/v1/health' -TimeoutSec 3
    Write-Host "   ok: agentd $($health.version)" -ForegroundColor Green
}
catch {
    Write-Host '   agentd is not running. Start it with: agentd run' -ForegroundColor Red
    exit 1
}

$fakeHook = Join-Path $PSScriptRoot 'fake-hook.ps1'

Write-Host '== 2. firing a synthetic Cline hook (it will block) ==' -ForegroundColor Cyan
Write-Host "   command: $Command" -ForegroundColor DarkGray
$job = Start-Job -ScriptBlock {
    param($hook, $cmd)
    & $hook -Agent cline -Tool execute_command -Command $cmd
} -ArgumentList $fakeHook, $Command

Write-Host '== 3. waiting for the approval to appear ==' -ForegroundColor Cyan
$approval = $null
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    $pending = @(agentlink-sim list --state pending --json | ConvertFrom-Json)
    if ($pending.Count -gt 0) {
        $approval = $pending[0]
        break
    }
}

if (-not $approval) {
    Write-Host '   no approval appeared; stopping' -ForegroundColor Red
    Stop-Job $job -ErrorAction SilentlyContinue
    Remove-Job $job -Force -ErrorAction SilentlyContinue
    exit 1
}

Write-Host "   pending: $($approval.approval_id)" -ForegroundColor Yellow
agentlink-sim list --state pending

Write-Host '== 4. deciding ==' -ForegroundColor Cyan
if ($Deny) {
    agentlink-sim deny $approval.approval_id --reason 'demo deny'
}
else {
    agentlink-sim approve $approval.approval_id --reason 'demo approve'
}

Write-Host '== 5. hook result ==' -ForegroundColor Cyan
Wait-Job $job -Timeout 20 | Out-Null
Receive-Job $job
Remove-Job $job -Force -ErrorAction SilentlyContinue

Write-Host '== 6. audit trail ==' -ForegroundColor Cyan
agentlink-sim show $approval.approval_id
