<#
.SYNOPSIS
    Build Transcritorio standalone distribution for Windows.

.DESCRIPTION
    Copies source to a temporary directory OUTSIDE Dropbox, stamps the build,
    installs into the build venv, runs PyInstaller, verifies the result, and
    optionally generates an Inno Setup installer. All temporary files are
    cleaned up at the end.

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

$RepoRoot     = (Resolve-Path "$PSScriptRoot\..").Path
$PackagingDir = Join-Path $RepoRoot "packaging"
$VendorDir    = Join-Path $PackagingDir "vendor"

# ALL build work happens OUTSIDE Dropbox to avoid file-locking.
$TempBuild    = Join-Path $env:TEMP "transcritorio-build-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
$DistDir      = Join-Path $TempBuild "dist"
$WorkDir      = Join-Path $TempBuild "work"
$SourceCopy   = Join-Path $TempBuild "source"
$InstallerDir = Join-Path $TempBuild "installer"

# Final output (persisted outside Dropbox)
$FinalDist    = Join-Path $env:LOCALAPPDATA "Transcritorio\packaging\dist"
$FinalInstaller = Join-Path $env:LOCALAPPDATA "Transcritorio\packaging\installer"

Write-Host "=== Transcritorio Build ===" -ForegroundColor Cyan
Write-Host "  Source:   $RepoRoot"
Write-Host "  Venv:     $VenvPath"
Write-Host "  Temp:     $TempBuild"
Write-Host "  Output:   $FinalDist"
Write-Host ""

# -----------------------------------------------------------------------
# Step 1: Build venv (only if not skipped)
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
    & $Python -m pip install torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
    if ($LASTEXITCODE -ne 0) { throw "Failed to install PyTorch" }
    Write-Host "  Installing torchcodec..."
    & $Python -m pip install torchcodec==0.7.0
    if ($LASTEXITCODE -ne 0) { throw "Failed to install torchcodec" }
    Write-Host "  Installing Transcritorio + PyInstaller..."
    & $Python -m pip install "$RepoRoot" "pyinstaller>=6.0"
    if ($LASTEXITCODE -ne 0) { throw "Failed to install Transcritorio" }
    Write-Host "  Venv ready." -ForegroundColor Green
} else {
    Write-Host "--- [1/7] Skipping venv (--SkipVenv) ---" -ForegroundColor DarkGray
    $Python = Join-Path $VenvPath "Scripts\python.exe"
}

# -----------------------------------------------------------------------
# Step 2: Copy source to temp dir + stamp + install (OUTSIDE DROPBOX)
# -----------------------------------------------------------------------
Write-Host "--- [2/7] Copying source and stamping build ---" -ForegroundColor Yellow

# Clean slate — SourceCopy IS the temp root (same directory depth as the repo)
if (Test-Path $TempBuild) { Remove-Item $TempBuild -Recurse -Force }
New-Item -ItemType Directory -Path $SourceCopy -Force | Out-Null

# Copy source (only Python package + packaging files, skip heavy dirs)
Copy-Item (Join-Path $RepoRoot "transcribe_pipeline") (Join-Path $SourceCopy "transcribe_pipeline") -Recurse
Copy-Item (Join-Path $RepoRoot "packaging") (Join-Path $SourceCopy "packaging") -Recurse
Copy-Item (Join-Path $RepoRoot "assets") (Join-Path $SourceCopy "assets") -Recurse
Copy-Item (Join-Path $RepoRoot "pyproject.toml") $SourceCopy
Copy-Item (Join-Path $RepoRoot "README.md") $SourceCopy -ErrorAction SilentlyContinue

# Stamp build timestamp in the COPY (not the Dropbox original)
$StampScript = Join-Path $SourceCopy "packaging\stamp_build.py"
$BuildTimestamp = (& $Python -B $StampScript stamp).Trim()
Write-Host "  Build stamp: $BuildTimestamp"

# Copy FFmpeg vendor if it exists (from Dropbox staging)
$FfmpegVendor = Join-Path $VendorDir "ffmpeg"
if (Test-Path $FfmpegVendor) {
    Copy-Item $FfmpegVendor (Join-Path $SourceCopy "packaging\vendor\ffmpeg") -Recurse -Force
    Write-Host "  FFmpeg vendor copied."
}

