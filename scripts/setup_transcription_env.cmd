@echo off
setlocal
set PYTHONDONTWRITEBYTECODE=1
set PIP_NO_COMPILE=1
pushd "%~dp0.."

set "FOUND_BTBN_FFMPEG="
for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\BtbN.FFmpeg.GPL.Shared.7.1_*") do (
  for /d %%B in ("%%D\ffmpeg-*shared-7.1\bin") do (
    if exist "%%B\ffmpeg.exe" (
      set "FOUND_BTBN_FFMPEG=1"
      set "PATH=%%B;%PATH%"
    )
  )
)
for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg.Shared_*") do (
  for /d %%B in ("%%D\ffmpeg-*shared\bin") do (
    if exist "%%B\ffmpeg.exe" if not defined FOUND_BTBN_FFMPEG set "PATH=%%B;%PATH%"
  )
)

set "TRANSCRITORIO_HOME=%LOCALAPPDATA%\Transcritorio"
if "%TRANSCRICAO_VENV%"=="" set "TRANSCRICAO_VENV=%TRANSCRITORIO_HOME%\transcricao-venv"
if not exist "%TRANSCRITORIO_HOME%" mkdir "%TRANSCRITORIO_HOME%"
set "PYTHONPATH=%CD%\scripts\python_sitecustomize;%PYTHONPATH%"

py -3.13 -m venv "%TRANSCRICAO_VENV%"
if errorlevel 1 (
  py -3.12 -m venv "%TRANSCRICAO_VENV%"
)
if errorlevel 1 (
  echo Could not create %TRANSCRICAO_VENV% with Python 3.13 or 3.12.
  exit /b 1
)

"%TRANSCRICAO_VENV%\Scripts\python.exe" -m ensurepip --upgrade
if errorlevel 1 exit /b 1

"%TRANSCRICAO_VENV%\Scripts\python.exe" -m pip install --upgrade pip wheel setuptools
if errorlevel 1 exit /b 1

"%TRANSCRICAO_VENV%\Scripts\python.exe" -m pip install torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 exit /b 1

"%TRANSCRICAO_VENV%\Scripts\python.exe" -m pip install whisperx==3.8.5 python-docx jiwer PySide6==6.11.0
if errorlevel 1 exit /b 1

echo.
echo Environment created at %TRANSCRICAO_VENV%.
echo Install FFmpeg shared separately and put ffmpeg/ffprobe on PATH.
echo Baixe os modelos com o token Hugging Face do proprio usuario:
echo   scripts\transcribe.cmd models download
echo   set PYANNOTE_METRICS_ENABLED=0

popd
endlocal
