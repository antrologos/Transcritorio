@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title Transcritório — instalar a versão de teste (beta)

rem =============================================================================
rem  Instala a VERSÃO DE TESTE do Transcritório, LADO A LADO com a estável.
rem
rem  Por que um instalador separado: `uv tool install transcritorio` tem um
rem  nome só e substituiria a versão que você usa no dia a dia. A beta vai
rem  para um ambiente próprio, e as duas podem ficar abertas ao mesmo tempo
rem  (o lançador marca o canal, e a janela da beta diz isso no título).
rem
rem  O que é COMPARTILHADO com a estável, de propósito: os modelos baixados
rem  (são muitos GB — baixar de novo seria absurdo), as preferências e a
rem  lista de projetos recentes. Os seus projetos não são tocados.
rem
rem  Origem do código: a pasta deste script, quando ela é o repositório;
rem  senão, o ramo `beta` no GitHub.
rem =============================================================================

set "RAIZ=%~dp0.."
set "BETA_DIR=%LOCALAPPDATA%\Transcritorio\beta-venv"
set "LANCADOR=%LOCALAPPDATA%\Transcritorio\Transcritorio-Beta.cmd"
set "UV_HTTP_TIMEOUT=600"

echo.
echo  =========================================================
echo   Transcritório — versão de TESTE (beta)
echo  =========================================================
echo.
echo   Instala em: %BETA_DIR%
echo   A versão estável do dia a dia NÃO é alterada.
echo.

rem -- 1) uv (mesma busca do instalador oficial) ------------------------------
set "UV_EXE="
for /f "delims=" %%i in ('where uv 2^>nul') do if not defined UV_EXE set "UV_EXE=%%i"
if not defined UV_EXE if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe" set "UV_EXE=%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe"
if not defined UV_EXE (
    for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv*") do (
        for /f "delims=" %%i in ('dir /s /b "%%D\uv.exe" 2^>nul') do if not defined UV_EXE set "UV_EXE=%%i"
    )
)
if not defined UV_EXE if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV_EXE=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV_EXE if exist "%ProgramFiles%\WinGet\Links\uv.exe" set "UV_EXE=%ProgramFiles%\WinGet\Links\uv.exe"
if not defined UV_EXE (
    echo  [!] O "uv" não foi encontrado. Instale primeiro a versão estável
    echo      com o "Instalar-Transcritorio.bat" — ele traz o uv junto.
    echo.
    pause
    exit /b 1
)

rem -- 2) A beta está aberta? --------------------------------------------------
tasklist /FI "IMAGENAME eq pythonw.exe" 2>nul | find /i "pythonw.exe" >nul
if not errorlevel 1 (
    echo  [!] Há um Transcritório ABERTO neste computador.
    echo      Feche-o antes de continuar: uma janela aberta impede a troca
    echo      dos arquivos e deixa a instalação pela metade.
    echo.
    pause
    exit /b 1
)

rem -- 3) Ambiente próprio da beta --------------------------------------------
echo  [1/3] Preparando o ambiente da versão de teste...
"%UV_EXE%" venv --python 3.12 "%BETA_DIR%"
if errorlevel 1 goto ERRO

rem -- 4) O código: pasta local (repositório) ou o ramo beta no GitHub ---------
if exist "%RAIZ%\pyproject.toml" (
    echo  [2/3] Instalando o código desta pasta ^(%RAIZ%^)...
    set "ORIGEM=%RAIZ%"
) else (
    echo  [2/3] Baixando o ramo "beta" do GitHub...
    set "ORIGEM=git+https://github.com/antrologos/Transcritorio@beta"
)
"%UV_EXE%" pip install --python "%BETA_DIR%\Scripts\python.exe" "!ORIGEM!"
if errorlevel 1 goto ERRO

rem -- 5) Lançador do canal de teste ------------------------------------------
echo  [3/3] Criando o atalho da versão de teste...
> "%LANCADOR%" echo @echo off
>> "%LANCADOR%" echo rem Lancador da versao de TESTE do Transcritorio (gerado por Instalar-Beta.bat).
>> "%LANCADOR%" echo rem TRANSCRITORIO_CHANNEL separa a instancia unica da estavel e marca o titulo.
>> "%LANCADOR%" echo setlocal
>> "%LANCADOR%" echo set "TRANSCRITORIO_CHANNEL=beta"
>> "%LANCADOR%" echo set "PATH=%%LOCALAPPDATA%%\Microsoft\WinGet\Links;%%PATH%%"
>> "%LANCADOR%" echo for /d %%%%D in ("%%LOCALAPPDATA%%\Microsoft\WinGet\Packages\Gyan.FFmpeg*"^) do (
>> "%LANCADOR%" echo   for /d %%%%B in ("%%%%D\ffmpeg-*"^) do if exist "%%%%B\bin\ffmpeg.exe" set "PATH=%%PATH%%;%%%%B\bin"
>> "%LANCADOR%" echo ^)
>> "%LANCADOR%" echo start "" "%BETA_DIR%\Scripts\transcritorio.exe" %%*
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Transcritorio (beta).lnk'); $s.TargetPath='%LANCADOR%'; $s.WorkingDirectory='%LOCALAPPDATA%\Transcritorio'; $s.Description='Transcritorio - versao de teste'; $s.Save()" >nul 2>nul

echo.
echo  =========================================================
echo   Pronto! A versão de teste está instalada.
echo  =========================================================
echo.
echo   Abrir: o atalho "Transcritorio (beta)" na área de trabalho,
echo          ou %LANCADOR%
echo.
echo   A janela da beta traz "versão de teste" no título — é assim
echo   que você distingue as duas.
echo.
echo   Para remover a versão de teste, basta apagar a pasta:
echo       %BETA_DIR%
echo.
choice /C SN /N /M "  Abrir agora? [S/N] "
if errorlevel 2 goto FIM
start "" "%LANCADOR%"
goto FIM

:ERRO
echo.
echo  [!] A instalação da versão de teste falhou. Verifique a conexão e
echo      rode este arquivo de novo.
echo.
pause
exit /b 1

:FIM
endlocal
exit /b 0