# Install package from the TEMP COPY
& $Python -m pip install --force-reinstall --no-cache-dir --no-deps "$SourceCopy" 2>&1 | Select-Object -Last 5
if ($LASTEXITCODE -ne 0) { throw "Failed to install package from temp copy" }

# Overwrite __init__.py in site-packages with the stamped version
# (pip's wheel build process uses its own temp dir and ignores our stamp)
# Stamp __init__.py in site-packages.
# Use stamp_build.py pointed at the site-packages copy (not the source copy).
Push-Location $env:TEMP
$SitePkgInit = (& $Python -B -c "import transcribe_pipeline; print(transcribe_pipeline.__file__)").Trim()
$SitePkgDir = Split-Path $SitePkgInit
# Copy the already-stamped __init__.py from source copy to site-packages
$StampedInit = Join-Path $SourceCopy "transcribe_pipeline" "__init__.py"
Write-Host "  Copying $StampedInit -> $SitePkgInit"
& $Python -B -c "import shutil; shutil.copy2(r'$StampedInit', r'$SitePkgInit'); print('  Copy OK')"
# Delete pycache
& $Python -B -c "import shutil, pathlib; c=pathlib.Path(r'$SitePkgDir')/'__pycache__'; shutil.rmtree(str(c)) if c.exists() else None"

# Verify using a FRESH Python process
$InstalledBuild = (& $Python -B -c "import transcribe_pipeline; print(transcribe_pipeline.__build__)").Trim()
Pop-Location
if ($InstalledBuild -ne $BuildTimestamp) {
    throw "FATAL: Installed build='$InstalledBuild' but expected '$BuildTimestamp'. Build pipeline broken!"
}
Write-Host "  Package verified: build=$InstalledBuild" -ForegroundColor Green

# Nuke PyInstaller global cache
$PyInstallerCache = Join-Path $env:LOCALAPPDATA "pyinstaller"
if (Test-Path $PyInstallerCache) { Remove-Item $PyInstallerCache -Recurse -Force }
Write-Host "  PyInstaller cache cleared."

# -----------------------------------------------------------------------
# Step 3: Download FFmpeg (if needed)
# -----------------------------------------------------------------------
if (-not $SkipFfmpeg) {
    Write-Host "--- [3/7] Downloading FFmpeg ---" -ForegroundColor Yellow
    $FfmpegUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-$FfmpegBuild.zip"
    $FfmpegTarget = Join-Path $SourceCopy "packaging\vendor\ffmpeg"
    $FfmpegZip = Join-Path $TempBuild "ffmpeg.zip"
    $FfmpegExtract = Join-Path $TempBuild "ffmpeg-extract"
    if (Test-Path $FfmpegTarget) { Remove-Item $FfmpegTarget -Recurse -Force }
    Write-Host "  Downloading..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $FfmpegUrl -OutFile $FfmpegZip -UseBasicParsing
    if (-not (Test-Path $FfmpegZip)) { throw "FFmpeg download failed" }
    Expand-Archive -Path $FfmpegZip -DestinationPath $FfmpegExtract -Force
    $ExtractedDir = Get-ChildItem $FfmpegExtract -Directory | Select-Object -First 1
    Copy-Item $ExtractedDir.FullName $FfmpegTarget -Recurse -Force
    Write-Host "  FFmpeg ready." -ForegroundColor Green
    # Also update Dropbox staging copy
    if (-not (Test-Path $VendorDir)) { New-Item -ItemType Directory -Path $VendorDir -Force | Out-Null }
    Copy-Item $FfmpegTarget (Join-Path $VendorDir "ffmpeg") -Recurse -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "--- [3/7] Skipping FFmpeg ---" -ForegroundColor DarkGray
}

# -----------------------------------------------------------------------
# Step 4: Verify whisperx + icon
# -----------------------------------------------------------------------
Write-Host "--- [4/7] Verifying prerequisites ---" -ForegroundColor Yellow
& $Python -B -c "from whisperx.__main__ import cli; print('  whisperx OK')"
if ($LASTEXITCODE -ne 0) { throw "whisperx entry point not found" }
$IcoPath = Join-Path $SourceCopy "assets\transcritorio_icon.ico"
if (Test-Path $IcoPath) {
    Write-Host "  ICO: $IcoPath" -ForegroundColor Green
} else {
    Write-Warning "  ICO not found. Exe will have default icon."
}

