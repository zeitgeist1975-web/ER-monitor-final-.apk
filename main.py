# -*- coding: utf-8 -*-
"""
main.py — APK 진입점 부트스트랩
  1) 비정상 종료(SIGSEGV/ABRT 포함) 즉시 파악용 실시간 로그 (무버퍼 + faulthandler)
  2) Android 런타임 권한 자동 획득 (일반 + 특수권한 인텐트)
  3) 필수 요소 자동 점검 → 없으면 자동 다운로드 (한글폰트 폴백)
  4) 기본 입출력 폴더 = /sdcard/Download (미지정 시)
  5) 원본 앱(ER_monitor__final_.py)을 __main__ 으로 실행
"""
import os
import sys
import time
import atexit
import faulthandler
import threading
import traceback
re = __import__("re")

APP_SRC = 'ER_monitor__final_.py'
BOOT_TAG = 'BOOT'
_T0 = time.time()

# ══════════════════════════════════════════════════════════════════
#  0. 로그 디렉터리 결정 (미지정 시 Download 우선)
# ══════════════════════════════════════════════════════════════════
_IS_ANDROID = hasattr(sys, 'getandroidapilevel')

def _writable(d):
    try:
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, '.wtest')
        with open(p, 'w') as f:
            f.write('x')
        os.remove(p)
        return True
    except Exception:
        return False

def _pkg_name():
    for k in ('ANDROID_PRIVATE', 'ANDROID_ARGUMENT', 'ANDROID_UNPACK'):
        v = (os.environ.get(k) or '') + '/'
        m = re.search(r'/(?:data|user/\d+)/(?:data/)?([A-Za-z][\w.]*\.[\w.]+)/', v)
        if m:
            return m.group(1)
    return ''


def _io_candidates():
    env = os.environ.get('ERMON_IO_DIR')
    c = ([env] if env else []) + ['/sdcard/Download',
                                  '/storage/emulated/0/Download']
    pkg = _pkg_name()
    if pkg:
        c += ['/sdcard/Android/data/%s/files' % pkg,
              '/storage/emulated/0/Android/data/%s/files' % pkg]
    c += [os.environ.get('ANDROID_PRIVATE'), os.environ.get('ANDROID_ARGUMENT'),
          '/data/local/tmp', os.path.expanduser('~'), '/tmp', os.getcwd()]
    out = []
    for d in c:
        if d and d not in out:
            out.append(d)
    return out


IO_CANDS = _io_candidates()
IO_WRITABLE = [d for d in IO_CANDS if _writable(d)]
IO_DIR = IO_WRITABLE[0] if IO_WRITABLE else os.getcwd()
os.environ.setdefault('ERMON_IO_DIR', IO_DIR)
LOG_PATH = os.path.join(IO_DIR, 'ermon_boot.log')
FAULT_PATH = os.path.join(IO_DIR, 'ermon_fault.log')

# ══════════════════════════════════════════════════════════════════
#  1. 실시간 로그 (라인마다 flush + fsync → 강제종료에도 손실 없음)
# ══════════════════════════════════════════════════════════════════
_log_lock = threading.Lock()
try:
    _LOGF = open(LOG_PATH, 'a', buffering=1, encoding='utf-8', errors='replace')
except Exception:
    _LOGF = None

def blog(msg, tag=BOOT_TAG):
    line = '%s +%07.3fs [%s] %s' % (
        time.strftime('%Y-%m-%d %H:%M:%S'), time.time() - _T0, tag, msg)
    with _log_lock:
        try:
            print(line, flush=True)
        except Exception:
            pass
        if _LOGF:
            try:
                _LOGF.write(line + '\n')
                _LOGF.flush()
                os.fsync(_LOGF.fileno())
            except Exception:
                pass

# 네이티브 크래시(SIGSEGV/SIGABRT/SIGFPE/SIGBUS) 스택 덤프
try:
    _FF = open(FAULT_PATH, 'a', buffering=1)
    faulthandler.enable(file=_FF, all_threads=True)
    try:
        import signal
        faulthandler.register(signal.SIGTERM, file=_FF, all_threads=True, chain=True)
    except Exception:
        pass
except Exception as e:
    blog('faulthandler 비활성: %s' % e, 'WARN')

def _excepthook(t, v, tb):
    blog('UNCAUGHT %s: %s' % (t.__name__, v), 'FATAL')
    for ln in traceback.format_exception(t, v, tb):
        blog(ln.rstrip(), 'FATAL')
sys.excepthook = _excepthook

