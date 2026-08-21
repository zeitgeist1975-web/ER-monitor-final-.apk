#!/usr/bin/env python3
# --tpl <템플릿>: 더미 args 렌더 후 XML 파싱 / --scan <dir>: 생성 매니페스트 전수 파싱
import glob, os, sys, xml.etree.ElementTree as ET

class Nil:
    def __getattr__(s, k): return Nil()
    def __iter__(s): return iter(())
    def __bool__(s): return False
    def __str__(s): return ""
    def __getitem__(s, k): return Nil()

class Args(Nil):
    numeric_version="1"; version="1.0.0"; package="org.ermon.ermonitor"
    android_entrypoint="org.kivy.android.PythonActivity"
    android_apptheme="@android:style/Theme.NoTitleBar"
    activity_launch_mode="singleTask"; allow_backup="true"; window=True
    min_sdk_version=26; android_api=34; permissions=[]; orientation=["portrait"]
    service_class_name="org.kivy.android.PythonService"
    extra_manifest_xml=""; extra_manifest_application_arguments=""

def parse(src, tag):
    try:
        ET.fromstring(src); print("[OK]", tag); return True
    except ET.ParseError as e:
        ln = e.position[0]; L = src.splitlines()
        print("[FAIL]", tag, e, file=sys.stderr)
        for i in range(max(0, ln-6), min(len(L), ln+5)):
            print(("%s%5d | %s") % (">>" if i+1==ln else "  ", i+1, L[i]), file=sys.stderr)
        return False

def tpl(p):
    import jinja2
    r = open(p, encoding="utf-8").read()
    if "extra_manifest" in r: print("[FAIL] 플레이스홀더 잔존", p, file=sys.stderr); return False
    if "supportsPictureInPicture" not in r: print("[FAIL] PiP 없음", p, file=sys.stderr); return False
    return parse(jinja2.Environment(undefined=jinja2.ChainableUndefined)
                 .from_string(r).render(args=Args(), debug=True, url_scheme=""), p)

def scan(d):
    h = sorted(set(glob.glob(os.path.join(d, "**", "AndroidManifest.xml"), recursive=True)))
    if not h: print("[WARN] 대상 없음", d); return True
    return all(parse(open(f, encoding="utf-8").read(), f) for f in h)

if __name__ == "__main__":
    m, v = sys.argv[1], sys.argv[2]
    sys.exit(0 if (tpl(v) if m == "--tpl" else scan(v)) else 1)