# -----------------------------------------------------------------------
# Step 5: Run PyInstaller (from temp copy, output to temp)
# -----------------------------------------------------------------------
Write-Host "--- [5/7] Running PyInstaller ---" -ForegroundColor Yellow
$SpecFile = Join-Path $SourceCopy "packaging\transcritorio.spec"
$PyInstaller = Join-Path $VenvPath "Scripts\pyinstaller.exe"
& $PyInstaller --distpath $DistDir --workpath $WorkDir --clean --noconfirm $SpecFile
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit code $LASTEXITCODE)" }

# -----------------------------------------------------------------------
# Step 6: Verify output
# -----------------------------------------------------------------------
Write-Host "--- [6/7] Verifying build ---" -ForegroundColor Yellow
$OutputDir = Join-Path $DistDir "Transcritorio"

foreach ($file in @("Transcritorio.exe", "transcritorio-cli.exe", "whisperx.exe")) {
    $p = Join-Path $OutputDir $file
    if (Test-Path $p) { Write-Host "  OK: $file" -ForegroundColor Green }
    else { throw "MISSING: $file" }
}

# Copy FFmpeg into bundle if not already there
$FfmpegInBundle = Join-Path $OutputDir "vendor\ffmpeg\bin\ffmpeg.exe"
if (-not (Test-Path $FfmpegInBundle)) {
    $FfmpegSource = Join-Path $SourceCopy "packaging\vendor\ffmpeg\bin"
    if (Test-Path $FfmpegSource) {
        $FfmpegDest = Join-Path $OutputDir "vendor\ffmpeg\bin"
        New-Item -ItemType Directory -Path $FfmpegDest -Force | Out-Null
        Copy-Item "$FfmpegSource\*" $FfmpegDest -Force
        Write-Host "  FFmpeg copied into bundle." -ForegroundColor Green
    }
}

# Verify CLI runs
$null = & (Join-Path $OutputDir "transcritorio-cli.exe") --help 2>&1
if ($LASTEXITCODE -ne 0) { throw "FATAL: CLI exe doesn't run!" }
Write-Host "  CLI verified." -ForegroundColor Green

$SizeGB = [math]::Round((Get-ChildItem $OutputDir -Recurse | Measure-Object Length -Sum).Sum / 1GB, 2)
Write-Host "  Bundle size: $SizeGB GB"

# -----------------------------------------------------------------------
# Step 7: Copy results to final location + optional installer
# -----------------------------------------------------------------------
Write-Host "--- [7/7] Finalizing ---" -ForegroundColor Yellow

# Move bundle to final persistent location
if (Test-Path $FinalDist) { Remove-Item $FinalDist -Recurse -Force }
New-Item -ItemType Directory -Path (Split-Path $FinalDist) -Force | Out-Null
Move-Item $OutputDir $FinalDist
Write-Host "  Bundle: $FinalDist" -ForegroundColor Green

# Build Inno Setup installer
if (-not $SkipInstaller) {
    $IssFile = Join-Path $SourceCopy "packaging\transcritorio.iss"
    $Iscc = $null
    foreach ($c in @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )) { if (Test-Path $c) { $Iscc = $c; break } }

    if ($Iscc) {
        if (Test-Path $FinalInstaller) { Remove-Item $FinalInstaller -Recurse -Force }
        New-Item -ItemType Directory -Path $FinalInstaller -Force | Out-Null
        & $Iscc "/DBundleDir=$FinalDist" "/O$FinalInstaller" $IssFile
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
        Write-Host "  Installer: $FinalInstaller" -ForegroundColor Green
    } else {
        Write-Warning "  Inno Setup not found. Install it to build the installer."
    }
}

# Clean up temp directory
Remove-Item $TempBuild -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "  Temp cleaned up."

# -----------------------------------------------------------------------
# Done
# -----------------------------------------------------------------------
Write-Host ""
Write-Host "=== Build complete ===" -ForegroundColor Cyan
Write-Host "  Bundle:    $FinalDist\Transcritorio.exe"
Write-Host "  Size:      $SizeGB GB"
Write-Host "  Build:     $BuildTimestamp"
if (Test-Path $FinalInstaller) {
    Write-Host "  Installer: $FinalInstaller"
}