# 쓰기 가능한 모든 후보에 로그 위치 안내 파일을 남긴다(유실 방지).
for _d in IO_WRITABLE[:5]:
    try:
        with open(os.path.join(_d, 'ermon_log_where.txt'), 'w',
                  encoding='utf-8') as _f:
            _f.write(LOG_PATH + '\n')
    except Exception:
        pass


def _relocate_log(prefer='/sdcard/Download'):
    """권한 획득 후 Download 가 열리면 로그를 그쪽으로 옮긴다."""
    global _LOGF, LOG_PATH
    if os.path.dirname(LOG_PATH) == prefer or not _writable(prefer):
        return
    try:
        new = os.path.join(prefer, 'ermon_boot.log')
        try:
            data = open(LOG_PATH, encoding='utf-8', errors='replace').read()
        except Exception:
            data = ''
        nf = open(new, 'a', buffering=1, encoding='utf-8', errors='replace')
        if data:
            nf.write(data)
            nf.flush()
        with _log_lock:
            try:
                if _LOGF:
                    _LOGF.close()
            except Exception:
                pass
            _LOGF = nf
            LOG_PATH = new
        os.environ['ERMON_IO_DIR'] = prefer
        blog('로그 이전 완료 -> %s' % new)
    except Exception as e:
        blog('로그 이전 실패: %s' % e, 'WARN')


def _show_error_screen(text):
    """무음 종료 방지 — 오류 내용을 화면에 표시."""
    try:
        from kivy.app import App
        from kivy.uix.label import Label
        from kivy.uix.scrollview import ScrollView
        msg = 'LOG: %s\n\n%s' % (LOG_PATH, text)

        class _ErrApp(App):
            def build(self):
                lb = Label(text=msg, font_size='12sp', halign='left',
                           valign='top', size_hint_y=None, padding=(10, 10))
                fp = os.environ.get('ERMON_FONT')
                if fp and os.path.exists(fp):
                    lb.font_name = fp
                lb.bind(texture_size=lambda i, v: setattr(i, 'height', v[1]))
                lb.bind(width=lambda i, v: setattr(i, 'text_size', (v - 20, None)))
                sv = ScrollView()
                sv.add_widget(lb)
                return sv

        blog('오류 화면 표시')
        _ErrApp().run()
    except Exception as e:
        blog('오류 화면 표시 실패: %s' % e, 'WARN')

def _thread_excepthook(a):
    blog('THREAD-UNCAUGHT [%s] %s: %s' % (
        getattr(a.thread, 'name', '?'), a.exc_type.__name__, a.exc_value), 'FATAL')
    for ln in traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback):
        blog(ln.rstrip(), 'FATAL')
try:
    threading.excepthook = _thread_excepthook
except Exception:
    pass

@atexit.register
def _bye():
    blog('프로세스 종료 (uptime=%.3fs)' % (time.time() - _T0), 'EXIT')
    try:
        if _LOGF:
            _LOGF.flush()
            os.fsync(_LOGF.fileno())
    except Exception:
        pass

blog('=' * 62)
blog('부트스트랩 시작  py=%s' % sys.version.replace('\n', ' '))
blog('android=%s  io_dir=%s' % (_IS_ANDROID, IO_DIR))
blog('log=%s  fault=%s' % (LOG_PATH, FAULT_PATH))
blog('cwd=%s  __file__=%s' % (os.getcwd(), os.path.abspath(__file__)))

# ══════════════════════════════════════════════════════════════════
#  2. 권한 획득
# ══════════════════════════════════════════════════════════════════
NORMAL_PERMS = [
    'INTERNET', 'ACCESS_NETWORK_STATE', 'WAKE_LOCK', 'VIBRATE',
    'POST_NOTIFICATIONS', 'FOREGROUND_SERVICE', 'RECEIVE_BOOT_COMPLETED',
    'READ_EXTERNAL_STORAGE', 'WRITE_EXTERNAL_STORAGE',
]

def _api_level():
    try:
        return sys.getandroidapilevel()
    except Exception:
        return 0

