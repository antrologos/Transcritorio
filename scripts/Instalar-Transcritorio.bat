@echo off
setlocal
chcp 65001 >nul
title Instalador do Transcritório
rem ============================================================================
rem  Instalador do Transcritório
rem  https://github.com/antrologos/Transcritorio
rem
rem  SEGURANÇA (por que este script é confiável):
rem   - instala APENAS de fontes oficiais e assinadas: o uv (Astral) e o
rem     FFmpeg (Gyan.dev) chegam pelo winget da própria Microsoft; o
rem     Transcritório e as dependências vêm do PyPI (pypi.org), o
rem     repositório público de pacotes Python, com versões travadas;
rem   - NÃO pede senha de administrador e não grava nada fora do seu
rem     perfil de usuário;
rem   - não baixa executáveis de sites avulsos, não usa http sem
rem     criptografia e não contém nenhuma senha ou chave;
rem   - todo o script está comentado em português — leia à vontade.
rem ============================================================================

echo.
echo  =============================================
echo    Instalador do Transcritório
echo  =============================================
echo.
echo  A instalação leva alguns minutos (download de ~2,5 GB).
echo  Pode deixar esta janela trabalhando — não feche.
echo.

rem -- 0) winget (gerenciador de pacotes da Microsoft; vem no Windows 10/11) --
where winget >nul 2>nul
if errorlevel 1 goto SEM_WINGET

rem -- 1) uv: baixa o Python oficial e gerencia o aplicativo -------------------
rem    (nao checamos o errorlevel do winget: "ja instalado" tambem sai
rem     com codigo diferente de zero; a prova real e o executavel existir)
echo  [1/3] Instalando o uv (gerenciador, assinado pela Astral)...
winget install -e --id astral-sh.uv --accept-source-agreements --accept-package-agreements --disable-interactivity >nul 2>nul
echo         concluído.

rem -- 2) FFmpeg: leitura dos arquivos de áudio e vídeo ------------------------
echo  [2/3] Instalando o FFmpeg (leitura de áudio/vídeo)...
winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements --disable-interactivity >nul 2>nul
set "FFMPEG_OK="
where ffmpeg >nul 2>nul && set "FFMPEG_OK=1"
if not defined FFMPEG_OK if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe" set "FFMPEG_OK=1"
if not defined FFMPEG_OK (
    for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*") do (
        for /d %%B in ("%%D\ffmpeg-*") do if exist "%%B\bin\ffmpeg.exe" set "FFMPEG_OK=1"
    )
)
if defined FFMPEG_OK (
    echo         concluído.
) else (
    echo         [!] O FFmpeg não foi confirmado — verifique a internet e
    echo             rode este instalador de novo; o aplicativo também avisa
    echo             e orienta se ele faltar.
)

rem -- Localizar o uv SEM depender do PATH desta janela (recém-instalado
rem    ainda não aparece aqui; o winget guarda um atalho fixo em Links) -------
set "UV_EXE="
for /f "delims=" %%i in ('where uv 2^>nul') do if not defined UV_EXE set "UV_EXE=%%i"
if not defined UV_EXE if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe" set "UV_EXE=%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe"
if not defined UV_EXE goto ERRO_UV

rem -- 3) O Transcritório em si (do PyPI; atualiza se já estiver instalado) ---
"%UV_EXE%" tool list 2>nul | findstr /b /c:"transcritorio " >nul
if not errorlevel 1 (
    echo  [3/3] O Transcritório já está instalado — atualizando...
    echo         ^(se o Transcritório estiver ABERTO agora, feche-o antes:
    echo          uma janela aberta impede a troca dos arquivos^)
    "%UV_EXE%" tool upgrade transcritorio
) else (
    echo  [3/3] Baixando o Transcritório e as dependências ^(PyPI^)...
    "%UV_EXE%" tool install transcritorio
)
if errorlevel 1 goto ERRO_REDE

rem -- Abrir o aplicativo (o 1º uso cria o atalho na área de trabalho) --------
rem    O PATH desta janela é anterior ao winget: estender a sessão para o
rem    app recém-aberto enxergar o FFmpeg (registro só vale em janelas novas).
set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Links"
for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*") do (
    for /d %%B in ("%%D\ffmpeg-*") do if exist "%%B\bin\ffmpeg.exe" set "PATH=%PATH%;%%B\bin"
)
set "APP_EXE=%APPDATA%\uv\tools\transcritorio\Scripts\transcritorio.exe"
if not exist "%APP_EXE%" set "APP_EXE=%USERPROFILE%\.local\bin\transcritorio.exe"
echo.
echo  =============================================
echo   Pronto! Abrindo o Transcritório...
echo  =============================================
echo.
echo  No primeiro uso, o assistente baixa os modelos de transcrição e o
echo  atalho "Transcritório" aparece na sua área de trabalho.
echo.
if exist "%APP_EXE%" (
    start "" "%APP_EXE%"
) else (
    echo  Para abrir: feche esta janela, abra o Prompt de Comando e digite:
    echo      transcritorio
)
goto FIM

:SEM_WINGET
echo.
echo  [!] O "winget" não foi encontrado neste Windows.
echo      Instale o "App Installer" pela Microsoft Store (é gratuito e
echo      oficial da Microsoft) e rode este instalador de novo:
echo      https://apps.microsoft.com/detail/9NBLGGH4NNS1
echo.
echo      Alternativa manual, sem winget:
echo      https://github.com/antrologos/Transcritorio/blob/main/docs/INSTALL_WINDOWS.md
goto FIM_ERRO

:ERRO_UV
echo.
echo  [!] O uv não foi encontrado depois da instalação.
echo      Feche esta janela, abra de novo o instalador e tente outra vez.
echo      Se repetir, siga o guia passo a passo:
echo      https://github.com/antrologos/Transcritorio/blob/main/docs/INSTALL_WINDOWS.md
goto FIM_ERRO

:ERRO_REDE
echo.
echo  [!] O download ou a instalação do Transcritório falhou. Causas comuns:
echo      - O Transcritório estava ABERTO durante uma atualização: feche a
echo        janela dele e rode este instalador de novo.
echo      - Rede de universidade/empresa: peça à TI para liberar
echo        pypi.org, files.pythonhosted.org, astral.sh e huggingface.co
echo        — ou tente numa rede doméstica. Guia completo:
echo      https://github.com/antrologos/Transcritorio/blob/main/docs/INSTALL_WINDOWS.md
goto FIM_ERRO

:FIM_ERRO
echo.
echo  Nada foi quebrado: você pode rodar este instalador quantas vezes quiser.
:FIM
echo.
pause
endlocal
