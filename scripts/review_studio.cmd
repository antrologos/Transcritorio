@echo off
setlocal
set PYTHONDONTWRITEBYTECODE=1

rem --- Software root is the parent of the scripts/ directory ---
set "TRANSCRITORIO_ROOT=%~dp0.."

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
if "%TRANSCRICAO_VENV%"=="" (
  set "TRANSCRICAO_VENV=%TRANSCRITORIO_HOME%\transcricao-venv"
)
rem --- Ambiente do canal atual (uv tool install transcritorio); o venv
rem     legado so existe em maquinas anteriores a v0.2. Em qualquer um dos
rem     dois o PYTHONPATH acima faz valer o CODIGO DESTA PASTA, nao o
rem     instalado — e por isso que este script serve para testar mudancas
rem     ainda nao publicadas.
set "UV_TOOL_PY=%APPDATA%\uv\tools\transcritorio\Scripts\python.exe"
set "PYTHONPATH=%TRANSCRITORIO_ROOT%\scripts\python_sitecustomize;%TRANSCRITORIO_ROOT%;%PYTHONPATH%"
if exist "%TRANSCRICAO_VENV%\Scripts\python.exe" (
  set "PATH=%TRANSCRICAO_VENV%\Scripts;%PATH%"
  "%TRANSCRICAO_VENV%\Scripts\python.exe" -B -m transcribe_pipeline.review_studio_qt %*
) else if exist "%UV_TOOL_PY%" (
  set "PATH=%APPDATA%\uv\tools\transcritorio\Scripts;%PATH%"
  "%UV_TOOL_PY%" -B -m transcribe_pipeline.review_studio_qt %*
) else (
  python -B -m transcribe_pipeline.review_studio_qt %*
)
set EXITCODE=%ERRORLEVEL%
endlocal & exit /b %EXITCODE%
