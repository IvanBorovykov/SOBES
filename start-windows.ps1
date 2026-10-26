param([switch]$CheckOnly)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Set-Location -LiteralPath $PSScriptRoot

try {
    $venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        $basePython = $null
        if (Get-Command py -ErrorAction SilentlyContinue) {
            foreach ($version in @('-3.12', '-3.13', '-3.11')) {
                try {
                    $candidate = & py $version -c 'import sys; print(sys.executable)' 2>$null
                } catch {
                    continue
                }
                if ($LASTEXITCODE -eq 0 -and $candidate) {
                    $basePython = [string]$candidate
                    break
                }
            }
        }
        if (-not $basePython -and (Get-Command python -ErrorAction SilentlyContinue)) {
            try {
                $candidate = & python -c 'import sys; assert (3, 11) <= sys.version_info[:2] <= (3, 13); print(sys.executable)' 2>$null
            } catch {
                $candidate = $null
            }
            if ($LASTEXITCODE -eq 0 -and $candidate) { $basePython = [string]$candidate }
        }
        if (-not $basePython) {
            throw 'Install 64-bit Python 3.11, 3.12 or 3.13 from python.org, including Tcl/Tk and the Python launcher.'
        }
        & $basePython -m venv (Join-Path $PSScriptRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python virtual environment.' }
    }

    & $venvPython -c 'import sys, struct, tkinter; assert (3, 11) <= sys.version_info[:2] <= (3, 13) and struct.calcsize(''P'') == 8'
    if ($LASTEXITCODE -ne 0) {
        throw 'The .venv must use 64-bit Python 3.11-3.13 with Tcl/Tk. Rename .venv and run this launcher again.'
    }
    $requirements = Join-Path $PSScriptRoot 'requirements.txt'
    $stampPath = Join-Path $PSScriptRoot '.venv\sobes-requirements.sha256'
    $requirementsHash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
    $installedHash = if (Test-Path -LiteralPath $stampPath) { (Get-Content -LiteralPath $stampPath -Raw).Trim() } else { '' }
    if ($installedHash -ne $requirementsHash) {
        & $venvPython -m pip install --disable-pip-version-check -r $requirements
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check the network and run again.' }
        Set-Content -LiteralPath $stampPath -Value $requirementsHash -Encoding ASCII
    }
    if ($CheckOnly) { exit 0 }
    & $venvPython -X utf8 (Join-Path $PSScriptRoot 'assistant.py')
    exit $LASTEXITCODE
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
