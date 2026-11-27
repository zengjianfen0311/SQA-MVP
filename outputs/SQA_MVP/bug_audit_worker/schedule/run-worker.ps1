param(
    [Parameter(Mandatory=$true)][string]$ConfigPath,
    [string]$Python = 'python',
    [ValidateSet('dry_run','apply')][string]$Mode = 'dry_run'
)
$ErrorActionPreference = 'Stop'
$entry = Join-Path $PSScriptRoot '../../bug_audit_worker_cli.py'
$resolvedConfig = (Resolve-Path -LiteralPath $ConfigPath).Path
$workerArgs = @('-X','utf8',$entry,'run','--config',$resolvedConfig,'--live')
if ($Mode -eq 'apply') { $workerArgs += '--apply' }
& $Python @workerArgs
exit $LASTEXITCODE
