#!/usr/bin/env bash
# Launch the extracted Transcritorio GUI in WSL through WSLg (DISPLAY=:0).
# Requires the AppImage to already be extracted in ~/transcritorio-test/squashfs-root/
# (run smoke_wsl_appimage.sh first).
set -u

cd "$HOME/transcritorio-test" || exit 2
unset QT_QPA_PLATFORM
export DISPLAY="${DISPLAY:-:0}"

echo "DISPLAY=$DISPLAY"
ls squashfs-root/usr/bin/ 2>&1 | head -5

./squashfs-root/usr/bin/Transcritorio > gui_live.log 2>&1 &
PID=$!
echo "started PID=$PID"
sleep 8

if kill -0 "$PID" 2>/dev/null; then
    echo "OK: Transcritorio GUI rodando (PID=$PID)"
    echo "----- log (ultimas 20 linhas) -----"
    tail -20 gui_live.log
    echo "-----------------------------------"
    echo ""
    echo "A JANELA DO TRANSCRITORIO DEVE ESTAR ABERTA NO WINDOWS (via WSLg)."
    echo "Tire um screenshot com Win+Shift+S antes do timeout."
    echo "Vou manter o app rodando por 30 segundos..."
    sleep 30
    kill "$PID" 2>/dev/null
    wait "$PID" 2>/dev/null
    echo "GUI encerrada."
else
    echo "FALHOU: processo morreu em menos de 8s"
    echo "----- log completo -----"
    cat gui_live.log
    exit 1
fi
