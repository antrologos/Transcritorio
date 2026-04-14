@echo off
set PYTHONDONTWRITEBYTECODE=1

rem --- Software root is the parent of the scripts/ directory ---
set "TRANSCRITORIO_ROOT=%~dp0.."

for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\BtbN.FFmpeg.GPL.Shared.7.1_*") do (
  for /d %%B in ("%%D\ffmpeg-*shared-7.1\bin") do (
    if exist "%%B\ffmpeg.exe" set "PATH=%%B;%PATH%"
  )
)
for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg.Shared_*") do (
  for /d %%B in ("%%D\ffmpeg-*shared\bin") do (
    if exist "%%B\ffmpeg.exe" if not exist "%LOCALAPPDATA%\Microsoft\WinGet\Packages\BtbN.FFmpeg.GPL.Shared.7.1_*" set "PATH=%%B;%PATH%"
  )
)
set "TRANSCRITORIO_HOME=%LOCALAPPDATA%\Transcritorio"
if "%TRANSCRICAO_VENV%"=="" (
  set "TRANSCRICAO_VENV=%TRANSCRITORIO_HOME%\transcricao-venv"
)
set "PYTHONPATH=%TRANSCRITORIO_ROOT%\scripts\python_sitecustomize;%TRANSCRITORIO_ROOT%;%PYTHONPATH%"
if exist "%TRANSCRICAO_VENV%\Scripts\python.exe" (
  set "PATH=%TRANSCRICAO_VENV%\Scripts;%PATH%"
  "%TRANSCRICAO_VENV%\Scripts\python.exe" -B -m transcribe_pipeline %*
) else (
  python -B -m transcribe_pipeline %*
)
set EXITCODE=%ERRORLEVEL%
exit /b %EXITCODE%
