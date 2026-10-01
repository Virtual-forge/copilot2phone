<#
.SYNOPSIS
    AgentLink Phase 0 probe hook.

.DESCRIPTION
    Install this as the agent's PreToolUse hook to capture the real hook
    contract: the JSON that arrives on stdin, the response the agent expects,
    and the exit-code semantics.

    Every invocation is written to %USERPROFILE%\.agentlink\probe\ as
    <agent>-input-<timestamp>.json, with a one-line summary in probe.log.

    The response is read from <agent>-response.json in the same folder, so you
    can flip between allow and block without editing this script.

.EXAMPLE
    # Capture only; allow by default.
    .\probe-hook.ps1 -Agent cline

.EXAMPLE
    # Force a block for the next invocation.
    '{"cancel": true, "errorMessage": "probe block"}' |
        Set-Content "$env:USERPROFILE\.agentlink\probe\cline-response.json"
    .\probe-hook.ps1 -Agent cline

.EXAMPLE
    # Force a non-zero exit for Codex.
    .\probe-hook.ps1 -Agent codex -ExitCode 2
#>
[CmdletBinding()]
param(
    [ValidateSet('cline', 'codex')]
    [string]$Agent = 'cline',

    [string]$ResponseFile = '',

    [int]$ExitCode = 0
)

$ErrorActionPreference = 'Stop'

$probeDir = Join-Path $env:USERPROFILE '.agentlink\probe'
New-Item -ItemType Directory -Force -Path $probeDir | Out-Null

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$inputFile = Join-Path $probeDir "$Agent-input-$stamp.json"

$stdin = [Console]::In.ReadToEnd()
Set-Content -Path $inputFile -Value $stdin -Encoding UTF8

$logFile = Join-Path $probeDir 'probe.log'
$line = "[{0}] agent={1} bytes={2} exit={3} -> {4}" -f $stamp, $Agent, $stdin.Length, $ExitCode, $inputFile
Add-Content -Path $logFile -Value $line

if (-not $ResponseFile) {
    $ResponseFile = Join-Path $probeDir "$Agent-response.json"
}

if (Test-Path $ResponseFile) {
    $response = Get-Content -Raw -Path $ResponseFile
}
elseif ($Agent -eq 'cline') {
    $response = '{"cancel": false}'
}
else {
    $response = ''
}

if ($response) {
    Write-Output $response
}

exit $ExitCode
