#!/usr/bin/env bash
set -euo pipefail
P4A="${1:?p4a dir}"; R="${GITHUB_WORKSPACE:-$PWD}"
X="$R/src/android/extra_manifest.xml"; A="$R/src/android/extra_manifest_application_arguments.xml"
[ -f "$X" ] && [ -f "$A" ] || { echo "FATAL: extra_manifest 파일 없음"; exit 1; }
mapfile -t T < <(find "$P4A/pythonforandroid/bootstraps" -name AndroidManifest.tmpl.xml | sort)
[ "${#T[@]}" -gt 0 ] || { echo "FATAL: 템플릿 없음"; exit 1; }
OK=0
for t in "${T[@]}"; do
  EXTRA_XML="$X" EXTRA_APP="$A" python3 - "$t" <<'PY'
import os,re,sys
p=sys.argv[1]; s=o=open(p,encoding='utf-8').read()
if 'supportsPictureInPicture' not in s:
    s=re.sub(r'(<activity\s+android:name="(?:\{\{\s*args\.android_entrypoint\s*\}\}|org\.kivy\.android\.PythonActivity)")',
             r'\1\n                  android:supportsPictureInPicture="true"\n                  android:resizeableActivity="true"',s,1)
s=re.sub(r'\{\{\s*args\.extra_manifest_xml\s*\}\}',
         lambda m:open(os.environ['EXTRA_XML'],encoding='utf-8').read().strip(),s)
s=re.sub(r'\{\{\s*args\.extra_manifest_application_arguments\s*\}\}',
         lambda m:' '.join(l.strip() for l in open(os.environ['EXTRA_APP'],encoding='utf-8') if l.strip() and l.strip()[0]!='#'),s)
if s!=o: open(p+'.bak','w',encoding='utf-8').write(o); open(p,'w',encoding='utf-8').write(s)
print('[patch]',p)
PY
  case "$t" in */bootstraps/sdl2/*)
    for k in supportsPictureInPicture requestLegacyExternalStorage picture_in_picture; do
      grep -q "$k" "$t" || { echo "FATAL: sdl2 $k 미적용"; exit 1; }; done
    grep -q extra_manifest "$t" && { echo "FATAL: 플레이스홀더 잔존"; exit 1; }
    OK=1;; esac
done
[ "$OK" -eq 1 ] || { echo "FATAL: sdl2 미처리"; exit 1; }
echo "[patch] 완료"
