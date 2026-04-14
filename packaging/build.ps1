<#
.SYNOPSIS
    Build Transcritorio standalone distribution for Windows.

.DESCRIPTION
    Creates a build venv, installs all dependencies (including PyTorch CUDA),
    downloads FFmpeg, runs PyInstaller, and verifies the output.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build.ps1 -SkipVenv -SkipFfmpeg
#>

param(
    [string]$VenvPath = "$env:LOCALAPPDATA\Transcritorio\build-venv",
    [string]$FfmpegBuild = "n7.1-latest-win64-gpl-shared-7.1",
    [switch]$SkipVenv,
    [switch]$SkipFfmpeg,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"

$RepoRoot    = (Resolve-Path "$PSScriptRoot\..").Path
$PackagingDir = Join-Path $RepoRoot "packaging"
$VendorDir    = Join-Path $PackagingDir "vendor"
# Build/dist OUTSIDE Dropbox to avoid file-locking on .exe/.dll during PyInstaller
$AppBuildRoot = Join-Path $env:LOCALAPPDATA "Transcritorio\packaging"
$DistDir      = Join-Path $AppBuildRoot "dist"
$BuildDir     = Join-Path $AppBuildRoot "build"

Write-Host "=== Transcritorio Build ===" -ForegroundColor Cyan
Write-Host "  Repo:  $RepoRoot"
Write-Host "  Venv:  $VenvPath"
Write-Host "  Dist:  $DistDir"
Write-Host ""

# -----------------------------------------------------------------------
# Step 1: Build venv
# -----------------------------------------------------------------------
if (-not $SkipVenv) {
    Write-Host "--- [1/7] Creating build venv ---" -ForegroundColor Yellow

    if (Test-Path $VenvPath) {
        Write-Host "  Removing existing venv..."
        Remove-Item $VenvPath -Recurse -Force
    }

    py -3 -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) { throw "Failed to create venv" }

    $Python = Join-Path $VenvPath "Scripts\python.exe"

    & $Python -m pip install --upgrade pip wheel setuptools
    if ($LASTEXITCODE -ne 0) { throw "Failed to upgrade pip" }

    Write-Host "  Installing PyTorch (CUDA 12.8)..."
    & $Python -m pip install torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 `
        --index-url https://download.pytorch.org/whl/cu128
    if ($LASTEXITCODE -ne 0) { throw "Failed to install PyTorch" }

    Write-Host "  Installing torchcodec..."
    & $Python -m pip install torchcodec==0.7.0
    if ($LASTEXITCODE -ne 0) { throw "Failed to install torchcodec" }

    Write-Host "  Installing Transcritorio package..."
    & $Python -m pip install "$RepoRoot"
    if ($LASTEXITCODE -ne 0) { throw "Failed to install Transcritorio" }

    Write-Host "  Installing PyInstaller..."
    & $Python -m pip install "pyinstaller>=6.0"
    if ($LASTEXITCODE -ne 0) { throw "Failed to install PyInstaller" }

    Write-Host "  Venv ready." -ForegroundColor Green
} else {
    Write-Host "--- [1/7] Skipping venv (--SkipVenv) ---" -ForegroundColor DarkGray
    $Python = Join-Path $VenvPath "Scripts\python.exe"
}

# Always reinstall the package to pick up source code changes
Write-Host "--- Updating package from source ---" -ForegroundColor Yellow
& $Python -m pip install --no-deps --force-reinstall "$RepoRoot" 2>&1 | Select-Object -Last 3
if ($LASTEXITCODE -ne 0) { throw "Failed to update package from source" }

# -----------------------------------------------------------------------
# Step 2: Download FFmpeg
# -----------------------------------------------------------------------
if (-not $SkipFfmpeg) {
    Write-Host "--- [2/7] Downloading FFmpeg ---" -ForegroundColor Yellow

    $FfmpegUrl    = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-$FfmpegBuild.zip"
    $FfmpegTarget  = Join-Path $VendorDir "ffmpeg"
    # Extract to TEMP (outside Dropbox) to avoid file-locking issues
    $TempDir       = Join-Path $env:TEMP "transcritorio-ffmpeg-build"
    $FfmpegZip     = Join-Path $TempDir "ffmpeg.zip"
    $FfmpegExtract = Join-Path $TempDir "ffmpeg-extract"

    New-Item -ItemType Directory -Path $VendorDir -Force | Out-Null
    New-Item -ItemType Directory -Path $TempDir -Force | Out-Null
    if (Test-Path $FfmpegTarget) { Remove-Item $FfmpegTarget -Recurse -Force }

    Write-Host "  Downloading $FfmpegUrl ..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $FfmpegUrl -OutFile $FfmpegZip -UseBasicParsing
    if (-not (Test-Path $FfmpegZip)) { throw "FFmpeg download failed" }

    Write-Host "  Extracting..."
    Expand-Archive -Path $FfmpegZip -DestinationPath $FfmpegExtract -Force
    $ExtractedDir = Get-ChildItem $FfmpegExtract -Directory | Select-Object -First 1
    # Copy (not Move) to Dropbox to avoid locking conflicts
    Copy-Item $ExtractedDir.FullName $FfmpegTarget -Recurse -Force

    Remove-Item $TempDir -Recurse -Force -ErrorAction SilentlyContinue

    # Verify
    $FfmpegExe = Join-Path $FfmpegTarget "bin\ffmpeg.exe"
    if (Test-Path $FfmpegExe) {
        Write-Host "  FFmpeg staged at $FfmpegTarget" -ForegroundColor Green
    } else {
        throw "ffmpeg.exe not found after extraction"
    }
} else {
    Write-Host "--- [2/7] Skipping FFmpeg (--SkipFfmpeg) ---" -ForegroundColor DarkGray
}

# -----------------------------------------------------------------------
# Step 3: Verify whisperx entry point
# -----------------------------------------------------------------------
Write-Host "--- [3/7] Verifying whisperx ---" -ForegroundColor Yellow
& $Python -B -c "from whisperx.__main__ import cli; print('whisperx entry point OK')"
if ($LASTEXITCODE -ne 0) { throw "whisperx entry point not found" }

# -----------------------------------------------------------------------
# Step 4: Convert icon (if ImageMagick available)
# -----------------------------------------------------------------------
Write-Host "--- [4/7] Icon ---" -ForegroundColor Yellow
$IcoPath = Join-Path $RepoRoot "assets\transcritorio_icon.ico"
if (Test-Path $IcoPath) {
    Write-Host "  ICO already exists: $IcoPath" -ForegroundColor Green
} elseif (Get-Command magick -ErrorAction SilentlyContinue) {
    $SvgPath = Join-Path $RepoRoot "assets\transcritorio_icon.svg"
    magick convert $SvgPath -define icon:auto-resize=256,128,64,48,32,16 $IcoPath
    Write-Host "  ICO created: $IcoPath" -ForegroundColor Green
} else {
    Write-Warning "  ImageMagick not found. Create $IcoPath manually for exe/installer icons."
}

# -----------------------------------------------------------------------
# Step 5: Run PyInstaller
# -----------------------------------------------------------------------
Write-Host "--- [5/7] Running PyInstaller ---" -ForegroundColor Yellow
$PyInstaller = Join-Path $VenvPath "Scripts\pyinstaller.exe"
& $PyInstaller `
    --distpath $DistDir `
    --workpath $BuildDir `
    --clean `
    --noconfirm `
    (Join-Path $PackagingDir "transcritorio.spec")

if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit code $LASTEXITCODE)" }

