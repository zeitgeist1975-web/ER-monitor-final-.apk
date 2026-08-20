#!/usr/bin/env bash
# p4a 부트스트랩 AndroidManifest 템플릿에 PiP 속성 주입
#   android:supportsPictureInPicture="true"
#   android:resizeableActivity="true"
# 사용법: bash tools/patch_p4a_manifest.sh <p4a_source_dir>
set -euo pipefail

P4A_DIR="${1:?p4a source dir 필요}"
LOG="${GITHUB_WORKSPACE:-$PWD}/patch_manifest.log"
: > "$LOG"

log() { echo "[patch] $*" | tee -a "$LOG"; }

log "p4a dir = $P4A_DIR"
mapfile -t TPLS < <(find "$P4A_DIR/pythonforandroid/bootstraps" \
                      -name 'AndroidManifest.tmpl.xml' | sort)

if [ "${#TPLS[@]}" -eq 0 ]; then
  log "FATAL: AndroidManifest.tmpl.xml 을 찾지 못함"
  find "$P4A_DIR" -maxdepth 4 -type d | head -50 | tee -a "$LOG"
  exit 1
fi

PATCHED=0
for T in "${TPLS[@]}"; do
  log "대상: $T"
  if grep -q 'supportsPictureInPicture' "$T"; then
    log "  → 이미 패치됨, 건너뜀"
    PATCHED=$((PATCHED+1)); continue
  fi
  if ! grep -q 'org.kivy.android.PythonActivity' "$T"; then
    log "  → PythonActivity 없음, 건너뜀"
    continue
  fi
  cp "$T" "$T.bak"
  python3 - "$T" <<'PY'
import sys, re
p = sys.argv[1]
s = open(p, encoding='utf-8').read()
needle = 'android:name="org.kivy.android.PythonActivity"'
add = (needle +
       '\n              android:supportsPictureInPicture="true"'
       '\n              android:resizeableActivity="true"')
if needle not in s:
    sys.exit('needle not found')
s = s.replace(needle, add, 1)
open(p, 'w', encoding='utf-8').write(s)
print('patched:', p)
PY
  grep -n 'supportsPictureInPicture\|resizeableActivity' "$T" | tee -a "$LOG"
  PATCHED=$((PATCHED+1))
done

log "패치 완료 템플릿 수 = $PATCHED"
[ "$PATCHED" -gt 0 ] || { log "FATAL: 패치 0건"; exit 1; }
