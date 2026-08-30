@echo off
REM Abre o Transcritorio como se fosse um computador recem-instalado.
REM NAO toca na instalacao real: tudo (modelos, preferencias, ambiente de
REM AI) vai para D:\tmp\transcritorio_virgem. Para zerar o teste, apague
REM essa pasta. Feche o Transcritorio normal antes (instancia unica).
REM
REM Opcional: simular uma maquina diferente da sua para ver o que o
REM assistente recomendaria nela. Exemplos:
REM   set TRANSCRITORIO_FAKE_HARDWARE=cpu     (sem placa de video)
REM   set TRANSCRITORIO_FAKE_HARDWARE=gpu2    (placa de 2 GB)
set "TRANSCRITORIO_HOME=D:\tmp\transcritorio_virgem"
set "TRANSCRITORIO_MODEL_CACHE=D:\tmp\transcritorio_virgem\models"
if not exist "%TRANSCRITORIO_HOME%" mkdir "%TRANSCRITORIO_HOME%"
start "" "%USERPROFILE%\AppData\Roaming\uv\tools\transcritorio\Scripts\transcritorio.exe"
echo Transcritorio aberto em modo de teste (primeiro uso).
echo Feche-o e abra pelo atalho de sempre para voltar ao normal.