# -----------------------------------------------------------------------
# Step 6: Verify output
# -----------------------------------------------------------------------
Write-Host "--- [6/7] Verifying build output ---" -ForegroundColor Yellow
$OutputDir = Join-Path $DistDir "Transcritorio"

$RequiredFiles = @(
    "Transcritorio.exe",
    "transcritorio-cli.exe",
    "whisperx.exe"
)

# FFmpeg is optional at this check (may be in vendor/ffmpeg/bin/)
$FfmpegInBundle = Join-Path $OutputDir "vendor\ffmpeg\bin\ffmpeg.exe"
if (Test-Path $FfmpegInBundle) {
    Write-Host "  OK: vendor\ffmpeg\bin\ffmpeg.exe" -ForegroundColor Green
} else {
    Write-Warning "  FFmpeg not found in bundle. Verify packaging/vendor/ffmpeg/ was staged."
}

$AllPresent = $true
foreach ($file in $RequiredFiles) {
    $FullPath = Join-Path $OutputDir $file
    if (Test-Path $FullPath) {
        Write-Host "  OK: $file" -ForegroundColor Green
    } else {
        Write-Host "  MISSING: $file" -ForegroundColor Red
        $AllPresent = $false
    }
}

$SizeGB = [math]::Round(
    (Get-ChildItem $OutputDir -Recurse | Measure-Object Length -Sum).Sum / 1GB, 2
)
Write-Host "  Bundle size: $SizeGB GB"

if (-not $AllPresent) { throw "Build incomplete - required executables missing" }
Write-Host "  Build verified." -ForegroundColor Green

# -----------------------------------------------------------------------
# Step 7: Build Inno Setup installer (optional)
# -----------------------------------------------------------------------
if (-not $SkipInstaller) {
    Write-Host "--- [7/7] Building installer ---" -ForegroundColor Yellow

    $IssFile = Join-Path $PackagingDir "transcritorio.iss"
    $Iscc = $null

    # Search common Inno Setup locations
    foreach ($candidate in @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
    )) {
        if ($candidate -and (Test-Path $candidate)) {
            $Iscc = $candidate
            break
        }
    }

    if ($Iscc) {
        & $Iscc $IssFile
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
        Write-Host "  Installer built." -ForegroundColor Green
    } else {
        Write-Warning "  Inno Setup (ISCC.exe) not found. Install Inno Setup 6 to build the installer."
    }
} else {
    Write-Host "--- [7/7] Skipping installer (--SkipInstaller) ---" -ForegroundColor DarkGray
}

# -----------------------------------------------------------------------
# Done
# -----------------------------------------------------------------------
Write-Host ""
Write-Host "=== Build complete ===" -ForegroundColor Cyan
Write-Host "  Output directory: $OutputDir"
Write-Host "  Bundle size:      $SizeGB GB"
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Test: $OutputDir\Transcritorio.exe"
Write-Host "  2. Test: $OutputDir\transcritorio-cli.exe models status"
Write-Host "  3. Test: $OutputDir\whisperx.exe --help"
