# Checkpoint - one-time setup for Windows (no admin rights needed).
# Usage (from this folder):  powershell -ExecutionPolicy Bypass -File setup.ps1
# Creates .venv, installs pinned Python packages, builds the interface.
# It never downloads speech or AI models - that's an explicit action in Settings.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Fail($msg) {
    Write-Host ""
    Write-Host "  Setup stopped: $msg" -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to close"
    exit 1
}

function Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }

# ---------------------------------------------------------------- Python 3.11+
Step "Checking Python"
$python = $null
foreach ($candidate in @(@("py", "-3.11"), @("py", "-3.12"), @("python"))) {
    try {
        $exe = $candidate[0]
        $pyArgs = @()
        if ($candidate.Length -gt 1) { $pyArgs = $candidate[1..($candidate.Length - 1)] }
        $ver = & $exe @pyArgs -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver) {
            $parts = $ver.Trim().Split(".")
            if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11) { $python = $candidate; break }
        }
    } catch { }
}
if (-not $python) {
    Fail "Python 3.11 or newer wasn't found. Install it from https://www.python.org/downloads/windows/ (tick 'Add python.exe to PATH'), then run setup.ps1 again."
}
Write-Host "    Using: $($python -join ' ') ($ver)"

# ---------------------------------------------------------------- virtual environment
Step "Creating the Python environment (.venv)"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    $exe = $python[0]
    $pyArgs = @()
    if ($python.Length -gt 1) { $pyArgs = $python[1..($python.Length - 1)] }
    & $exe @pyArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail "Couldn't create the virtual environment." }
}
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

Step "Installing Python packages (first run can take a few minutes)"
& $venvPy -m pip install --upgrade pip --disable-pip-version-check -q
if ($LASTEXITCODE -ne 0) { Fail "pip upgrade failed. Check your internet connection." }
& $venvPy -m pip install -r backend\requirements.txt --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Fail "Installing packages failed. Scroll up for the error; a network or proxy issue is the usual cause." }

# ---------------------------------------------------------------- interface
Step "Building the interface"
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
    if (Test-Path "frontend\dist\index.html") {
        Write-Host "    Node.js not found - using the existing prebuilt interface." -ForegroundColor Yellow
    } else {
        Fail "Node.js 20+ is needed to build the interface. Install the LTS version from https://nodejs.org and run setup.ps1 again."
    }
} else {
    $nodeVer = (& node -p "process.versions.node").Trim()
    if ([int]($nodeVer.Split(".")[0]) -lt 20) { Fail "Node.js $nodeVer is too old; install Node.js 20 LTS or newer." }
    Push-Location frontend
    try {
        if (Test-Path "package-lock.json") { & npm ci --no-audit --no-fund } else { & npm install --no-audit --no-fund }
        if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
    } catch {
        Pop-Location
        Fail "Building the interface failed: $_"
    }
    Pop-Location
}

# ---------------------------------------------------------------- config
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "    Created .env from .env.example" }

Step "Checking the installation"
Push-Location backend
& $venvPy -m checkpoint.preflight
$ok = $LASTEXITCODE
Pop-Location
if ($ok -ne 0) { Fail "Preflight reported errors (see above)." }

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "  Start Checkpoint:  start.cmd"
Write-Host "  Optional: download a transcription model and set up Ollama in Settings (see docs\models.md)."
Write-Host ""
