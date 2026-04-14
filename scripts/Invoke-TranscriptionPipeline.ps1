param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot
try {
    $env:PYTHONDONTWRITEBYTECODE = "1"
    if (Test-Path ".\.venv-transcricao\Scripts\python.exe") {
        & ".\.venv-transcricao\Scripts\python.exe" -m transcribe_pipeline @Args
    } else {
        & python -m transcribe_pipeline @Args
    }
} finally {
    Pop-Location
}