def request_runtime_permissions(timeout=25.0):
    """일반 위험권한 요청 → 승인/거부 결과를 로그로 남김."""
    if not _IS_ANDROID:
        blog('PC 환경 — 권한 요청 생략', 'PERM')
        return
    try:
        from android.permissions import request_permissions, check_permission, Permission
    except Exception as e:
        blog('android.permissions 사용 불가: %s' % e, 'PERM')
        return

    want, names = [], []
    for n in NORMAL_PERMS:
        p = getattr(Permission, n, None)
        if p is None:
            blog('미지원 권한 스킵: %s' % n, 'PERM')
            continue
        try:
            if check_permission(p):
                blog('이미 승인: %s' % n, 'PERM')
                continue
        except Exception:
            pass
        want.append(p)
        names.append(n)

    if not want:
        blog('추가 요청 필요 권한 없음', 'PERM')
        return

    done = threading.Event()
    result = {}

    def _cb(perms, grants):
        for p, g in zip(perms, grants):
            result[p] = g
        done.set()

    blog('권한 요청: %s' % ','.join(names), 'PERM')
    try:
        request_permissions(want, _cb)
    except TypeError:
        request_permissions(want)
        done.set()
    except Exception as e:
        blog('요청 예외: %s' % e, 'PERM')
        return

    if not done.wait(timeout):
        blog('권한 콜백 타임아웃(%.0fs) — 계속 진행' % timeout, 'PERM')
        return
    for n, p in zip(names, want):
        blog('결과 %s = %s' % (n, 'GRANTED' if result.get(p) else 'DENIED'), 'PERM')


def request_all_files_access():
    """API30+ 전체 파일 접근(MANAGE_EXTERNAL_STORAGE) — /sdcard/Download 사용."""
    if not _IS_ANDROID or _api_level() < 30:
        return
    try:
        from jnius import autoclass
        Environment = autoclass('android.os.Environment')
        if Environment.isExternalStorageManager():
            blog('MANAGE_EXTERNAL_STORAGE 이미 허용', 'PERM')
            return
        PA = autoclass('org.kivy.android.PythonActivity')
        Intent = autoclass('android.content.Intent')
        Settings = autoclass('android.provider.Settings')
        Uri = autoclass('android.net.Uri')
        it = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                    Uri.parse('package:' + PA.mActivity.getPackageName()))
        it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        PA.mActivity.startActivity(it)
        blog('전체 파일 접근 설정화면 호출', 'PERM')
    except Exception as e:
        blog('전체 파일 접근 요청 실패(무시): %s' % e, 'PERM')


def request_overlay_permission():
    """SYSTEM_ALERT_WINDOW — 상단 오버레이 기능용."""
    if not _IS_ANDROID or _api_level() < 23:
        return
    try:
        from jnius import autoclass
        Settings = autoclass('android.provider.Settings')
        PA = autoclass('org.kivy.android.PythonActivity')
        act = PA.mActivity
        if Settings.canDrawOverlays(act):
            blog('SYSTEM_ALERT_WINDOW 이미 허용', 'PERM')
            return
        Intent = autoclass('android.content.Intent')
        Uri = autoclass('android.net.Uri')
        it = Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse('package:' + act.getPackageName()))
        it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        act.startActivity(it)
        blog('오버레이 권한 설정화면 호출', 'PERM')
    except Exception as e:
        blog('오버레이 권한 요청 실패(무시): %s' % e, 'PERM')


def request_battery_exemption():
    """REQUEST_IGNORE_BATTERY_OPTIMIZATIONS — PiP 백그라운드 유지용."""
    if not _IS_ANDROID or _api_level() < 23:
        return
    try:
        from jnius import autoclass
        PA = autoclass('org.kivy.android.PythonActivity')
        act = PA.mActivity
        Context = autoclass('android.content.Context')
        pm = act.getSystemService(Context.POWER_SERVICE)
        pkg = act.getPackageName()
        if pm.isIgnoringBatteryOptimizations(pkg):
            blog('배터리 최적화 이미 제외됨', 'PERM')
            return
        Intent = autoclass('android.content.Intent')
        Settings = autoclass('android.provider.Settings')
        Uri = autoclass('android.net.Uri')
        it = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                    Uri.parse('package:' + pkg))
        it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        act.startActivity(it)
        blog('배터리 최적화 제외 요청', 'PERM')
    except Exception as e:
        blog('배터리 최적화 요청 실패(무시): %s' % e, 'PERM')

# ══════════════════════════════════════════════════════════════════
#  3. 필수 요소 자동 점검 / 자동 다운로드
# ══════════════════════════════════════════════════════════════════
SYSTEM_KR_FONTS = [
    '/system/fonts/NotoSansCJK-Regular.ttc',
    '/system/fonts/NotoSansCJKkr-Regular.otf',
    '/system/fonts/NotoSansKR-Regular.otf',
    '/system/fonts/NotoSerifCJK-Regular.ttc',
    '/system/fonts/DroidSansFallback.ttf',
]
FONT_URL = ('https://raw.githubusercontent.com/google/fonts/main/ofl/'
            'nanumgothic/NanumGothic-Regular.ttf')

