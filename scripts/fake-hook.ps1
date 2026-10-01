<#
.SYNOPSIS
    Send a synthetic agent hook payload through the AgentLink hook adapter.

.DESCRIPTION
    Lets you exercise the whole slice 1 loop without Cline or Codex installed.
    The adapter will block until the daemon returns a decision, so run this in
    one terminal and decide from another (or use .\demo-slice1.ps1).

.EXAMPLE
    .\fake-hook.ps1 -Agent cline -Tool execute_command -Command "rm -rf build/"

.EXAMPLE
    .\fake-hook.ps1 -Agent codex -Tool shell -Command "git push --force"
#>
[CmdletBinding()]
param(
    [ValidateSet('cline', 'codex')]
    [string]$Agent = 'cline',

    [string]$Tool = '',

    [string]$Command = 'rm -rf build/',

    [string]$Path = '',

    [string]$Workspace = (Get-Location).Path,

    [string]$SessionId = 'demo-session'
)

$ErrorActionPreference = 'Stop'

if (-not $Tool) {
    $Tool = if ($Agent -eq 'cline') { 'execute_command' } else { 'shell' }
}

if ($Agent -eq 'cline') {
    $payload = @{
        hookName       = 'PreToolUse'
        taskId         = $SessionId
        workspaceRoots = @($Workspace)
        preToolUse     = @{
            toolName   = $Tool
            parameters = @{
                command = $Command
                path    = $Path
                cwd     = $Workspace
            }
        }
    }
    $exe = 'agentd-cline-hook'
}
else {
    $payload = @{
        hook_event_name = 'PreToolUse'
        session_id      = $SessionId
        cwd             = $Workspace
        tool_name       = $Tool
        tool_input      = @{
            command = $Command
            path    = $Path
        }
    }
    $exe = 'agentd-codex-hook'
}

$json = $payload | ConvertTo-Json -Depth 8 -Compress

Write-Host "-> $exe" -ForegroundColor DarkGray
Write-Host "   $json" -ForegroundColor DarkGray

$json | & $exe
$code = $LASTEXITCODE

Write-Host "   exit code: $code" -ForegroundColor DarkGray
exit $code
