"""Toy v0.2.0: instaladores de duplo clique (.bat) — forma e seguranca.

O publico-alvo nao usa terminal: Instalar-Transcritorio.bat faz os 3
comandos do guia com duplo clique. Este toy valida ESTATICAMENTE (sem
executar winget): CRLF obrigatorio (goto em .bat com LF e bug notorio
do cmd), comandos-chave, fallback de PATH do uv, e o checklist de
seguranca — so IDs winget oficiais, so https, sem elevacao, sem
downloads avulsos, pause sempre (janela nunca fecha sem ler).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
INSTALAR = SCRIPTS / "Instalar-Transcritorio.bat"
ATUALIZAR = SCRIPTS / "Atualizar-Transcritorio.bat"

for caminho in (INSTALAR, ATUALIZAR):
    assert caminho.exists(), caminho
    dados = caminho.read_bytes()
    # CRLF em TODAS as linhas (LF orfao quebra goto/labels no cmd)
    assert b"\r\n" in dados, f"{caminho.name}: sem CRLF"
    assert not re.search(rb"(?<!\r)\n", dados), f"{caminho.name}: LF orfao"
    texto = dados.decode("utf-8")
    assert texto.startswith("@echo off"), caminho.name
    assert "chcp 65001" in texto, f"{caminho.name}: sem chcp (acentos)"
    assert "pause" in texto, f"{caminho.name}: janela fecharia sem ler"
    # Fallback do uv fora do PATH da sessao — cascata completa (2026-09-01:
    # beta tester real com winget que instala mas NAO cria o link em Links;
    # o exe fica so dentro de Packages\astral-sh.uv*, as vezes em subpasta)
    assert r"%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe" in texto, caminho.name
    assert r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv" in texto, (
        f"{caminho.name}: sem varredura do pacote portable")
    assert r"%USERPROFILE%\.local\bin\uv.exe" in texto, (
        f"{caminho.name}: sem fallback do instalador oficial da Astral")
    # Blindagem de rede (2026-09-02, beta tester com a causa fora da tela):
    # tempo limite maior, 2a tentativa com registro e as ultimas linhas do
    # registro impressas junto do erro; a lista de hosts cita o GitHub
    # (de onde o uv baixa o Python quando a maquina nao tem nenhum).
    assert 'set "UV_HTTP_TIMEOUT=' in texto, f"{caminho.name}: sem tempo limite ampliado"
    assert r"%TEMP%\Transcritorio-" in texto and ".log" in texto, (
        f"{caminho.name}: sem registro da 2a tentativa")
    assert "-Tail" in texto, f"{caminho.name}: erro nao mostra o registro"
    assert "github.com e" in texto or "e github.com" in texto, (
        f"{caminho.name}: hosts sem github.com")
    assert texto.count('> "%LOG%" 2>&1') == 1, f"{caminho.name}: 2a tentativa ausente/duplicada"
    # ---- checklist de seguranca ----
    assert "http://" not in texto, f"{caminho.name}: http sem criptografia"
    baixo = texto.lower()
    for proibido in ("curl ", "invoke-webrequest", "iex ", "downloadstring",
                     "runas", "-verb runas", "start-process -verb"):
        assert proibido not in baixo, f"{caminho.name}: {proibido!r}"
    assert "hf_" not in texto, f"{caminho.name}: segredo?"

texto = INSTALAR.read_text(encoding="utf-8")
assert "winget install -e --id astral-sh.uv" in texto
assert "winget install -e --id Gyan.FFmpeg" in texto
assert "tool install transcritorio" in texto
assert "tool upgrade transcritorio" in texto  # idempotente: ja instalado -> upgrade
assert "SEM_WINGET" in texto and "ERRO_REDE" in texto
assert "files.pythonhosted.org" in texto  # mensagem de proxy/TI
assert "4 GB" in texto  # espaco em disco como causa comum
assert "INSTALADO" in texto  # rotulo da 2a tentativa bem-sucedida
# So os DOIS IDs oficiais no winget — nenhum pacote extra
ids = re.findall(r"--id (\S+)", texto)
assert sorted(set(ids)) == ["Gyan.FFmpeg", "astral-sh.uv"], ids

texto2 = ATUALIZAR.read_text(encoding="utf-8")
assert "tool upgrade transcritorio" in texto2
assert "tool install" not in texto2  # atualizador nunca instala do zero

print("PASS: toy_installer_bat (CRLF, comandos, fallback, seguranca)")
