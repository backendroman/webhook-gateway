# Windows equivalent of the Makefile: .\scripts\dev.ps1 <up|down|migrate|test|test-unit|lint|fmt>
param([Parameter(Mandatory = $true)][ValidateSet('up', 'down', 'migrate', 'test', 'test-unit', 'lint', 'fmt')][string]$Target)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

function Invoke-Step([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

switch ($Target) {
    'up'        { Invoke-Step docker @('compose', 'up', '--build', '-d') }
    'down'      { Invoke-Step docker @('compose', 'down') }
    'migrate'   { Invoke-Step uv @('run', 'alembic', 'upgrade', 'head') }
    'test'      { Invoke-Step uv @('run', 'pytest') }
    'test-unit' { Invoke-Step uv @('run', 'pytest', 'tests/unit') }
    'lint' {
        Invoke-Step uv @('run', 'ruff', 'check', '.')
        Invoke-Step uv @('run', 'ruff', 'format', '--check', '.')
        Invoke-Step uv @('run', 'mypy')
    }
    'fmt' {
        Invoke-Step uv @('run', 'ruff', 'format', '.')
        Invoke-Step uv @('run', 'ruff', 'check', '--fix', '.')
    }
}
