@echo off
chcp 65001 >nul
title Transcritório — relatório de diagnóstico
rem =============================================================================
rem  Junta as informações que o desenvolvedor precisa para entender por que uma
rem  transcrição demorou. NÃO altera nada: só LÊ arquivos e grava um .txt na sua
rem  Área de Trabalho, que você confere antes de mandar.
rem
rem  Baixa o script de coleta do repositório oficial do Transcritório (público e
rem  auditável): github.com/antrologos/Transcritorio -> scripts/Diagnostico.ps1
rem =============================================================================
echo.
echo  Coletando informações... isso leva alguns segundos.
echo.
set "PS=%TEMP%\Transcritorio-Diagnostico.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/antrologos/Transcritorio/beta/scripts/Diagnostico.ps1' -OutFile $env:TEMP'\Transcritorio-Diagnostico.ps1' } catch { Write-Host ''; Write-Host ' [!] Nao consegui baixar o script. Verifique a internet.'; exit 1 }"
if not exist "%PS%" goto ERRO
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS%"
echo.
pause
exit /b 0
:ERRO
echo.
echo  [!] Falhou. Avise o desenvolvedor.
echo.
pause
exit /b 1
