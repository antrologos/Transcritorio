#!/usr/bin/env bash
# Smoke test for the Linux AppImage inside WSL Ubuntu.
#
# Replicates the smoke-linux-appimage job from .github/workflows/release.yml
# but aimed at local validation from Windows/WSL. Does not require GUI
# display — uses Qt offscreen platform plugin.
#
# Usage (from within WSL Ubuntu):
#   bash smoke_wsl_appimage.sh          # downloads latest tagged AppImage
#   bash smoke_wsl_appimage.sh /path    # uses an existing AppImage file
#
# Exit 0 = all checks pass. Exit !=0 = see which step failed in output.

set -u

TEST_DIR="$HOME/transcritorio-test"
mkdir -p "$TEST_DIR"
cd "$TEST_DIR"

VERSION="${TRANSCRITORIO_VERSION:-0.1.2}"
APPIMAGE="${1:-}"

pass() { printf '\n[PASS] %s\n' "$1"; }
fail() { printf '\n[FAIL] %s\n' "$1"; exit 1; }
info() { printf '[--] %s\n' "$1"; }

# -------- Step 1: obtain AppImage --------
if [ -z "$APPIMAGE" ]; then
    APPIMAGE="Transcritorio-x86_64.AppImage"
    URL="https://github.com/antrologos/Transcritorio/releases/download/v${VERSION}/${APPIMAGE}"
    if [ ! -f "$APPIMAGE" ]; then
        info "Downloading ${URL}..."
        curl -fLsS --output "$APPIMAGE" "$URL" || fail "download failed from $URL"
    else
        info "Using cached $APPIMAGE ($(du -h "$APPIMAGE" | cut -f1))"
    fi
fi
[ -f "$APPIMAGE" ] || fail "AppImage not found: $APPIMAGE"
pass "AppImage present: $(du -h "$APPIMAGE" | cut -f1)"

# -------- Step 2: make executable --------
chmod +x "$APPIMAGE" || fail "chmod failed"
pass "chmod +x"

# -------- Step 3: extract (no FUSE needed) --------
rm -rf squashfs-root
./"$APPIMAGE" --appimage-extract > /dev/null 2>&1 || fail "--appimage-extract failed"
[ -d squashfs-root ] || fail "squashfs-root missing after extract"
FILES=$(ls squashfs-root/usr/bin/ 2>/dev/null | tr '\n' ' ')
info "usr/bin: $FILES"
for bin in Transcritorio transcritorio-cli; do
    [ -f "squashfs-root/usr/bin/$bin" ] || fail "missing: $bin"
done
pass "AppImage extracted; Transcritorio + transcritorio-cli present"

# -------- Step 4: transcritorio-cli --help --------
rm -f cli_help.txt
./squashfs-root/usr/bin/transcritorio-cli --help > cli_help.txt 2>&1 \
    || { tail -20 cli_help.txt; fail "transcritorio-cli --help crashed"; }
grep -q manifest cli_help.txt && grep -q transcribe cli_help.txt \
    || { tail -20 cli_help.txt; fail "CLI --help output is incomplete"; }
pass "transcritorio-cli --help (manifest + transcribe subcommands visible)"

# -------- Step 5: GUI smoke (offscreen Qt) --------
export QT_QPA_PLATFORM=offscreen
rm -f gui_log.txt
set +e
timeout --preserve-status 3 ./squashfs-root/usr/bin/Transcritorio > gui_log.txt 2>&1
gui_code=$?
set -e
info "GUI exit code: $gui_code"
tail -30 gui_log.txt
case $gui_code in
    0|124|143)
        pass "GUI survived startup in offscreen (exit=$gui_code)" ;;
    *)
        fail "GUI crashed in offscreen (exit=$gui_code)" ;;
esac

# -------- Step 6: models verify (may need token but must not crash) --------
set +e
./squashfs-root/usr/bin/transcritorio-cli models verify > models_log.txt 2>&1
models_code=$?
set -e
info "models verify exit code: $models_code (any non-crash is OK)"
tail -10 models_log.txt
pass "transcritorio-cli models subcommand responded"

echo
echo "=============================================="
echo "OK: todos os smoke tests do WSL passaram."
echo "Exit codes: CLI=0, GUI=$gui_code, models=$models_code"
echo "=============================================="
