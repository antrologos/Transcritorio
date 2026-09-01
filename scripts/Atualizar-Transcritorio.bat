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

set "UV_EXE="
for /f "delims=" %%i in ('where uv 2^>nul') do if not defined UV_EXE set "UV_EXE=%%i"
if not defined UV_EXE if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe" set "UV_EXE=%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe"
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
"%UV_EXE%" tool upgrade transcritorio
if errorlevel 1 goto ERRO_REDE

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
echo  [!] A atualização falhou. Causas comuns:
echo      - O Transcritório ainda está ABERTO: feche a janela dele e rode
echo        este atualizador de novo.
echo      - Rede de universidade/empresa: peça à TI para liberar pypi.org
echo        e files.pythonhosted.org — ou tente numa rede doméstica.
echo      Nada foi quebrado: a versão atual continua funcionando.
:FIM
echo.
pause
endlocal
