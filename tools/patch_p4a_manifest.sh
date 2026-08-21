#!/usr/bin/env bash
# p4a AndroidManifest.tmpl.xml 직접 패치 + 렌더 검증
#  buildozer 1.5.0 의 --extra-manifest-* 는 셸 인용부호를 리터럴로 넘기는 버그가 있어
#  사용하지 않고, 템플릿의 jinja 플레이스홀더를 여기서 직접 치환한다.
#   1) {{ args.extra_manifest_xml }}                   -> uses-feature 블록
#   2) {{ args.extra_manifest_application_arguments }} -> application 속성
#   3) PiP 속성 주입 (sdl2 는 android:name="{{args.android_entrypoint}}")
# 사용법: bash tools/patch_p4a_manifest.sh <p4a_source_dir>
set -euo pipefail
P4A="${1:?p4a dir}"; R="${GITHUB_WORKSPACE:-$PWD}"
X="$R/src/android/extra_manifest.xml"; A="$R/src/android/extra_manifest_application_arguments.xml"
[ -f "$X" ] && [ -f "$A" ] || { echo "::error::extra_manifest 파일 없음"; exit 1; }
mapfile -t T < <(find "$P4A/pythonforandroid/bootstraps" -name AndroidManifest.tmpl.xml | sort)
[ "${#T[@]}" -gt 0 ] || { echo "::error::AndroidManifest.tmpl.xml 없음"; exit 1; }
SDL2=""
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
  case "$t" in */bootstraps/sdl2/*) SDL2="$t";; esac
done
[ -n "$SDL2" ] || { echo "::error::sdl2 템플릿 없음"; exit 1; }
for k in supportsPictureInPicture requestLegacyExternalStorage picture_in_picture; do
  grep -q "$k" "$SDL2" || { echo "::error::sdl2 $k 미적용"; exit 1; }
done
grep -q extra_manifest "$SDL2" && { echo "::error::플레이스홀더 잔존"; exit 1; }
python3 - "$SDL2" <<'PY'
import sys, xml.etree.ElementTree as ET
p = sys.argv[1]; raw = open(p, encoding='utf-8').read()
try:
    import jinja2
except ImportError:
    print('[verify] jinja2 없음 - 렌더 검증 생략'); sys.exit(0)
class Nil:
    def __getattr__(s,k): return Nil()
    def __iter__(s): return iter(())
    def __bool__(s): return False
    def __str__(s): return ''
    def __getitem__(s,k): return Nil()
class Args(Nil):
    numeric_version='1'; version='1.0.0'; package='org.ermon.ermonitor'
    android_entrypoint='org.kivy.android.PythonActivity'
    android_apptheme='@android:style/Theme.NoTitleBar'
    activity_launch_mode='singleTask'; allow_backup='true'; window=True
    min_sdk_version=26; android_api=34; permissions=[]; orientation=['portrait']
    service_class_name='org.kivy.android.PythonService'
    extra_manifest_xml=''; extra_manifest_application_arguments=''
out = jinja2.Environment(undefined=jinja2.ChainableUndefined).from_string(raw).render(
    args=Args(), debug=True, url_scheme='')
try:
    ET.fromstring(out); print('[verify] AndroidManifest 렌더 파싱 OK')
except ET.ParseError as e:
    ln = e.position[0]; L = out.splitlines()
    print('::error::AndroidManifest 렌더 파싱 실패: %s' % e)
    for i in range(max(0,ln-6), min(len(L), ln+5)):
        print('%s%5d | %s' % ('>>' if i+1==ln else '  ', i+1, L[i]))
    sys.exit(1)
PY
echo "[patch] 완료"
