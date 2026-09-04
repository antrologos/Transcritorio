@echo off
rem =============================================================================
rem  Abre a VERSÃO DE TESTE (beta) do Transcritório.
rem
rem  TRANSCRITORIO_CHANNEL faz duas coisas: separa a instância única (a beta e
rem  a estável podem ficar abertas ao mesmo tempo, em vez de um atalho apenas
rem  acordar a janela da outra) e marca "versão de teste" no título da janela.
rem
rem  Instalado por scripts\Instalar-Beta.bat em
rem  %LOCALAPPDATA%\Transcritorio\Transcritorio-Beta.cmd
rem =============================================================================
setlocal
set "TRANSCRITORIO_CHANNEL=beta"
set "BETA_EXE=%LOCALAPPDATA%\Transcritorio\beta-venv\Scripts\transcritorio.exe"

rem FFmpeg do winget (o registro do PATH só vale em janelas novas)
set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Links"
for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*") do (
    for /d %%B in ("%%D\ffmpeg-*") do if exist "%%B\bin\ffmpeg.exe" set "PATH=%PATH%;%%B\bin"
)
for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\BtbN.FFmpeg*") do (
    for /d %%B in ("%%D\ffmpeg-*") do if exist "%%B\bin\ffmpeg.exe" set "PATH=%PATH%;%%B\bin"
)

if not exist "%BETA_EXE%" (
    echo  A versao de teste nao esta instalada neste computador.
    echo  Rode primeiro: scripts\Instalar-Beta.bat
    echo.
    pause
    exit /b 1
)
start "" "%BETA_EXE%" %*
endlocal
