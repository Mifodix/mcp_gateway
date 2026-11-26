param(
    [string]$Image = 'python:3.12',
    [string]$Memory = '512M',
    [switch]$CowMemory,
    [string]$Results
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Results) {
    $suffix = if ($CowMemory) { 'results-windows-cow.json' } else { 'results-windows.json' }
    $Results = Join-Path $scriptDir $suffix
}

$arguments = @(
    (Join-Path $scriptDir 'spike.py'),
    '--image', $Image,
    '--memory', $Memory,
    '--results', $Results
)
if ($CowMemory) {
    $arguments += '--cow-memory'
}

& python @arguments
exit $LASTEXITCODE
