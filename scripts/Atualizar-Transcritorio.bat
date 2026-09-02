@echo off
setlocal
chcp 65001 >nul
title Atualizador do Transcritório
rem ============================================================================
rem  Atualiza o Transcritório para a versão mais recente do PyPI.
rem  Mesmas garantias do instalador: só fontes oficiais (PyPI), sem senha
rem  de administrador, nada fora do perfil do usuário. Projetos, áudios e
rem  modelos baixados não são tocados.
rem ============================================================================

echo.
echo  =============================================
echo    Atualizador do Transcritório
echo  =============================================
echo.

rem O winget às vezes não cria o atalho em Links (App Installer antigo,
rem política de link simbólico): procurar também dentro do próprio pacote.
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
if not defined UV_EXE goto ERRO_UV

rem O Transcritório foi mesmo instalado nesta máquina?
"%UV_EXE%" tool list 2>nul | findstr /b /c:"transcritorio " >nul
if errorlevel 1 goto ERRO_UV

echo  IMPORTANTE: se o Transcritório estiver aberto agora, feche a janela
echo  dele antes de continuar (uma janela aberta impede a atualização).
echo.
pause
echo.
echo  Procurando e instalando a versão mais nova...
echo.
rem Mesma blindagem do instalador: tempo limite maior para conexão lenta
rem e uma 2ª tentativa com registro (o uv retoma do que já baixou).
set "UV_HTTP_TIMEOUT=600"
set "LOG=%TEMP%\Transcritorio-atualizador.log"
"%UV_EXE%" tool upgrade transcritorio
if not errorlevel 1 goto ATUALIZADO
echo.
echo  A primeira tentativa falhou ^(falhas de rede são comuns^). Tentando
echo  de novo — desta vez sem barra de progresso, gravando o registro em:
echo      %LOG%
"%UV_EXE%" tool upgrade transcritorio > "%LOG%" 2>&1
if errorlevel 1 goto ERRO_REDE
:ATUALIZADO

echo.
echo  Pronto! Na próxima vez que abrir, o Transcritório já é o novo.
echo  (Se ele estiver aberto agora, feche e abra de novo.)
goto FIM

:ERRO_UV
echo.
echo  [!] O uv não foi encontrado — o Transcritório foi instalado nesta
echo      máquina? Rode primeiro o Instalar-Transcritorio.bat:
echo      https://github.com/antrologos/Transcritorio/blob/main/docs/INSTALL_WINDOWS.md
goto FIM

:ERRO_REDE
echo.
echo  [!] A atualização falhou.
if exist "%LOG%" (
    echo      Últimas linhas do registro ^(a causa costuma estar aqui^):
    echo      ----------------------------------------------------------
    powershell -NoProfile -Command "Get-Content -LiteralPath '%LOG%' -Tail 12 -Encoding UTF8"
    echo      ----------------------------------------------------------
    echo      Registro completo: %LOG%
    echo.
)
echo      Causas comuns:
echo      - O Transcritório ainda está ABERTO: feche a janela dele e rode
echo        este atualizador de novo.
echo      - Rede de universidade/empresa bloqueando downloads: peça à TI
echo        para liberar pypi.org, files.pythonhosted.org e github.com —
echo        ou tente em casa / no hotspot do celular.
echo      - Conexão lenta: rode de novo ^(continua de onde parou^).
echo      Nada foi quebrado: a versão atual continua funcionando.
:FIM
echo.
pause
endlocal