def ensure_assets():
    """한글 폰트: 시스템 폰트 존재 시 그대로 사용(기본 폰트 정책).
    하나도 없을 때만 NanumGothic 자동 다운로드 → fonts/NanumGothic.ttf"""
    for f in SYSTEM_KR_FONTS:
        if os.path.exists(f):
            blog('시스템 한글폰트 확인: %s' % f, 'ASSET')
            return f
    here = os.path.dirname(os.path.abspath(__file__))
    for f in (os.path.join(here, 'NanumGothic.ttf'),
              os.path.join(here, 'fonts', 'NanumGothic.ttf')):
        if os.path.exists(f):
            blog('번들 폰트 확인: %s' % f, 'ASSET')
            return f
    dst_dir = os.path.join(IO_DIR, 'ermon_fonts')
    dst = os.path.join(dst_dir, 'NanumGothic.ttf')
    if os.path.exists(dst) and os.path.getsize(dst) > 100000:
        blog('캐시 폰트 사용: %s' % dst, 'ASSET')
        return dst
    blog('한글폰트 없음 → 자동 다운로드 시도', 'ASSET')
    try:
        os.makedirs(dst_dir, exist_ok=True)
        import urllib.request
        t = time.time()
        urllib.request.urlretrieve(FONT_URL, dst)
        blog('폰트 다운로드 완료 %d bytes (%.2fs)' % (
            os.path.getsize(dst), time.time() - t), 'ASSET')
        return dst
    except Exception as e:
        blog('폰트 다운로드 실패: %s — 기본 폰트로 진행(한글 깨짐 가능)' % e, 'ASSET')
        return None


def preflight():
    """의존 모듈 존재 확인 — 없는 항목을 로그로 즉시 특정."""
    mods = ['kivy', 'flask', 'requests', 'jnius', 'plyer', 'certifi',
            'jinja2', 'werkzeug', 'urllib3']
    for m in mods:
        try:
            __import__(m)
            v = getattr(sys.modules[m], '__version__', '?')
            blog('MOD OK   %-12s %s' % (m, v), 'PRE')
        except Exception as e:
            blog('MOD FAIL %-12s %s' % (m, e), 'PRE')

# ══════════════════════════════════════════════════════════════════
#  4. 실행
# ══════════════════════════════════════════════════════════════════
def main():
    blog('--- 권한 단계 ---')
    request_runtime_permissions()
    request_all_files_access()
    request_overlay_permission()
    request_battery_exemption()
    _relocate_log()
    blog('IO 후보=%s' % IO_CANDS)
    blog('쓰기가능=%s' % IO_WRITABLE)
    blog('로그경로=%s' % LOG_PATH)
    for _k in ('ANDROID_ARGUMENT', 'ANDROID_PRIVATE', 'ANDROID_UNPACK',
               'ANDROID_APP_PATH', 'PYTHONHOME', 'PYTHONPATH'):
        blog('ENV %-16s = %s' % (_k, os.environ.get(_k)))

    blog('--- 자원 점검 단계 ---')
    fp = ensure_assets()
    if fp:
        os.environ['ERMON_FONT'] = fp
    preflight()

    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    # p4a 는 app 디렉터리의 .py 를 .pyc 로 치환하므로 두 형태 모두 탐색
    _base = os.path.splitext(APP_SRC)[0]
    _pyc = '%s.cpython-%d%d.pyc' % ((_base,) + sys.version_info[:2])
    _cands = [os.path.join(here, APP_SRC),
              os.path.join(here, _base + '.pyc'),
              os.path.join(here, '__pycache__', _pyc)]
    target = None
    for _c in _cands:
        if os.path.exists(_c):
            target = _c
            break
    if target is None:
        blog('원본 소스 없음 (탐색: %s)' % ' | '.join(_cands), 'FATAL')
        blog('디렉터리 내용: %s' % os.listdir(here), 'FATAL')
        raise SystemExit(2)
    blog('진입점 = %s' % target)

    blog('--- 앱 실행: %s (%d bytes) ---' % (target, os.path.getsize(target)))
    import runpy
    try:
        runpy.run_path(target, run_name='__main__')
        blog('앱 정상 반환')
    except SystemExit as e:
        blog('SystemExit code=%s' % e.code, 'EXIT')
        raise
    except BaseException:
        blog('앱 실행 중 예외 — 아래 트레이스백', 'FATAL')
        for ln in traceback.format_exc().splitlines():
            blog(ln, 'FATAL')
        raise


if __name__ == '__main__':
    try:
        main()
    except SystemExit as _e:
        if _e.code not in (0, None):
            _show_error_screen('SystemExit code=%s\n\n%s'
                               % (_e.code, traceback.format_exc()))
        raise
    except BaseException:
        _tb = traceback.format_exc()
        for _ln in _tb.splitlines():
            blog(_ln, 'FATAL')
        _show_error_screen(_tb)
        raise
