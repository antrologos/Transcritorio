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
rem    Localizar o uv SEM depender do PATH desta janela (recém-instalado
rem    só entra no PATH de janelas novas). O winget às vezes NÃO cria o
rem    atalho em Links (App Installer antigo, política de link simbólico):
rem    procurar também dentro do próprio pacote e nos locais alternativos.
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

rem -- 3) O Transcritório em si (do PyPI; atualiza se já estiver instalado) ---
rem    Conexão lenta estourava o tempo limite do uv em arquivos grandes
rem    (~200 MB): 10 min por arquivo. E como o uv guarda o que já baixou,
rem    uma 2ª tentativa é barata e resolve a maioria das falhas de rede —
rem    ela grava um registro, cujas últimas linhas aparecem no erro
rem    (caso real de beta tester: a causa rolava para fora da tela).
set "UV_HTTP_TIMEOUT=600"
set "LOG=%TEMP%\Transcritorio-instalador.log"
rem    Python FIXO em 3.12 na instalação: o uv escolhia o Python mais novo
rem    da máquina e o torchcodec ainda não tem pacote para o 3.14 — caso
rem    real de beta tester. O uv baixa o 3.12 oficial se não houver nenhum.
"%UV_EXE%" tool list 2>nul | findstr /b /c:"transcritorio " >nul
if not errorlevel 1 (
    set "UV_CMD=tool upgrade transcritorio"
    echo  [3/3] O Transcritório já está instalado — atualizando...
    echo         ^(se o Transcritório estiver ABERTO agora, feche-o antes:
    echo          uma janela aberta impede a troca dos arquivos^)
) else (
    set "UV_CMD=tool install --python 3.12 transcritorio"
    echo  [3/3] Baixando o Transcritório e as dependências ^(PyPI^)...
)
"%UV_EXE%" %UV_CMD%
if not errorlevel 1 goto INSTALADO
echo.
echo  A primeira tentativa falhou ^(falhas de rede são comuns^). Tentando
echo  de novo — desta vez sem barra de progresso, gravando o registro em:
echo      %LOG%
"%UV_EXE%" %UV_CMD% > "%LOG%" 2>&1
if errorlevel 1 goto ERRO_REDE
:INSTALADO

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
echo      Causa mais comum: o "App Installer" do Windows (que fornece o
echo      winget) está desatualizado e não instala pacotes deste tipo.
echo      Abra a Microsoft Store, procure "App Installer", atualize
echo      (é gratuito e oficial da Microsoft) e rode este instalador de
echo      novo: https://apps.microsoft.com/detail/9NBLGGH4NNS1
echo.
echo      Se repetir, siga o guia passo a passo:
echo      https://github.com/antrologos/Transcritorio/blob/main/docs/INSTALL_WINDOWS.md
goto FIM_ERRO

:ERRO_REDE
echo.
echo  [!] O download ou a instalação do Transcritório falhou.
if exist "%LOG%" (
    echo      Últimas linhas do registro ^(a causa costuma estar aqui^):
    echo      ----------------------------------------------------------
    powershell -NoProfile -Command "Get-Content -LiteralPath '%LOG%' -Tail 12 -Encoding UTF8"
    echo      ----------------------------------------------------------
    echo      Registro completo: %LOG%
    echo      Mande esse arquivo ^(ou uma foto desta tela^) para quem lhe
    echo      passou o instalador — é o que permite descobrir a causa.
    echo.
)
echo      Causas comuns:
echo      - Rede de universidade/empresa bloqueando downloads: peça à TI
echo        para liberar pypi.org, files.pythonhosted.org, github.com e
echo        astral.sh — ou tente em casa / no hotspot do celular.
echo      - Conexão lenta: rode este instalador de novo ^(ele continua de
echo        onde parou, não recomeça do zero^).
echo      - Pouco espaço em disco: são necessários ~4 GB livres.
echo      - O Transcritório estava ABERTO durante uma atualização: feche a
echo        janela dele e rode este instalador de novo.
echo      Guia completo:
echo      https://github.com/antrologos/Transcritorio/blob/main/docs/INSTALL_WINDOWS.md
goto FIM_ERRO

:FIM_ERRO
echo.
echo  Nada foi quebrado: você pode rodar este instalador quantas vezes quiser.
:FIM
echo.
pause
endlocal
