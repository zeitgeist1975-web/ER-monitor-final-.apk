# ═══════════════════════════════════════════════════════════════════
#  STEP 0: 절대 최초 충돌 로그 (import 전, builtins만 사용)
#  APK에서 로그가 안 생기면 이 파일로 Python 실행 여부 확인
# ═══════════════════════════════════════════════════════════════════
import sys as _sys
import os as _os

import time as _time
import threading as _th0

# ══════════════════════════════════════════════════════════════════
#  [일원화 2026-H1] 단일 통합 로그
#   이전: emergency_crash.log / er_name_search.log / emergency_app.log
#         3계열이 서로 다른 경로에 흩어져, 사고 순간의 인과를 한 파일에서
#         재구성할 수 없었다(이번 광주 오진의 직접 원인).
#   현재: 모든 채널(BOOT/APP/NS/API/KIVY/JS/CRASH/ADMIN)이 ermon.log 한 곳.
#   위치: 소스파일과 같은 폴더 → Download → /sdcard → /data/local/tmp
#   특성: 매 줄 flush(비정상 종료에도 보존) · 4MB 회전 · 스레드명 포함
# ══════════════════════════════════════════════════════════════════
LOG_NAME = 'ermon.log'
#  [일원화 2026-H3] 파일이 둘로 쪼개지던 문제의 원인:
#   APK 에서 소스 폴더는 /data/data/<pkg>/files/app (쓰기 가능하지만
#   사용자가 볼 수 없음). 그래서 부팅 로그가 거기 쌓이다가, 나중에
#   _setup_logging() 이 외부저장소를 승격하는 순간부터 다른 파일로 갈아탔다.
#   → ① main.py 부트스트랩이 정한 경로를 ERMON_LOG 로 인계받아 1순위
#     ② 안드로이드에서는 '사용자가 볼 수 있는 경로'를 소스 폴더보다 앞에
#     ③ 경로가 바뀔 때는 기존 내용을 이어붙여 옮긴다(_log_migrate)
_ANDROID_BOOT = hasattr(_sys, 'getandroidapilevel')
_ENV_LOG = (_os.environ.get('ERMON_LOG') or '').strip()
_ENV_IO = (_os.environ.get('ERMON_IO_DIR') or '').strip()
_LOG_CANDIDATES = []
if _ENV_LOG:
    _LOG_CANDIDATES.append(_ENV_LOG)          # main.py 가 이미 쓰고 있는 파일
if _ENV_IO:
    _LOG_CANDIDATES.append(_os.path.join(_ENV_IO, LOG_NAME))
_SRC_LOG = ''
try:
    _SRC_LOG = _os.path.join(
        _os.path.dirname(_os.path.abspath(__file__)), LOG_NAME)
except Exception:
    pass
if _SRC_LOG and not _ANDROID_BOOT:
    _LOG_CANDIDATES.append(_SRC_LOG)          # PC: 소스 폴더가 가장 편하다
_LOG_CANDIDATES += [
    '/storage/emulated/0/Download/' + LOG_NAME,
    '/sdcard/Download/' + LOG_NAME,
]
if _SRC_LOG and _ANDROID_BOOT:
    _LOG_CANDIDATES.append(_SRC_LOG)          # 안드로이드: 외부저장소 실패 시에만
_LOG_CANDIDATES += [
    '/sdcard/' + LOG_NAME,
    '/data/local/tmp/' + LOG_NAME,
    _os.path.join(_os.path.expanduser('~'), LOG_NAME),
]
_LOG_CANDIDATES = [p for i, p in enumerate(_LOG_CANDIDATES)
                   if p and p not in _LOG_CANDIDATES[:i]]
#  ERMON_LOG 가 있으면 부트스트랩이 정한 경로가 최종 — 승격 금지
_LOG_FIXED = bool(_ENV_LOG)
_LOG_PICK = [None]
_LOG_SEQ = [0]
_LOG_MAX = 4 * 1024 * 1024
_LOG_IO = _th0.Lock()
_LOG_RING = []                 # Kivy 디버그 패널 / /diag 최근 로그용
_LOG_RING_MAX = 600


def _ulog(tag, msg):
    """[일원화] 모든 로그의 단일 진입점. 실패해도 절대 예외를 던지지 않는다."""
    try:
        t = _time.time()
        ts = _time.strftime('%m-%d %H:%M:%S', _time.localtime(t)) + ('.%03d' % int((t % 1) * 1000))
    except Exception:
        ts = '??-?? ??:??:??.???'
    try:
        th = _th0.current_thread().name[:12]
    except Exception:
        th = '?'
    with _LOG_IO:
        _LOG_SEQ[0] += 1
        line = '%06d %s %-12s [%s] %s' % (_LOG_SEQ[0], ts, th, tag, msg)
        try:
            _LOG_RING.append(line)
            if len(_LOG_RING) > _LOG_RING_MAX:
                del _LOG_RING[0:len(_LOG_RING) - _LOG_RING_MAX]
        except Exception:
            pass
        try:
            print(line)
        except Exception:
            pass
        paths = [_LOG_PICK[0]] if _LOG_PICK[0] else _LOG_CANDIDATES
        for p in paths:
            try:
                try:
                    if _os.path.exists(p) and _os.path.getsize(p) > _LOG_MAX:
                        _os.replace(p, p + '.1')
                except Exception:
                    pass
                with open(p, 'a', encoding='utf-8', errors='replace') as f:
                    f.write(line + '\n')
                    f.flush()
                    try:
                        _os.fsync(f.fileno())
                    except Exception:
                        pass
                _LOG_PICK[0] = p
                return p
            except Exception:
                continue
    return None


def _write_crash(msg):
    """하위호환 래퍼 — 통합 로그로 수렴."""
    return _ulog('BOOT', msg)


def _log_migrate(new_path):
    """[일원화 2026-H3] 로그 파일을 new_path 로 '이관'한다.
    기존 파일 내용을 앞에 이어붙이고 원본을 지워, 어느 시점에도
    ermon.log 가 두 개로 존재하지 않도록 보장한다."""
    if not new_path:
        return _LOG_PICK[0]
    with _LOG_IO:
        old = _LOG_PICK[0]
        if old and _os.path.abspath(old) == _os.path.abspath(new_path):
            return old
        try:
            _os.makedirs(_os.path.dirname(new_path) or '.', exist_ok=True)
            prev = ''
            if old and _os.path.exists(old):
                with open(old, encoding='utf-8', errors='replace') as f:
                    prev = f.read()
            with open(new_path, 'a', encoding='utf-8', errors='replace') as f:
                if prev:
                    f.write(prev)
                f.write('%s [BOOT] 로그 이관: %s -> %s\n'
                        % (_time.strftime('%m-%d %H:%M:%S'), old, new_path))
                f.flush()
                try:
                    _os.fsync(f.fileno())
                except Exception:
                    pass
            if old and prev and _os.path.exists(old):
                try:
                    _os.remove(old)              # 분산 방지: 원본 제거
                except Exception:
                    pass
            if new_path not in _LOG_CANDIDATES:
                _LOG_CANDIDATES.insert(0, new_path)
            _LOG_PICK[0] = new_path
            return new_path
        except Exception:
            return old


def _log_path():
    return _LOG_PICK[0] or (_LOG_CANDIDATES[0] if _LOG_CANDIDATES else '?')


def _log_size_str():
    try:
        return '%.1f KB' % (_os.path.getsize(_log_path()) / 1024.0)
    except Exception:
        return '크기불명'


def _log_strays():
    """현재 사용 중이 아닌 ermon.log 잔재를 찾아 알린다(일원화 자체검증)."""
    cur = _os.path.abspath(_log_path())
    out = []
    for p in _LOG_CANDIDATES:
        try:
            if _os.path.exists(p) and _os.path.abspath(p) != cur:
                out.append('%s (%d B)' % (p, _os.path.getsize(p)))
        except Exception:
            continue
    return ' / '.join(out)


def _install_crash_hooks():
    """메인/서브 스레드의 미처리 예외를 전부 통합 로그에 남긴다.
    (이전에는 Flask 워커 스레드 예외가 어디에도 기록되지 않았다)"""
    import traceback as _tb

    def _hook(et, ev, tb):
        try:
            _ulog('CRASH', 'UNCAUGHT %s: %s\n%s'
                  % (getattr(et, '__name__', et), ev,
                     ''.join(_tb.format_exception(et, ev, tb))))
        except Exception:
            pass

    try:
        _sys.excepthook = _hook
    except Exception:
        pass

    def _thook(a):
        try:
            _ulog('CRASH', 'THREAD %s %s: %s\n%s'
                  % (getattr(getattr(a, 'thread', None), 'name', '?'),
                     getattr(a.exc_type, '__name__', a.exc_type), a.exc_value,
                     ''.join(_tb.format_exception(a.exc_type, a.exc_value,
                                                  a.exc_traceback))))
        except Exception:
            pass

    try:
        _th0.excepthook = _thook
    except Exception:
        pass
    try:
        import atexit as _atexit
        _atexit.register(lambda: _ulog('BOOT', '프로세스 종료(atexit)'))
    except Exception:
        pass


_install_crash_hooks()
_ulog('BOOT', '=' * 58)

_write_crash(f'[0] Python started, __name__={__name__}')
_write_crash(f'[0] sys.version={_sys.version}')
if hasattr(_sys, 'getandroidapilevel'):
    _write_crash(f'[0] Android API={_sys.getandroidapilevel()}')

# ── STEP 1: 핵심 라이브러리 import (하나씩 테스트) ──
_imports_ok = []
_imports_fail = []

for _lib in ['flask', 'requests', 'xml.etree.ElementTree', 'json', 
             'datetime', 'time', 'traceback', 'threading', 'logging']:
    try:
        __import__(_lib)
        _imports_ok.append(_lib)
    except Exception as _e:
        _imports_fail.append(f'{_lib}: {_e}')

_write_crash(f'[1] Imports OK: {",".join(_imports_ok)}')
if _imports_fail:
    _write_crash(f'[1] Imports FAILED: {";".join(_imports_fail)}')

# 이제 정식 import
from flask import Flask, render_template_string, jsonify, request, redirect
import requests
import xml.etree.ElementTree as ET
import json
from datetime import datetime
import time
import traceback
import threading
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

_write_crash('[2] Creating Flask app...')
try:
    flask_app = Flask(__name__)
    _write_crash('[2] Flask app created OK')
except Exception as _e:
    _write_crash(f'[2] Flask FAILED: {_e}')
    raise

# ── 브라우저 → Kivy PiP 요청 공유 상태 (Flask 스레드가 쓰고, Kivy Clock이 읽음)
_pip_state = {'pending': False, 'h_param': '', 'iv_sec': 180}
#  [FIX 2026-H2] PiP 시도 결과를 브라우저/진단화면에서 즉시 확인하기 위한 슬롯
_PIP_LAST = {'stage': '', 'ok': False, 'reason': '', 'ts': 0.0, 'api': 0,
             'device_feature': None, 'manifest_flag': None}

# ══════════════════════════════════════════════════════════════════
#  [ROOT-FIX 2026-E1] 프로세스 식별 / 완전종료 훅
#  근본원인: on_pause() 가 무조건 True 를 반환하고 WakeLock 을 유지해
#  프로세스가 절대 죽지 않는다. 재실행 시 구 프로세스가 5000 포트를
#  점유한 채 남아 신규 인스턴스의 Flask 가 bind 실패 → UI 는 새것,
#  데이터는 구 인스턴스(오래된 로스터)라는 좀비 상태가 된다.
#  → 기동 시 /api/whoami 로 타 인스턴스를 식별하고 /api/app_exit 로
#    자동 종료시킨 뒤 포트를 인수한다.
# ══════════════════════════════════════════════════════════════════
_APP_PID     = os.getpid()
_APP_BOOT_TS = time.time()
_EXIT_HOOKS  = []          # 완전종료 시 실행할 콜러블 (Kivy 가 등록)


def _register_exit_hook(fn):
    if fn not in _EXIT_HOOKS:
        _EXIT_HOOKS.append(fn)

# ── 동기화: 마지막 데이터 갱신 타임스탬프 (브라우저↔PiP 동기화용) ─
_refresh_notify_ts = [0.0]

# ── 햅틱: 브라우저 → Kivy 햅틱 요청 플래그 ─────────────────────────
_haptic_pending = [False]

# ── PiP 병상 합계 캐시: HVS 태그가 일시적으로 누락된 경우 최후 양수값 사용
# {hpid: {'hvec_t': int, 'hvgc_t': int, 'hv36_t': int, 'hicu_t': int}}
_pip_bed_total_cache = {}

# ── 비교화면 최신 병상 데이터 공유 캐시 ─────────────────────────────────
# /compare가 API를 호출할 때마다 여기에 저장되며,
# /pip_data는 이 캐시에 유효한 값이 있으면 API를 재호출하지 않고 재사용한다.
# 이를 통해 두 화면의 데이터 일관성을 보장하고 API 이중 호출을 방지한다.
# {hpid: {'hvec':int, 'hvgc':int, 'hv36':int, 'hicu':int,
#          'hvec_t':int, 'hvgc_t':int, 'hv36_t':int, 'hicu_t':int,
#          'fetched_at':str}}
import threading as _threading
_compare_bed_cache: dict = {}
_compare_bed_cache_lock = _threading.Lock()

# ══════════════════════════════════════════════════════════════════
#  [최적화] 공용 네트워크 인프라
#  - _http_get: 스레드별 requests.Session 재사용 (TCP+TLS keep-alive)
#    → 매 호출마다 새 연결을 만드는 requests.get 대비 핸드셰이크 제거.
#    요청 파라미터/타임아웃/응답 처리 방식은 requests.get과 100% 동일.
#  - _NET_POOL: 요청마다 ThreadPoolExecutor를 새로 만들지 않고 공유.
# ══════════════════════════════════════════════════════════════════
_thread_http = _threading.local()

_API_KEYS = ('Q0', 'Q1', 'QZ', 'QN', 'STAGE1', 'STAGE2', 'HPID', 'pageNo', 'numOfRows')


def _http_get(url, **kwargs):
    """requests.get과 동일 시그니처. 스레드-로컬 Session으로 연결 재사용.
    [일원화 2026-H1] 모든 외부 API 호출을 op·파라미터·상태·바이트·소요와
    함께 통합 로그에 남긴다. '어떤 질의가 무엇을 돌려줬는지'가 파일 하나에
    남아야 원인 추적이 가능하다."""
    _t0 = _time.time()
    _op = url.rsplit('/', 1)[-1]
    try:
        _pp = kwargs.get('params') or {}
        _ps = ' '.join('%s=%s' % (k, _pp[k]) for k in _API_KEYS if k in _pp)
    except Exception:
        _ps = ''
    try:
        _r = _http_get_raw(url, **kwargs)
        try:
            _n = len(_r.content)
        except Exception:
            _n = -1
        _ulog('API', '%s %s -> %s %dB %dms'
              % (_op, _ps, _r.status_code, _n, int((_time.time() - _t0) * 1000)))
        return _r
    except Exception as _e:
        _ulog('API', '%s %s -> EXC %s (%dms)'
              % (_op, _ps, str(_e)[:120], int((_time.time() - _t0) * 1000)))
        raise


def _http_get_raw(url, **kwargs):
    s = getattr(_thread_http, 'session', None)
    if s is None:
        s = requests.Session()
        try:
            _ad = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=12)
            s.mount('https://', _ad)
            s.mount('http://', _ad)
        except Exception:
            pass
        _thread_http.session = s
    try:
        return s.get(url, **kwargs)
    except requests.exceptions.ConnectionError:
        # keep-alive 연결이 서버측에서 끊긴 경우 새 세션으로 1회 재시도
        # (requests.get은 매번 새 연결이므로 이 경우가 없음 → 동작 등가 보장용)
        try:
            s.close()
        except Exception:
            pass
        s = requests.Session()
        _thread_http.session = s
        return s.get(url, **kwargs)

# 공유 워커 풀: compare/pip_data 의 병렬 API 호출에 사용
_NET_POOL = ThreadPoolExecutor(max_workers=12, thread_name_prefix='netpool')

# ── [최적화] Jinja 템플릿 컴파일 캐시 ─────────────────────────────
#  render_template_string은 호출 때마다 대형 템플릿을 재컴파일한다.
#  동일 소스 문자열은 1회만 컴파일해 재사용 (렌더링 결과는 동일).
_tmpl_cache = {}
_tmpl_cache_lock = _threading.Lock()

def _render_cached(source, **context):
    tmpl = _tmpl_cache.get(source)
    if tmpl is None:
        with _tmpl_cache_lock:
            tmpl = _tmpl_cache.get(source)
            if tmpl is None:
                tmpl = flask_app.jinja_env.from_string(source)
                _tmpl_cache[source] = tmpl
    flask_app.update_template_context(context)
    return tmpl.render(context)

# 전역 로그 파일 경로 / 참조 (Android에서 초기화됨)
# Flask 라우트와 Kivy 클래스가 동일 리스트 객체를 공유한다.
LOG_FILE = None
_LOG_FILE_REF = [None]

def _log(msg, level='INFO'):
    """[일원화] 통합 로그(ermon.log) 단일 경로."""
    _ulog('APP' if level == 'INFO' else level, msg)

# ── 메모리 내 디버그 로그 (Kivy TextInput 패널에 실시간 표시됨) ─────────
# Flask 스레드·Kivy 스레드 모두에서 안전하게 append 가능 (GIL 보장).
_DEBUG_LINES = []

def _dlog(msg):
    """모든 처리 과정을 메모리(_DEBUG_LINES)와 파일/stdout에 동시 기록.
    Kivy 디버그 TextInput 패널이 이 리스트를 읽어 실시간 표시한다.
    """
    try:
        ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]
    except Exception:
        ts = '??:??:??.???'
    entry = f'{ts} {msg}'
    _DEBUG_LINES.append(entry)
    if len(_DEBUG_LINES) >300:
        _DEBUG_LINES.pop(0)
    _ulog('APP', msg)

# ══════════════════════════════════════════════════════════════════
#  [일원화 2026-H1] 단일 상태/설정 파일  ermon_state.json
#   이전: pip_prefs.json · emergency_state.json · bed_monitor.json 3종이
#         서로 다른 경로에 흩어져 있어, 재시작 후 상태 불일치의 원인을
#         추적할 수 없었다.
#   현재: 한 파일 안의 섹션(pip_prefs / pip_state / monitor)으로 통합.
#   모든 읽기/쓰기가 통합 로그에 기록된다.
# ══════════════════════════════════════════════════════════════════
STATE_NAME = 'ermon_state.json'
#  로그와 동일 정책: 부트스트랩(main.py)이 정한 위치를 1순위로 인계받는다.
_STATE_CANDIDATES = []
_ENV_STATE = (os.environ.get('ERMON_STATE') or '').strip()
if _ENV_STATE:
    _STATE_CANDIDATES.append(_ENV_STATE)
if _ENV_IO:
    _STATE_CANDIDATES.append(os.path.join(_ENV_IO, STATE_NAME))
_SRC_STATE = ''
try:
    _SRC_STATE = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), STATE_NAME)
except Exception:
    pass
if _SRC_STATE and not _ANDROID_BOOT:
    _STATE_CANDIDATES.append(_SRC_STATE)
_STATE_CANDIDATES += [
    '/storage/emulated/0/Download/' + STATE_NAME,
    '/sdcard/Download/' + STATE_NAME,
]
if _SRC_STATE and _ANDROID_BOOT:
    _STATE_CANDIDATES.append(_SRC_STATE)
_STATE_CANDIDATES += [
    '/data/local/tmp/' + STATE_NAME,
    os.path.join(os.path.expanduser('~'), STATE_NAME),
]
_STATE_CANDIDATES = [p for i, p in enumerate(_STATE_CANDIDATES)
                     if p and p not in _STATE_CANDIDATES[:i]]
_STATE_PICK = [None]
_STATE_LOCK = _threading.Lock()


def _state_load_all():
    for p in ([_STATE_PICK[0]] if _STATE_PICK[0] else _STATE_CANDIDATES):
        try:
            if not os.path.exists(p):
                continue
            with open(p, encoding='utf-8') as f:
                d = json.loads(f.read() or '{}')
            if isinstance(d, dict):
                _STATE_PICK[0] = p
                return d
        except Exception as e:
            _ulog('STATE', '읽기 실패 %s: %s' % (p, e))
    return {}


def _state_save_all(d):
    for p in ([_STATE_PICK[0]] if _STATE_PICK[0] else _STATE_CANDIDATES):
        try:
            with open(p, 'w', encoding='utf-8') as f:
                f.write(json.dumps(d, ensure_ascii=False))
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
            _STATE_PICK[0] = p
            return p
        except Exception:
            continue
    _ulog('STATE', '저장 실패: 쓰기 가능한 경로 없음')
    return None


def _state_get(section, default=None):
    with _STATE_LOCK:
        v = _state_load_all().get(section)
    _ulog('STATE', 'get %s -> %s' % (section, '있음' if v else '없음'))
    return default if v is None else v


def _state_set(section, value):
    """value=None 이면 섹션 삭제."""
    with _STATE_LOCK:
        d = _state_load_all()
        if value is None:
            d.pop(section, None)
        else:
            d[section] = value
        p = _state_save_all(d)
    _ulog('STATE', 'set %s (%s) -> %s'
          % (section, '삭제' if value is None else '저장', p))
    return p


def _state_path():
    return _STATE_PICK[0] or (_STATE_CANDIDATES[0] if _STATE_CANDIDATES else '?')


SERVICE_KEY = 'ac084c52bdaee51ccc5d0beedacbed40db1995171f5b980ae3549de259b2db3e'
_write_crash('[2] Flask routes registering... BUILD=2026-H1')
API_URL = 'https://apis.data.go.kr/B552657/ErmctInfoInqireService/getEmrrmRltmUsefulSckbdInfoInqire'
MSG_API_URL = 'https://apis.data.go.kr/B552657/ErmctInfoInqireService/getEmrrmSrsillDissMsgInqire'
LIST_API_URL = 'https://apis.data.go.kr/B552657/ErmctInfoInqireService/getEgytListInfoInqire'
#  중증질환자 수용가능정보 — 목록 API 와 다른 백엔드 테이블을 쓰므로
#  Q0 계열이 전멸한 시/도의 hpid 확보용 대체 축으로 사용한다.
STRM_API_URL = 'https://apis.data.go.kr/B552657/ErmctInfoInqireService/getStrmListInfoInqire'
#  기관 기본정보 — HPID 단건 조회. '지역 필터'가 아니라 '기관 키' 조회이므로
#  시/도 파라미터가 전멸해도 유일하게 살아남는 축이다. 주소 확정에 사용.
BASS_API_URL = 'https://apis.data.go.kr/B552657/ErmctInfoInqireService/getEgytBassInfoInqire'
BUILD_ID = '2026-H1'

# ══════════════════════════════════════════════════════════════════
#  [ROOT-CAUSE 2026-H1] 행정구역 개편 반영
#   2026-07-01 「전남광주통합특별시 설치 및 지원에 관한 특별법」시행으로
#   광주광역시와 전라남도가 폐지되고 전남광주통합특별시(광주 5구 + 전남
#   22시군 = 27개 기초자치단체)로 통합되었다. 주소 표기도
#     '광주광역시 동구'  → '전남광주통합특별시 동구'
#     '전라남도 순천시'  → '전남광주통합특별시 순천시'
#   로 바뀌었다.
#
#   ※ 그동안의 '광주 0건' 은 공공API 결함이 아니었다. API 는 정상
#     (Q0=광주 → 73건)이었고, 앱의 행정구역 테이블이 낡아서
#     73건 전부가 sido='전라남도' 로 흡수되어 '광주광역시' 건수가
#     영원히 0 이 되었던 것이다(별칭 '전남' 이 '전남광주통합특별시'
#     접두를 삼킨 것이 결정타).
# ══════════════════════════════════════════════════════════════════
_GJ5 = ['광산구', '남구', '동구', '북구', '서구']                  # 구 광주광역시
_JN22 = ['목포시', '여수시', '순천시', '나주시', '광양시',
         '담양군', '곡성군', '구례군', '고흥군', '보성군', '화순군',
         '장흥군', '강진군', '해남군', '영암군', '무안군', '함평군',
         '영광군', '장성군', '완도군', '진도군', '신안군']          # 구 전라남도
SIDO_MERGED = '전남광주통합특별시'
#  통합시는 '광주권 5구 → 전남권 22시군' 순서를 유지한다(가나다 정렬 제외)
DISTRICTS = {k: (v if k == SIDO_MERGED else sorted(v)) for k, v in {
    '서울특별시': ['강남구','강동구','강북구','강서구','관악구','광진구','구로구','금천구','노원구','도봉구','동대문구','동작구','마포구','서대문구','서초구','성동구','성북구','송파구','양천구','영등포구','용산구','은평구','종로구','중구','중랑구'],
    '부산광역시': ['강서구','금정구','남구','동구','동래구','부산진구','북구','사상구','사하구','서구','수영구','연제구','영도구','중구','해운대구','기장군'],
    '대구광역시': ['남구','달서구','동구','북구','서구','수성구','중구','달성군'],
    '인천광역시': ['계양구','남구','남동구','동구','부평구','서구','연수구','중구','강화군','옹진군'],
    '전남광주통합특별시': _GJ5 + _JN22,
    '대전광역시': ['대덕구','동구','서구','유성구','중구'],
    '울산광역시': ['남구','동구','북구','중구','울주군'],
    '세종특별자치시': ['세종특별자치시'],
    '경기도': ['수원시','성남시','고양시','용인시','부천시','안산시','안양시','남양주시','화성시','평택시','의정부시','시흥시','파주시','광명시','김포시','군포시','광주시','이천시','양주시','오산시','구리시','안성시','포천시','의왕시','하남시','여주시','양평군','동두천시','과천시','가평군','연천군'],
    '강원특별자치도': ['춘천시','원주시','강릉시','동해시','태백시','속초시','삼척시','홍천군','횡성군','영월군','평창군','정선군','철원군','화천군','양구군','인제군','고성군','양양군'],
    '충청북도': ['청주시','충주시','제천시','보은군','옥천군','영동군','증평군','진천군','괴산군','음성군','단양군'],
    '충청남도': ['천안시','공주시','보령시','아산시','서산시','논산시','계룡시','당진시','금산군','부여군','서천군','청양군','홍성군','예산군','태안군'],
    '전북특별자치도': ['전주시','군산시','익산시','정읍시','남원시','김제시','완주군','진안군','무주군','장수군','임실군','순창군','고창군','부안군'],
    '경상북도': ['포항시','경주시','김천시','안동시','구미시','영주시','영천시','상주시','문경시','경산시','군위군','의성군','청송군','영양군','영덕군','청도군','고령군','성주군','칠곡군','예천군','봉화군','울진군','울릉군'],
    '경상남도': ['창원시','진주시','통영시','사천시','김해시','밀양시','거제시','양산시','의령군','함안군','창녕군','고성군','남해군','하동군','산청군','함양군','거창군','합천군'],
    '제주특별자치도': ['제주시','서귀포시']
}.items()}

HTML = '''
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>응급의료기관 정보</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body {
            font-family: 'Malgun Gothic', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: clamp(10px, 3vw, 20px);
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            padding: clamp(20px, 4vw, 30px);
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }
        h1 {
            text-align: center;
            color: #667eea;
            margin-bottom: 30px;
            font-size: clamp(1.5rem, 5vw, 2.2rem);
        }
        .form-group { margin-bottom: 20px; }
        label {
            display: block;
            margin-bottom: 8px;
            font-weight: 600;
            color: #333;
            font-size: clamp(0.95rem, 2.5vw, 1.05rem);
        }
        select {
            width: 100%;
            padding: 6px 12px;
            line-height: 1.2;
            border: 2px solid #ddd;
            border-radius: 10px;
            font-size: clamp(1rem, 2.5vw, 1.1rem);
            background: white;
        }
        select:focus { outline: none; border-color: #667eea; }
        select:disabled { background: #f0f0f0; }
        .btn { text-align: center; justify-content: center; }
        button, .sat-btn, .lv-btn { text-align: center; }
        /* 등급(권역/센터/기관/모두) · 병상 포화도 버튼 높이 통일 */
        .filter-row .lv-btn, .filter-row .sat-btn {
            height: 2.6rem; box-sizing: border-box;
            display: inline-flex; align-items: center; justify-content: center;
            padding: 0 10px; line-height: 1;
        }
        .btn {
            width: 100%;
            padding: 8px 14px;
            line-height: 1.2;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 10px;
            font-size: clamp(1.05rem, 2.8vw, 1.15rem);
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s;
        }
        .btn:hover:not(:disabled) { transform: translateY(-2px); }
        .btn:disabled { background: #ccc; cursor: not-allowed; }
        .mode-tabs { display: flex; gap: 8px; margin-bottom: 10px; }
        .mode-tab {
            flex: 1; display: flex; align-items: center; justify-content: center;
            text-align: center; margin: 0;
            /* select 와 동일 높이: padding 6px 12px + line-height 1.2 + border 2px */
            padding: 6px 12px; line-height: 1.2;
            border: 2px solid #ddd; border-radius: 10px;
            font-weight: 700; color: #666; cursor: pointer; user-select: none;
            font-size: clamp(1rem, 2.5vw, 1.1rem);
        }
        .mode-tab input { display: none; }
        .mode-tab.active { border-color: #667eea; background: #eef1ff; color: #4a5bbf; }

        /* ── 등급(조합) 필터 + 포화도 진입 ── */
        .filter-row { display: flex; gap: 6px; align-items: stretch; margin-bottom: 12px; }
        .lv-group { display: flex; gap: 4px; flex: 1; }
        .lv-btn {
            flex: 1; padding: 4px 2px; line-height: 1.15;
            border: 2px solid #ddd; border-radius: 8px; background: #fff;
            font-size: 0.85rem; font-weight: 700; color: #777;
            cursor: pointer; white-space: nowrap;
        }
        .lv-btn.active { color: #fff; border-color: transparent; }
        .lv-btn.active[data-lv="권역"] { background: #4217d1; }
        .lv-btn.active[data-lv="센터"] { background: #0d6655; }
        .lv-btn.active[data-lv="기관"] { background: #2c4570; }
        .lv-btn.active[data-lv="모두"] { background: #667eea; }
        .sat-btn {
            padding: 4px 10px; line-height: 1.15; border: none; border-radius: 8px;
            background: linear-gradient(135deg, #e07a3a, #c0392b); color: #fff;
            font-size: 0.85rem; font-weight: 700; cursor: pointer; white-space: nowrap;
        }
        .sat-btn:disabled { background: #ccc; cursor: not-allowed; }
        .clear-btn {
            padding: 6px 10px; border: 2px solid #d9a400; border-radius: 8px;
            background: #fff; color: #8a6d00; font-size: 0.85rem;
            font-weight: 700; cursor: pointer; white-space: nowrap;
        }
        .clear-btn:disabled { border-color: #ddd; color: #bbb; cursor: not-allowed; }
        .pickall-btn {
            padding: 5px 12px; border: none; border-radius: 8px; background: #2e7d64;
            color: #fff; font-size: 0.82rem; font-weight: 700; cursor: pointer; white-space: nowrap;
        }
        .res-head {
            display: flex; align-items: center; justify-content: space-between;
            gap: 8px; margin: 16px 0 10px; font-weight: 600; flex-wrap: wrap;
        }
        .detail-btn {
            padding: 3px 9px; border: 1px solid #b9c2e8; border-radius: 7px;
            background: #f3f6ff; color: #4a5bbf; font-size: 0.76rem;
            font-weight: 700; cursor: pointer; white-space: nowrap;
        }
        .bedline { display: flex; align-items: center; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
        .bedchip {
            font-size: 0.75rem; font-weight: 700; padding: 1px 7px;
            border-radius: 5px; background: #eef0f4; color: #445;
        }

        /* ── 공용 막대 (100% 기준선 · 초과분 구분) ── */
        .cap-bar {
            position: relative; display: flex; height: 9px; width: 100%;
            border-radius: 5px; background: #e9e9e9; overflow: hidden; margin-top: 5px;
        }
        .cap-bar i, .cap-bar b { display: block; height: 100%; }
        .cap-bar b { background: #ff1744; }   /* 초과분: 단색(빗금 제거) */
        .cap-tick { position: absolute; top: -2px; bottom: -2px; width: 2px;
                    background: #263238; opacity: 0.75; }
        .cap-bar { overflow: visible; }
        .cap-wrap { position: relative; }
        .cap-scale { font-size: 0.66rem; color: #aaa; text-align: right; margin-top: 1px; }

        /* ── 병상 포화도 오버레이 ── */
        #satOverlay {
            position: fixed; inset: 0; z-index: 99997; background: #f5f7fa;
            display: flex; flex-direction: column;
        }
        .sat-head {
            flex: 0 0 auto; background: #fff; border-bottom: 2px solid #e0e0e0;
            padding: 8px 10px; display: block;
        }
        .sat-head-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
        .sat-head-top h2 { font-size: 0.98rem; color: #222; white-space: nowrap; }
        .sat-head .sub { font-size: 0.7rem; color: #888; font-weight: 400; margin-top: 2px; line-height: 1.35; }
        .sat-actions { display: flex; gap: 6px; flex-shrink: 0; }
        .sat-close, .sat-refresh {
            padding: 5px 11px; border: none; border-radius: 9px;
            color: #fff; font-weight: 700; font-size: 0.82rem; cursor: pointer; white-space: nowrap;
        }
        .sat-close { background: #444; }
        .sat-refresh { background: #2e7d64; }
        .sat-refresh:disabled { background: #bbb; cursor: not-allowed; }
        .sat-row { display: flex; gap: 4px; margin-top: 7px; }
        .mt-btn {
            flex: 1; padding: 4px 2px; line-height: 1.15;
            border: 2px solid #ddd; border-radius: 8px; background: #fff;
            font-size: 0.78rem; font-weight: 700; color: #777;
            cursor: pointer; white-space: nowrap;
        }
        .mt-btn.active { background: #37474f; border-color: transparent; color: #fff; }
        .mt-btn.active[data-mt="load"] { background: #ad1457; }
        .mt-info, .sort-btn {
            flex: 0 0 auto; padding: 4px 9px; border: 2px solid #ddd; border-radius: 8px;
            background: #fff; color: #666; font-size: 0.78rem; font-weight: 700;
            cursor: pointer; white-space: nowrap;
        }
        .sat-row select {
            flex: 1; min-width: 0; padding: 3px 6px; line-height: 1.2;
            border: 2px solid #ddd; border-radius: 8px; font-size: 0.8rem; background: #fff;
        }
        .sat-body { flex: 1 1 auto; overflow-y: auto; padding: 9px; -webkit-overflow-scrolling: touch; }
        .sat-item {
            background: #fff; border: 2px solid #e8e8e8; border-radius: 10px;
            padding: 7px 9px; margin-bottom: 6px; display: flex; gap: 8px; align-items: flex-start;
        }
        .sat-item.selected { border-color: #667eea; background: #f2f5ff; }
        .sat-item input { width: 19px; height: 19px; flex-shrink: 0; margin-top: 3px; cursor: pointer; }
        .sat-main { flex: 1; min-width: 0; }
        .sat-name { font-size: 0.92rem; font-weight: 700; color: #222; line-height: 1.3; }
        .sat-meta { font-size: 0.74rem; color: #666; margin-top: 2px; }
        .sat-rank { font-size: 0.7rem; color: #aaa; font-weight: 700; min-width: 20px; text-align: right; }
        .sat-pct { font-weight: 800; font-size: 0.86rem; }
        .sat-formula {
            display: none; background: #fffdf3; border: 2px solid #e6d9a8; border-radius: 10px;
            padding: 10px 12px; margin-bottom: 9px; font-size: 0.75rem; color: #5a4a20; line-height: 1.65;
        }
        .sat-formula.show { display: block; }
        .sat-formula code {
            background: #f2ecd8; padding: 1px 5px; border-radius: 4px;
            font-family: monospace; font-size: 0.95em;
        }

        /* ── 자세히 팝업 ── */
        #dtOverlay {
            position: fixed; inset: 0; z-index: 99999; background: rgba(0,0,0,0.5);
            display: flex; align-items: flex-end; justify-content: center;
        }
        .dt-card {
            background: #fff; width: 100%; max-width: 640px; max-height: 88vh;
            border-radius: 16px 16px 0 0; display: flex; flex-direction: column;
        }
        .dt-head {
            padding: 12px 14px; border-bottom: 2px solid #eee;
            display: flex; align-items: flex-start; justify-content: space-between; gap: 8px;
        }
        .dt-head h3 { font-size: 1rem; color: #222; line-height: 1.35; }
        .dt-body { padding: 12px 14px; overflow-y: auto; -webkit-overflow-scrolling: touch; }
        .dt-sec { margin-bottom: 14px; }
        .dt-sec h4 {
            font-size: 0.82rem; color: #667eea; margin-bottom: 6px;
            border-bottom: 1px solid #eef1ff; padding-bottom: 3px;
        }
        .dt-kv { display: flex; gap: 8px; font-size: 0.82rem; padding: 3px 0; line-height: 1.45; }
        .dt-kv span:first-child { color: #888; flex: 0 0 82px; }
        .dt-kv span:last-child { color: #222; flex: 1; word-break: break-all; }
        .dt-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 10px; }
        .dt-cell { font-size: 0.78rem; display: flex; justify-content: space-between; gap: 6px; padding: 2px 0; }
        .dt-cell span:first-child { color: #777; }
        .dt-cell span:last-child { font-weight: 700; color: #222; }
        .region-tag {
            display: inline-block; padding: 1px 7px; border-radius: 4px;
            font-size: 0.78em; font-weight: 700;
            background: #eef1ff; color: #4a5bbf; margin-bottom: 4px;
        }
        .selected-box {
            background: #fff3cd;
            border: 2px solid #ffc107;
            border-radius: 12px;
            padding: 15px;
            margin-bottom: 20px;
            display: none;
        }
        .selected-box.show { display: block; }
        .selected-title {
            font-weight: 700;
            color: #856404;
            margin-bottom: 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
        }
        .selected-item {
            display: flex;
            align-items: center;
            gap: 10px;
            background: white;
            padding: 10px;
            border-radius: 8px;
            margin-bottom: 8px;
        }
        .selected-item input { width: 20px; height: 20px; }
        .hospital-item {
            background: white;
            border: 2px solid #e0e0e0;
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 12px;
            display: flex;
            gap: 12px;
        }
        .hospital-item.selected { border-color: #667eea; background: #f0f4ff; }
        .hospital-checkbox { width: 22px; height: 22px; cursor: pointer; flex-shrink: 0; }
        .hospital-content { flex: 1; }
        .hospital-name {
            font-size: clamp(1.05rem, 2.6vw, 1.15rem);
            font-weight: 600;
            margin-bottom: 8px;
            color: #333;
        }
        .hospital-info {
            color: #666;
            font-size: clamp(0.9rem, 2.2vw, 1rem);
            line-height: 1.6;
        }
        .loading { text-align: center; padding: 40px; color: #667eea; }
        .error {
            background: #ffe0e0;
            color: #d32f2f;
            padding: 15px;
            border-radius: 10px;
            margin: 20px 0;
        }
        .error-detail {
            background: #fff;
            border: 2px solid #d32f2f;
            border-radius: 10px;
            padding: 15px;
            margin: 20px 0;
            max-height: 300px;
            overflow-y: auto;
        }
        .error-detail h3 { color: #d32f2f; margin-bottom: 10px; font-size: 1.1rem; }
        .error-detail pre {
            background: #f5f5f5;
            padding: 10px;
            border-radius: 5px;
            font-size: 0.85rem;
            white-space: pre-wrap;
            word-wrap: break-word;
            margin: 10px 0;
        }
        .error-actions { display: flex; gap: 10px; margin-top: 15px; flex-wrap: wrap; }
        .error-actions button {
            flex: 1; min-width: 120px; padding: 12px;
            border: none; border-radius: 8px; font-weight: 600; cursor: pointer; transition: all 0.2s;
        }
        .btn-copy { background: #2196F3; color: white; }
        .btn-copy:hover { background: #1976D2; }
        .btn-restart { background: #4CAF50; color: white; }
        .btn-restart:hover { background: #388E3C; }
        .copied-toast {
            position: fixed; top: 20px; right: 20px;
            background: #4CAF50; color: white; padding: 15px 20px;
            border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            z-index: 1000; animation: slideIn 0.3s ease-out;
        }
        @keyframes slideIn { from { transform: translateX(400px); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
        .spinner {
            border: 3px solid #f3f3f3; border-top: 3px solid #667eea;
            border-radius: 50%; width: 40px; height: 40px;
            animation: spin 1s linear infinite; margin: 20px auto;
        }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    </style>
</head>
<body>
    <div class="container">
        <h1>응급의료기관 정보</h1>
        <div class="mode-tabs" id="modeTabs">
            <label class="mode-tab active" data-mode="region" style="flex:2 1 0;">
                <input type="radio" name="smode" value="region" checked>지역 검색</label>
            <label class="mode-tab" data-mode="name" style="flex:2 1 0;">
                <input type="radio" name="smode" value="name">병원 검색</label>
            <button type="button" class="mode-tab" id="resetBtn" style="flex:1 1 0;cursor:pointer;background:#f3f4f6;">초기화</button>
        </div>
        <div id="regionPane">
            <div class="form-group" style="margin-bottom:8px;">
                <select id="sido"><option value="">시/도를 선택하세요 (선택 안 하면 전국)</option></select>
            </div>
            <div class="form-group" style="margin-bottom:8px;">
                <select id="gugun" disabled><option value="">시/군/구 (선택 안 하면 시/도 전체)</option></select>
            </div>
        </div>
        <div id="namePane" style="display:none;">
            <div class="form-group" style="margin-bottom:8px;">
                <input type="text" id="nameQuery" autocomplete="off"
                       placeholder="병원명 일부 입력 (예: 김천, 제일, 제생)"
                       style="width:100%;padding:5px 12px;line-height:1.2;border:2px solid #ddd;border-radius:8px;font-size:1rem;background:#fff;">
                <div id="nameQueryInfo" style="margin-top:6px;font-size:0.85rem;color:#666;"></div>
            </div>
        </div>
        <div class="filter-row">
            <div class="lv-group" id="lvGroup">
                <button type="button" class="lv-btn" data-lv="권역">권역</button>
                <button type="button" class="lv-btn" data-lv="센터">센터</button>
                <button type="button" class="lv-btn" data-lv="기관">기관</button>
                <button type="button" class="lv-btn active" data-lv="모두">모두</button>
            </div>
            <button type="button" class="sat-btn" id="satBtn">병상 포화도</button>
        </div>
        <div style="display:flex;gap:8px;align-items:stretch;">
            <button class="btn" id="resBtn" style="flex:1 1 0;min-width:0;margin-top:0;white-space:nowrap;overflow:hidden;padding:8px 4px;background:linear-gradient(135deg,#2f6f5f,#1d4c41);">자원 조건</button>
            <button class="btn" id="searchBtn" style="flex:2 1 0;min-width:0;margin-top:0;">병원 검색</button>
        </div>
        <button class="btn" id="saveAppBtn" style="margin-top:8px;background:linear-gradient(135deg,#556b8d,#3a4d6b);">저장 (단독 HTML — 선택+조회)</button>
        <div id="results"></div>
        <div class="selected-box" id="selectedBox">
            <div class="selected-title">
                <span id="selTitle">선택된 병원 (최대 5개)</span>
                <span style="display:flex;gap:6px;align-items:center;">
                    <button type="button" class="clear-btn" id="clearSelBtn" disabled>모두 해제</button>
                    <button class="btn" style="width:auto; padding:8px 16px; font-size:0.9rem;" id="compareBtn" disabled>정보보기
                    </button>
                </span>
            </div>
            <div id="selectedList"></div>
        </div>
    </div>

    <script>const districts = {{ districts|tojson }};

        // ──  표시 항목 · 순서 설정 (py/저장본 공용, localStorage 영구 기억) ──
        var EXSEC = (function () {
            var CATS = ['응급실', '중환자실', '격리진료구역', '입원실', '기타', '의료장비',
                        '중증질환 수용가능', '예외상황'];
            var MINSET = { '응급실': 1, '중환자실': 1, '입원실': 1, '예외상황': 1 };
            function load() {
                var c = null;
                try { c = JSON.parse(localStorage.getItem('exSections') || 'null'); } catch (e) {}
                if (!c || !c.order || !c.order.length) c = { order: CATS.slice(), hidden: {} };
                if (!c.hidden) c.hidden = {};
                CATS.forEach(function (nm) { if (c.order.indexOf(nm) === -1) c.order.push(nm); });
                c.order = c.order.filter(function (nm) { return CATS.indexOf(nm) !== -1; });
                return c;
            }
            function save(c) { try { localStorage.setItem('exSections', JSON.stringify(c)); } catch (e) {} }
            function groups(tb) {
                var out = [], cur = null;
                Array.prototype.forEach.call(tb.rows, function (tr) {
                    var td = tr.querySelector('td.category-header');
                    if (td) {
                        var nm = td.textContent.trim();
                        var name = CATS.filter(function (c2) { return nm.indexOf(c2) === 0; })[0] || nm;
                        cur = { name: name, rows: [tr] };
                        out.push(cur);
                    } else if (cur) {
                        cur.rows.push(tr);
                    }
                });
                return out;
            }
            function apply() {
                var tb = document.querySelector('.comparison-table tbody');
                if (!tb) return;
                var c = load(), by = {};
                groups(tb).forEach(function (g) { by[g.name] = g; });
                c.order.forEach(function (nm) {
                    var g = by[nm];
                    if (!g) return;
                    g.rows.forEach(function (r) {
                        tb.appendChild(r);
                        r.style.display = c.hidden[nm] ? 'none' : '';
                    });
                    delete by[nm];
                });
                Object.keys(by).forEach(function (nm) {
                    by[nm].rows.forEach(function (r) { tb.appendChild(r); });
                });
            }
            function panel() {
                var old = document.getElementById('secPanel');
                if (old) { old.remove(); return; }
                var c = load();
                var wrap = document.createElement('div');
                wrap.id = 'secPanel';
                wrap.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.45);'
                    + 'display:flex;align-items:center;justify-content:center;';
                function build() {
                    var rows = c.order.map(function (nm, i) {
                        return '<div style="display:flex;align-items:center;gap:8px;padding:6px 4px;'
                             + 'border-bottom:1px solid #eee;font-size:0.9rem;">'
                             + '<input type="checkbox" data-sc="' + i + '"'
                             + (c.hidden[nm] ? '' : ' checked') + '>'
                             + '<span style="flex:1;">' + nm + '</span>'
                             + '<button data-up="' + i + '" style="border:none;background:#eee;'
                             + 'border-radius:6px;padding:3px 9px;">▲</button>'
                             + '<button data-dn="' + i + '" style="border:none;background:#eee;'
                             + 'border-radius:6px;padding:3px 9px;">▼</button></div>';
                    }).join('');
                    wrap.innerHTML = '<div style="background:#fff;border-radius:14px;max-width:340px;'
                        + 'width:88vw;padding:16px;box-shadow:0 8px 30px rgba(0,0,0,0.3);">'
                        + '<div style="font-weight:700;margin-bottom:6px;">표시 항목 · 순서</div>'
                        + '<div style="max-height:52vh;overflow:auto;">' + rows + '</div>'
                        + '<div style="display:flex;gap:8px;margin-top:10px;">'
                        + '<button id="secMin" style="flex:1;padding:8px;border:none;border-radius:10px;'
                        + 'background:#667eea;color:#fff;font-weight:700;">최소</button>'
                        + '<button id="secAll" style="flex:1;padding:8px;border:none;border-radius:10px;'
                        + 'background:#e0e0e0;font-weight:700;">전체</button>'
                        + '<button id="secClose" style="padding:8px 12px;border:none;border-radius:10px;'
                        + 'background:#f5f5f5;">닫기</button></div></div>';
                }
                build();
                document.body.appendChild(wrap);
                wrap.addEventListener('click', function (e) {
                    var t = e.target;
                    if (t === wrap || t.id === 'secClose') { wrap.remove(); return; }
                    if (t.id === 'secMin') {
                        c.hidden = {};
                        c.order.forEach(function (nm) { if (!MINSET[nm]) c.hidden[nm] = true; });
                        save(c); apply(); build(); return;
                    }
                    if (t.id === 'secAll') { c.hidden = {}; save(c); apply(); build(); return; }
                    var up = t.getAttribute ? t.getAttribute('data-up') : null;
                    var dn = t.getAttribute ? t.getAttribute('data-dn') : null;
                    if (up !== null) {
                        var i = parseInt(up);
                        if (i >0) { var x = c.order[i]; c.order[i] = c.order[i - 1]; c.order[i - 1] = x; }
                        save(c); apply(); build(); return;
                    }
                    if (dn !== null) {
                        var j = parseInt(dn);
                        if (j < c.order.length - 1) {
                            var y = c.order[j]; c.order[j] = c.order[j + 1]; c.order[j + 1] = y;
                        }
                        save(c); apply(); build(); return;
                    }
                });
                wrap.addEventListener('change', function (e) {
                    var sc = e.target && e.target.getAttribute ? e.target.getAttribute('data-sc') : null;
                    if (sc === null) return;
                    var nm = c.order[parseInt(sc)];
                    if (e.target.checked) delete c.hidden[nm]; else c.hidden[nm] = true;
                    save(c); apply();
                });
            }
            try { var b = document.getElementById('secBtn'); if (b) b.onclick = panel; } catch (e) {}
            return { apply: apply, panel: panel };
        })();
        try { EXSEC.apply(); } catch (e) {}



        // ── 서버 연결 끊김 자동 재접속 (Pydroid 프로세스 재시작 대비) ──
        let _reconT = null;
        function startReconnect(after) {
            if (_reconT) return;
            const bar = document.createElement('div');
            bar.id = 'reconBar';
            bar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;'
                + 'background:#c62828;color:#fff;padding:8px;text-align:center;'
                + 'font-size:0.85rem;font-weight:600;';
            bar.textContent = ' 서버 연결 끊김 — Pydroid 3(파이썬 앱)를 다시 열어주세요. 자동 재접속 대기 중...';
            document.body.appendChild(bar);
            _reconT = setInterval(async () => {
                try {
                    const r = await fetch('/api/bed_notify_status', { cache: 'no-store' });
                    if (r.ok) {
                        clearInterval(_reconT); _reconT = null;
                        bar.style.background = '#2e7d32';
                        bar.textContent = ' 서버 재연결됨 — 다시 시도합니다';
                        setTimeout(() => { try { bar.remove(); } catch (e) {} }, 1500);
                        if (after) { try { after(); } catch (e) {} }
                    }
                } catch (e) {}
            }, 4000);
        }


        let hospitalsFullData = [];
        let selectedHospitals = [];
        let isSearching = false;
        const MAX_SEL = 5;

        const levelBadge = {
            '권역': '<span style="display:inline-block;padding:1px 6px;border-radius:4px;font-size:0.75em;font-weight:700;background:#4217d1;color:white;margin-right:5px;">권역</span>',
            '센터': '<span style="display:inline-block;padding:1px 6px;border-radius:4px;font-size:0.75em;font-weight:700;background:#0d6655;color:white;margin-right:5px;">센터</span>',
            '기관': '<span style="display:inline-block;padding:1px 6px;border-radius:4px;font-size:0.75em;font-weight:700;background:#2c4570;color:white;margin-right:5px;">기관</span>',
        };

        const sidoSelect  = document.getElementById('sido');
        const gugunSelect = document.getElementById('gugun');
        const searchBtn   = document.getElementById('searchBtn');
        const resultsDiv  = document.getElementById('results');
        const selectedBox = document.getElementById('selectedBox');
        const selectedList= document.getElementById('selectedList');
        const compareBtn  = document.getElementById('compareBtn');
        // 저장: location.href 이동 시 서버가 죽어 있으면 '연결 거부' 오류 페이지로
        // 이탈해 작업 내용을 잃는다. fetch + Blob 다운로드로 바꿔 페이지를 유지한다.
        async function saveStandaloneHtml() {
            const btn = document.getElementById('saveAppBtn');
            const old = btn ? btn.textContent : '';
            try {
                if (btn) { btn.disabled = true; btn.textContent = '저장 파일 생성 중...'; }
                const r = await fetch('/export', { cache: 'no-store' });
                if (!r.ok) throw new Error('HTTP ' + r.status);
                const html = await r.text();
                const url = URL.createObjectURL(new Blob([html], { type: 'text/html' }));
                const a = document.createElement('a');
                const p = new Date();
                const z = n => String(n).padStart(2, '0');
                a.href = url;
                a.download = 'er_app_' + p.getFullYear() + z(p.getMonth() + 1) + z(p.getDate())
                             + '_' + z(p.getHours()) + z(p.getMinutes()) + '.html';
                document.body.appendChild(a);
                a.click();
                setTimeout(function () {
                    try { a.remove(); URL.revokeObjectURL(url); } catch (e) {}
                }, 4000);
            } catch (e) {
                nsdbg('export FAIL ' + ((e && e.message) || e));
                alert('저장 실패: ' + ((e && e.message) || e)
                      + '\\n\\n서버(파이썬 앱)가 종료된 상태입니다. 앱을 다시 실행한 뒤 저장하십시오.'
                      + '\\n현재 화면의 검색 결과는 그대로 유지됩니다.');
            } finally {
                if (btn) { btn.disabled = false; btn.textContent = old; }
            }
        }
        try { document.getElementById('saveAppBtn').onclick = saveStandaloneHtml; } catch(e) {}

        for (const sido in districts) {
            const opt = document.createElement('option');
            opt.value = sido; opt.textContent = sido;
            sidoSelect.appendChild(opt);
        }

        var _dbgBuf = [], _dbgT = null;
        function nsdbg(m) {
            var ts = new Date().toTimeString().slice(0, 8);
            try { console.log(ts + ' [NS] ' + m); } catch (e) {}
            /*SRV-DBG-START*/
            // 요청 폭주 방지: 2초 단위로 모아 1회만 전송
            _dbgBuf.push(m);
            if (_dbgBuf.length >40) _dbgBuf.shift();
            if (_dbgT) return;
            _dbgT = setTimeout(function () {
                _dbgT = null;
                var payload = _dbgBuf.join(' | ');
                _dbgBuf = [];
                if (!payload) return;
                try { fetch('/api/ns_dbg?m=' + encodeURIComponent(payload), { cache: 'no-store' }); } catch (e) {}
            }, 2000);
            /*SRV-DBG-END*/
        }

        window.addEventListener('load', function() {
            try {
                const saved = localStorage.getItem('lastSelectedHospitals');
                if (saved) { selectedHospitals = JSON.parse(saved); updateSelectedBox(); }
            } catch(e) { console.warn('선택병원 복원 실패:', e); }
        });
        function saveToLocalStorage() {
            try { localStorage.setItem('lastSelectedHospitals', JSON.stringify(selectedHospitals)); }
            catch(e) { console.warn('저장 실패:', e); }
        }

        // ══════════════════════════════════════════════════════════════
        //  공용 유틸
        // ══════════════════════════════════════════════════════════════
        function _normTxt(v) { return String(v || '').toLowerCase().replace(/\\s+/g, ''); }
        function esc(v) {
            return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
                return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
            });
        }
        function pctTxt(v) { return (v === null || v === undefined) ? '—' : (Math.round(v * 100) + '%'); }
        function satColor(p) {          // 글자색 (적색 계열 명도 +20%)
            if (p === null || p === undefined) return '#bbb';
            if (p >= 100) return '#fd3838';
            if (p >= 90)  return '#ff6124';
            if (p >= 70)  return '#ffa726';
            if (p >= 40)  return '#ffe14d';
            return '#4fc35a';
        }
        function barColor(p) {          // 막대 0~100% 구간 (초과분 단색과 대비)
            if (p === null || p === undefined) return '#bbb';
            if (p >= 100) return '#ff8a80';
            return satColor(p);
        }
        // 막대: 스케일 동적(현재 목록 최대값 기준). 100% 지점에 기준선,
        //       초과분은 주황/황 빗금으로 0~100% 구간과 명확히 구분.
        function capBar(p, scale) {
            scale = scale || 150;
            var tick = (100 / scale * 100);
            if (p === null || p === undefined)
                return '<div class="cap-bar"><span class="cap-tick" style="left:' + tick + '%;"></span></div>';
            var under = Math.max(0, Math.min(100, p)) / scale * 100;
            var over  = Math.max(0, Math.min(scale, p) - 100) / scale * 100;
            return '<div class="cap-bar">'
                 + '<i style="width:' + under + '%;background:' + barColor(p) + ';"></i>'
                 + (over >0 ? '<b style="width:' + over + '%;"></b>' : '')
                 + '<span class="cap-tick" style="left:' + tick + '%;"></span></div>';
        }
        function bedTxt(b) {
            if (!b || b.t <= 0) return '정보없음';
            return (b.a < 0) ? ('0/' + b.t + ' 초과' + (-b.a)) : (b.a + '/' + b.t);
        }
        function showToast(message) {
            const toast = document.createElement('div');
            toast.className = 'copied-toast'; toast.textContent = message;
            document.body.appendChild(toast);
            setTimeout(() =>toast.remove(), 3000);
        }
        function restartApp() { location.reload(); }
        function copyErrorToClipboard() {
            const t = document.getElementById('errorText').textContent;
            navigator.clipboard.writeText(t).then(() =>showToast(' 복사됨')).catch(() => {
                const ta = document.createElement('textarea');
                ta.value = t; document.body.appendChild(ta); ta.select();
                document.execCommand('copy'); document.body.removeChild(ta);
                showToast(' 복사됨');
            });
        }
        function showDetailedError(error, context) {
            const txt = ['오류 시각: ' + new Date().toLocaleString('ko-KR'),
                         '컨텍스트: ' + context,
                         '메시지: ' + ((error && error.message) || error),
                         '스택: ' + ((error && error.stack) || '-'),
                         'UA: ' + navigator.userAgent].join(String.fromCharCode(10));
            resultsDiv.innerHTML =
                '<div class="error">조회 중 오류가 발생했습니다</div>'
              + '<div class="error-detail"><h3>오류 상세</h3><pre id="errorText">' + esc(txt) + '</pre>'
              + '<div class="error-actions">'
              + '<button class="btn-copy" onclick="copyErrorToClipboard()">복사</button>'
              + '<button class="btn-restart" onclick="restartApp()">재시작</button></div></div>';
        }

        // 재시도 래퍼: 서버 일시 중단(앱 백그라운드) 대비
        // ── 서버(파이썬) 사망 시 공공API 직접 호출로 자동 전환 ──────────
        //   EX 엔진은 페이지 하단에 상시 내장된다. 서버가 살아있으면 사용되지 않는다.
        var EXFALL_ON = false;
        function exFallBanner() {
            if (EXFALL_ON) return;
            EXFALL_ON = true;
            try {
                var bar = document.getElementById('reconBar');
                if (bar) {
                    bar.textContent = ' 서버 연결 끊김 — 직접 조회 모드로 계속 동작합니다';
                    bar.style.background = '#8a6d1f';
                    bar.style.display = 'block';
                }
            } catch (e) {}
        }
        async function exFallback(url) {
            if (typeof EX === 'undefined' || !EX) throw new Error('폴백 엔진 없음');
            var q = {}, qs = url.split('?')[1] || '';
            qs.split('&').forEach(function (kv) {
                if (!kv) return;
                var i = kv.indexOf('=');
                q[decodeURIComponent(kv.slice(0, i))] = decodeURIComponent(kv.slice(i + 1));
            });
            var path = url.split('?')[0];
            exFallBanner();
            nsdbg('EX fallback ' + path);
            if (path.indexOf('/api/hospitals_all') === 0) return EX.fetchAllHospitals(false);
            if (path.indexOf('/api/hospitals') === 0)
                return EX.fetchRegionHospitals(q.sido || '', q.gugun || '');
            if (path.indexOf('/api/beds') === 0) return EX.fetchBeds(q.sido || '');
            if (path.indexOf('/api/bed_saturation') === 0)
                return EX.fetchBedSaturation(q.force === '1');
            if (path.indexOf('/api/hospital_detail') === 0)
                return EX.fetchDetail(q.hpid || '', q.sido || '', q.gugun || '');
            throw new Error('폴백 미지원 경로: ' + path);
        }

        async function fetchJSON(url, tries) {
            tries = tries || 3;
            var last = null;
            for (var i = 0; i < tries; i++) {
                try {
                    const r = await fetch(url, { cache: 'no-store' });
                    const t = await r.text();
                    if (!r.ok) throw new Error('HTTP ' + r.status + ': ' + t.slice(0, 200));
                    return JSON.parse(t);
                } catch (e) {
                    last = e;
                    nsdbg('fetch retry ' + (i + 1) + '/' + tries + ' ' + url.split('?')[0]
                          + ' — ' + ((e && e.message) || e));
                    if (i < tries - 1) await new Promise(function (r2) { setTimeout(r2, 1200 * (i + 1)); });
                }
            }
            try {
                return await exFallback(url);          // ← 서버 사망 시 직접 조회
            } catch (e2) {
                nsdbg('EX fallback 실패 ' + ((e2 && e2.message) || e2));
            }
            throw last;
        }

        // ══════════════════════════════════════════════════════════════
        //  등급(조합) 필터
        // ══════════════════════════════════════════════════════════════
        var LEVEL_SET = {};                       // 비어 있으면 = 모두
        function lvOk(h) {
            var k = Object.keys(LEVEL_SET);
            return k.length === 0 || !!LEVEL_SET[(h && h.level) || ''];
        }
        function lvLabel() {
            var k = Object.keys(LEVEL_SET);
            return k.length === 0 ? '모두' : k.join('+');
        }
        function lvPaint() {
            document.querySelectorAll('.lv-btn').forEach(function (b) {
                var v = b.getAttribute('data-lv');
                b.classList.toggle('active', v === '모두' ? Object.keys(LEVEL_SET).length === 0 : !!LEVEL_SET[v]);
            });
            document.querySelectorAll('#satLv .lv-btn').forEach(function (b) {
                var v = b.getAttribute('data-lv');
                b.classList.toggle('active', v === '모두' ? Object.keys(LEVEL_SET).length === 0 : !!LEVEL_SET[v]);
            });
        }
        function lvClick(v) {
            if (v === '모두') LEVEL_SET = {};
            else if (LEVEL_SET[v]) delete LEVEL_SET[v];
            else LEVEL_SET[v] = true;
            if (Object.keys(LEVEL_SET).length === 3) LEVEL_SET = {};   // 셋 다 = 모두
            lvPaint(); nsdbg('level=' + lvLabel());
            renderCurrent();
            if (document.getElementById('satOverlay')) satRender();
        }
        try {
            document.getElementById('lvGroup').addEventListener('click', function (e) {
                var b = e.target.closest ? e.target.closest('.lv-btn') : null;
                if (b) lvClick(b.getAttribute('data-lv'));
            });
        } catch (e) { nsdbg('lvGroup bind fail: ' + e); }

        // ══════════════════════════════════════════════════════════════
        //  검색 모드
        // ══════════════════════════════════════════════════════════════
        var SEARCH_MODE = 'region';
        var allHospitals = [];
        var _nameTimer = null, _allLoading = false;

        const regionPane = document.getElementById('regionPane');
        const namePane   = document.getElementById('namePane');
        const nameQuery  = document.getElementById('nameQuery');
        const nameInfo   = document.getElementById('nameQueryInfo');

        function setMode(m) {
            SEARCH_MODE = m;
            document.querySelectorAll('.mode-tab').forEach(function (el) {
                el.classList.toggle('active', el.getAttribute('data-mode') === m);
            });
            regionPane.style.display = (m === 'region') ? 'block' : 'none';
            namePane.style.display   = (m === 'name') ? 'block' : 'none';
            searchBtn.style.display  = (m === 'region') ? 'block' : 'none';
            hospitalsFullData = [];
            resultsDiv.innerHTML = '';
            resSetEnabled(m === 'region');
            try { document.getElementById('resInfo').textContent = ''; } catch (e) {}
            if (m === 'name') { nameInfo.textContent = ''; loadAllHospitals(); }
            else { nameInfo.textContent = ''; searchBtn.disabled = false; }
            nsdbg('mode=' + m);
        }
        document.getElementById('modeTabs').addEventListener('change', function (e) {
            if (e.target && e.target.name === 'smode') setMode(e.target.value);
        });

        sidoSelect.addEventListener('change', (e) => {
            const sido = e.target.value;
            gugunSelect.innerHTML = '<option value="">시/군/구 (선택 안 하면 시/도 전체)</option>';
            if (sido && districts[sido]) {
                districts[sido].forEach(g => {
                    const o = document.createElement('option');
                    o.value = g; o.textContent = g; gugunSelect.appendChild(o);
                });
                gugunSelect.disabled = false;
            } else { gugunSelect.disabled = true; }
            if (SEARCH_MODE !== 'name') searchBtn.disabled = false;   // 미선택 시 전국 조회
            prefetchRegion();
        });
        gugunSelect.addEventListener('change', () => {
            if (SEARCH_MODE === 'name') return;
            prefetchRegion();
        });
        searchBtn.addEventListener('click', searchHospitals);

        // ── 전국 로스터 (병원명 검색용) ──
        async function loadAllHospitals() {
            if (_allLoading || allHospitals.length) { applyNameQuery(); return; }
            try {
                const c = JSON.parse(sessionStorage.getItem('allHospitals_v1') || 'null');
                //  ROOT-FIX 2026-D2: 0건 시/도가 있던 불완전 로스터는 3분만 캐시.
                //   (서버가 자동복구해도 화면이 12시간 옛 목록을 쓰던 문제)
                const ttlMs = (c && c.miss && c.miss.length) ? 3 * 60 * 1000 : 12 * 3600 * 1000;
                if (c && c.h && c.h.length && (Date.now() - c.t < ttlMs)) {
                    allHospitals = c.h;
                    nsdbg('roster sessionStorage n=' + allHospitals.length
                          + (c.miss && c.miss.length ? ' (불완전 miss=' + c.miss.length + ')' : ''));
                    applyNameQuery(); return;
                }
            } catch (e) {}
            _allLoading = true;
            nameInfo.textContent = '전국 목록 불러오는 중...';
            resultsDiv.innerHTML = '<div class="loading"><div class="spinner"></div><p>전국 응급의료기관 목록 로딩...</p></div>';
            const t0 = Date.now();
            try {
                /*SRV-ALL-START*/
                const d = await fetchJSON('/api/hospitals_all');
                /*SRV-ALL-END*/
                if (!d.success) throw new Error(d.error || '목록 조회 실패');
                allHospitals = d.hospitals || [];
                try {
                    sessionStorage.setItem('allHospitals_v1', JSON.stringify({
                        t: Date.now(), h: allHospitals, miss: d.missing || [] }));
                } catch (e) {}
                nsdbg('roster n=' + allHospitals.length
                      + ((d.missing && d.missing.length) ? ' miss=' + d.missing.join(',') : '')
                      + ' ' + (Date.now() - t0) + 'ms');
                if (d.missing && d.missing.length) {
                    try {
                        nameInfo.textContent = '일부 시/도 목록 미수신(' + d.missing.join(',')
                            + ') — 자동 재조회됩니다';
                    } catch (e) {}
                }
                resultsDiv.innerHTML = '';
                applyNameQuery();
                try { nameQuery.focus(); } catch (e) {}
            } catch (err) {
                nsdbg('roster ERROR ' + ((err && err.message) || err));
                if (String((err && err.message) || err).match(/fetch|network|Failed/i)) {
                    resultsDiv.innerHTML = '<div class="error">앱 서버에 연결할 수 없습니다<br>'
                        + '<span style="font-size:0.85rem;">앱이 백그라운드에서 종료되었을 수 있습니다. '
                        + 'ER Monitor 앱을 다시 실행한 뒤 아래 버튼을 눌러 주세요.</span></div>'
                        + '<div style="text-align:center;margin-top:12px;">'
                        + '<button class="sat-refresh" onclick="location.reload()">다시 연결</button></div>';
                    startReconnect(function () { location.reload(); });
                } else {
                    showDetailedError(err, '전국 병원 목록 조회');
                }
                nameInfo.textContent = '';
            } finally { _allLoading = false; }
        }

        function applyNameQuery() {
            if (SEARCH_MODE !== 'name') return;
            if (!allHospitals.length) return;
            const raw = (nameQuery.value || '').trim();
            const q = _normTxt(raw);
            if (!q) {
                hospitalsFullData = []; resultsDiv.innerHTML = '';
                nameInfo.textContent = '전국 ' + allHospitals.length + '개 로드됨 — 검색어를 입력하세요';
                return;
            }
            const list = allHospitals.filter(function (h) { return lvOk(h) && _normTxt(h.name).includes(q); });
            hospitalsFullData = list;
            nameInfo.textContent = '"' + raw + '" → ' + list.length + '건'
                + (Object.keys(LEVEL_SET).length ? ' [' + lvLabel() + ']' : '')
                + ' / 전국 ' + allHospitals.length + '개';
            displayHospitals(list);
            nsdbg('query="' + raw + '" hit=' + list.length);
            ensureBeds(list.slice(0, 60).map(function (h) { return h.sido; }));
        }
        try {
            nameQuery.addEventListener('input', function () {
                clearTimeout(_nameTimer); _nameTimer = setTimeout(applyNameQuery, 140);
            });
        } catch (e) {}

        // ══════════════════════════════════════════════════════════════
        //  지역 조회 (시/도만으로도 동작)
        // ══════════════════════════════════════════════════════════════
        var _regionCache = {}, _regionInflight = {}, REGION_TTL_MS = 5 * 60 * 1000;

        function fetchRegion(sido, gugun) {
            const key = sido + '|' + (gugun || '');
            const c = _regionCache[key];
            if (c && (Date.now() - c.t < REGION_TTL_MS))
                return Promise.resolve({ success: true, hospitals: c.hospitals, hit: 'mem' });
            if (_regionInflight[key]) return _regionInflight[key];
            const p = (async function () {
                const t0 = Date.now();
                try {
                    /*SRV-REGION-START*/
                    const data = await fetchJSON('/api/hospitals?sido=' + encodeURIComponent(sido)
                                               + '&gugun=' + encodeURIComponent(gugun || ''));
                    /*SRV-REGION-END*/
                    if (data && data.success) {
                        _regionCache[key] = { t: Date.now(), hospitals: data.hospitals };
                        data.ms = Date.now() - t0;
                        nsdbg('region ' + key + ' n=' + data.hospitals.length + ' ' + data.ms + 'ms');
                    }
                    return data;
                } finally { delete _regionInflight[key]; }
            })();
            _regionInflight[key] = p;
            return p;
        }
        function prefetchRegion() {
            // 불필요 API 호출 금지 — 조회는 [병원 검색] 버튼에서만 수행한다.
            return;
        }

        async function searchHospitals() {
            if (isSearching) return;
            const sido = sidoSelect.value, gugun = gugunSelect.value;
            isSearching = true; searchBtn.disabled = true;
            const t0 = Date.now();
            resultsDiv.innerHTML = '<div class="loading"><div class="spinner"></div><p>'
                + (sido ? '병원 정보를 검색중입니다...' : '전국 응급의료기관을 불러오는 중...')
                + '</p></div>';
            // ── 시/도 미선택 → 전국 로스터 조회 ──
            if (!sido) {
                try {
                    if (!allHospitals.length) {
                        /*SRV-ALL2-START*/
                        const dAll = await fetchJSON('/api/hospitals_all');
                        /*SRV-ALL2-END*/
                        if (!dAll.success) throw new Error(dAll.error || '전국 목록 조회 실패');
                        allHospitals = dAll.hospitals || [];
                    }
                    hospitalsFullData = allHospitals.slice();
                    await resFilterAfterSearch(hospitalsFullData.filter(lvOk), t0);
                    nsdbg('nationwide n=' + hospitalsFullData.length);
                } catch (error) {
                    showDetailedError(error, '전국 병원 목록 조회');
                } finally { isSearching = false; searchBtn.disabled = false; }
                return;
            }
            try {
                const data = await fetchRegion(sido, gugun);
                if (data && data.success) {
                    hospitalsFullData = data.hospitals;
                    await resFilterAfterSearch(hospitalsFullData.filter(lvOk), t0);
                } else {
                    resultsDiv.innerHTML = '<div class="error">오류: '
                        + esc((data && data.error) || '알 수 없는 오류') + '</div>';
                }
            } catch (error) {
                nsdbg('region ERROR ' + sido + '|' + gugun + ' ' + ((error && error.message) || error));
                /*SRV-RECON2-START*/
                if (String((error && error.message) || error).match(/fetch|network|Failed/i))
                    startReconnect(() => { try { searchBtn.click(); } catch (e) {} });
                /*SRV-RECON2-END*/
                showDetailedError(error, '병원 검색 (' + sido + ' ' + (gugun || '전체') + ')');
            } finally { isSearching = false; searchBtn.disabled = false; }
        }

        // ══════════════════════════════════════════════════════════════
        //  병상 데이터 (시/도 단위 캐시) — 목록에 실시간 응급병상 표시
        // ══════════════════════════════════════════════════════════════
        var bedBySido = {}, bedInflight = {}, BED_TTL_MS = 90 * 1000;
        function bedOf(hpid) {
            for (var k in bedBySido) { var m = bedBySido[k].m; if (m && m[hpid]) return m[hpid]; }
            return null;
        }
        async function ensureBeds(sidos, maxN) {
            var uniq = [], seen = {};
            (sidos || []).forEach(function (x) {
                if (!x || seen[x]) return;
                seen[x] = 1;
                var c = bedBySido[x];
                if (c && Date.now() - c.t < BED_TTL_MS) return;
                if (bedInflight[x]) return;
                uniq.push(x);
            });
            if (!uniq.length) { paintBeds(); return; }
            uniq = uniq.slice(0, maxN || 6);
            await Promise.all(uniq.map(async function (sd) {
                bedInflight[sd] = 1;
                try {
                    /*SRV-BEDS-START*/
                    const d = await fetchJSON('/api/beds?sido=' + encodeURIComponent(sd), 2);
                    /*SRV-BEDS-END*/
                    if (d && d.success) bedBySido[sd] = { t: Date.now(), m: d.beds || {} };
                } catch (e) { nsdbg('beds ' + sd + ' FAIL ' + ((e && e.message) || e)); }
                finally { delete bedInflight[sd]; }
            }));
            paintBeds();
        }
        function paintBeds() {
            document.querySelectorAll('.hospital-item').forEach(function (el) {
                var hp = el.getAttribute('data-hpid');
                var slot = el.querySelector('.bedslot');
                if (!slot) return;
                var b = bedOf(hp);
                slot.innerHTML = bedHtml(b);
            });
        }
        function bedHtml(b) {
            if (!b) return '<span class="bedchip">병상 —</span>';
            var p = (b.er && b.er.r !== null && b.er.r !== undefined) ? Math.round(b.er.r * 100) : null;
            return '<span class="bedchip" style="background:' + (p === null ? '#eef0f4' : satColor(p))
                 + ';color:' + (p === null ? '#445' : '#fff') + ';">응급 ' + bedTxt(b.er)
                 + (p === null ? '' : ' · ' + p + '%') + '</span>'
                 + '<span class="bedchip">병동 ' + bedTxt(b.ward) + '</span>'
                 + '<span class="bedchip">중환 ' + bedTxt(b.icu) + '</span>';
        }

        // ══════════════════════════════════════════════════════════════
        //  목록 렌더
        // ══════════════════════════════════════════════════════════════
        function renderCurrent() {
            if (SEARCH_MODE === 'name') { applyNameQuery(); return; }
            if (!hospitalsFullData || !hospitalsFullData.length) return;
            displayHospitals(hospitalsFullData.filter(lvOk));
        }
        function regionLine(h) {
            if (!h || !h.sido) return '';
            return '<span class="region-tag">' + esc(h.sido) + ' ' + esc(h.gugun || '-') + '</span><br>';
        }
        var _shown = [];
        // ══════════════════════════════════════════════════════════════
        //  자원검색 — CRRT / ECMO / TTM / HBO (AND 조합)
        //   TTM = 중심체온조절유도기(hvhypoayn), HBO = 고압산소치료기(hvoxyayn)
        //   지역/등급 선택은 메인화면 옵션을 그대로 따른다.
        //   병원명 검색 모드에서는 사용하지 않는다(버튼 비활성).
        // ══════════════════════════════════════════════════════════════
        var RES_SEL   = {};
        var RES_ORDER = ['crrt', 'ecmo', 'ttm', 'hbo'];
        var RES_LABEL = { crrt: 'CRRT', ecmo: 'ECMO', ttm: 'TTM', hbo: 'HBO' };
        var RES_BUSY  = false;

        function resActive() { return Object.keys(RES_SEL).length >0; }
        function resKeys() { return RES_ORDER.filter(function (k) { return RES_SEL[k]; }); }

        function resEqOf(h) {
            var c = bedBySido[h && h.sido];
            if (!c || !c.m) return null;
            var b = c.m[h.hpid];
            return (b && b.eq) ? b.eq : null;
        }
        function resOk(h) {
            var keys = resKeys();
            if (!keys.length) return true;
            var eq = resEqOf(h);
            if (!eq) return false;
            for (var i = 0; i < keys.length; i++) { if (!eq[keys[i]]) return false; }
            return true;
        }

        // 버튼 폭 고정 — 글자가 길어지면 폰트만 줄인다
        function fitBtnText(el, startRem) {
            if (!el) return;
            el.style.fontSize = '';          // ← CSS 기본값으로 원복 후 재측정
            if (el.scrollWidth <= el.clientWidth + 1) return;
            var f = startRem || 1.0;
            el.style.fontSize = f + 'rem';
            var guard = 0;
            while (el.scrollWidth >el.clientWidth + 1 && f >0.55 && guard++ < 24) {
                f = Math.max(0.55, f - 0.04);
                el.style.fontSize = f.toFixed(2) + 'rem';
                if (f <= 0.55) break;
            }
        }
        function resPaintBtn() {
            var b = document.getElementById('resBtn');
            if (!b) return;
            var on = resActive();
            b.textContent = on ? ('자원 (' + resKeys().length + ')') : '자원 조건';
            b.title = on ? resKeys().map(function (k) { return RES_LABEL[k]; }).join(' + ')
                           + ' (모두 보유)' : '자원 조건 미설정';
            b.style.background = on ? 'linear-gradient(135deg,#16a34a,#065f46)'
                                    : 'linear-gradient(135deg,#2f6f5f,#1d4c41)';
            // 폰트는 CSS 고정값 사용 (동적 축소·복귀 문제 원천 제거)
        }
        function resSetEnabled(on) {
            var b = document.getElementById('resBtn');
            if (!b) return;
            b.disabled = !on;
            b.style.opacity = on ? '1' : '0.45';
            b.style.cursor = on ? 'pointer' : 'not-allowed';
            if (!on) { RES_SEL = {}; }
            resPaintBtn();
        }

        // 등급 + 자원 조건을 모두 반영한 단일 렌더 경로
        // 검색 조건 전체 초기화 (선택된 병원은 유지)
        function resetSearchConditions() {
            try {
                RES_SEL = {};
                LEVEL_SET = {};
                if (typeof lvPaint === 'function') lvPaint();
                sidoSelect.value = '';
                gugunSelect.innerHTML = '<option value="">시/군/구 (선택 안 하면 시/도 전체)</option>';
                gugunSelect.disabled = true;
                hospitalsFullData = [];
                _shown = [];
                try { nameQuery.value = ''; nameInfo.textContent = ''; } catch (e) {}
                try { document.getElementById('resInfo').textContent = ''; } catch (e) {}
                resultsDiv.innerHTML = '';
                resPaintBtn();
                renderSelected();
            } catch (e) { nsdbg('reset err ' + e); }
        }
        try {
            document.getElementById('resetBtn').onclick = resetSearchConditions;
        } catch (e) {}

        function renderList() {
            if (SEARCH_MODE === 'name') { applyNameQuery(); return; }
            displayHospitals((hospitalsFullData || []).filter(lvOk));
        }

        function resInfoEl() {
            var el = document.getElementById('resInfo');
            if (!el) {
                el = document.createElement('div');
                el.id = 'resInfo';
                el.style.cssText = 'margin:6px 0 0;font-size:0.85rem;color:#555;';
                var host = document.getElementById('results');
                if (host && host.parentNode) host.parentNode.insertBefore(el, host);
            }
            return el;
        }

        // 적용 = 조건 저장만. API 호출·목록 갱신은 [병원 검색] 시 1회만 수행한다.
        function resApply() {
            var info = resInfoEl();
            resPaintBtn();
            info.textContent = resActive()
                ? ('자원 조건: ' + resKeys().map(function (k) { return RES_LABEL[k]; }).join(' + ')
                   + ' (모두 보유) — [병원 검색] 을 누르면 적용됩니다')
                : '';
        }

        // 검색 결과에 자원 조건을 반영 (검색 버튼 경로에서만 호출)
        async function resFilterAfterSearch(list, t0) {
            var info = resInfoEl();
            var sidos = [];
            list.forEach(function (h) {
                if (h.sido && sidos.indexOf(h.sido) < 0) sidos.push(h.sido);
            });
            if (!resActive()) {
                info.textContent = '';
                displayHospitals(list, t0 ? Date.now() - t0 : undefined);
                // 병상 표시는 자원조건과 무관하게 필요 → 비동기로 채운다
                try { ensureBeds(sidos); } catch (e) {}
                return;
            }
            info.textContent = '장비 정보 조회 중... (' + sidos.length + '개 시/도)';
            try { await ensureBeds(sidos, 20); } catch (e) {}
            var unknown = list.filter(function (h) { return !resEqOf(h); }).length;
            var hit = list.filter(resOk);
            info.textContent = '자원 ' + resKeys().map(function (k) { return RES_LABEL[k]; }).join(' + ')
                + ' (모두 보유) -> ' + hit.length + '곳 / 대상 ' + list.length + '곳'
                + (unknown ? ' · 장비정보 없음 ' + unknown + '곳 제외' : '');
            displayHospitals(list, t0 ? Date.now() - t0 : undefined);
        }

        function resPopup() {
            if (SEARCH_MODE === 'name') return;
            var old = document.getElementById('resPop');
            if (old) { old.remove(); return; }
            var ov = document.createElement('div');
            ov.id = 'resPop';
            ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.55);'
                + 'z-index:99999;display:flex;align-items:center;justify-content:center;';
            var box = document.createElement('div');
            box.style.cssText = 'background:#fff;padding:16px;max-width:340px;width:88%;'
                + 'border-radius:10px;box-shadow:0 8px 30px rgba(0,0,0,0.35);';
            box.innerHTML = '<div style="font-weight:800;margin-bottom:4px;">자원 검색</div>'
                + '<div style="font-size:0.82rem;color:#666;margin-bottom:10px;">'
                + '선택한 자원을 <b>모두</b>보유한 병원만 표시 (AND)</div>'
                + '<div id="resBtns" style="display:flex;flex-wrap:wrap;gap:6px;"></div>'
                + '<div style="display:flex;gap:6px;margin-top:14px;">'
                + '<button id="resAll" style="flex:1;padding:9px;border:1px solid #ccc;'
                + 'background:#f3f4f6;font-weight:700;cursor:pointer;">모두</button>'
                + '<button id="resClear" style="flex:1;padding:9px;border:1px solid #ccc;'
                + 'background:#f3f4f6;font-weight:700;cursor:pointer;">해제</button>'
                + '<button id="resDone" style="flex:1.4;padding:9px;border:none;'
                + 'background:#16a34a;color:#fff;font-weight:800;cursor:pointer;">적용</button>'
                + '</div>';
            ov.appendChild(box);
            document.body.appendChild(ov);
            function paint() {
                var wrap = box.querySelector('#resBtns');
                wrap.innerHTML = '';
                RES_ORDER.forEach(function (k) {
                    var b = document.createElement('button');
                    b.textContent = RES_LABEL[k];
                    b.style.cssText = 'flex:1 1 44%;padding:12px 4px;font-weight:800;'
                        + 'border:2px solid ' + (RES_SEL[k] ? '#16a34a' : '#ddd') + ';'
                        + 'background:' + (RES_SEL[k] ? '#16a34a' : '#fff') + ';'
                        + 'color:' + (RES_SEL[k] ? '#fff' : '#333') + ';cursor:pointer;';
                    b.onclick = function () {
                        if (RES_SEL[k]) { delete RES_SEL[k]; } else { RES_SEL[k] = 1; }
                        paint();
                    };
                    wrap.appendChild(b);
                });
            }
            paint();
            box.querySelector('#resAll').onclick = function () {
                RES_ORDER.forEach(function (k) { RES_SEL[k] = 1; }); paint();
            };
            box.querySelector('#resClear').onclick = function () { RES_SEL = {}; paint(); };
            box.querySelector('#resDone').onclick = function () { ov.remove(); resApply(); };
            ov.onclick = function (e) { if (e.target === ov) ov.remove(); };
        }
        try {
            document.getElementById('resBtn').onclick = resPopup;
            resPaintBtn();
        } catch (e) {}

        function displayHospitals(hospitals, ms) {
            // 자원검색 필터 (AND) — 모든 표시 경로에 일괄 적용
            if (resActive()) {
                hospitals = (hospitals || []).filter(resOk);
            }
            _shown = hospitals || [];
            if (!_shown.length) {
                resultsDiv.innerHTML = '<div class="loading">조건에 맞는 병원이 없습니다.'
                    + (resActive() ? '<br><span style="font-size:0.85em;">자원 조건: '
                        + RES_ORDER.filter(function (k) { return RES_SEL[k]; })
                            .map(function (k) { return RES_LABEL[k]; }).join(' + ')
                        + ' (모두 보유)</span>' : '')
                    + '</div>';
                return;
            }
            const room = MAX_SEL - selectedHospitals.length;
            const notSel = _shown.filter(function (h) {
                return !selectedHospitals.some(function (x) { return x.hpid === h.hpid; });
            });
            const _t = (typeof ms === 'number')
                ? ' <span style="font-weight:400;color:#999;font-size:0.85em;">(' + (ms / 1000).toFixed(1) + '초)</span>' : '';
            let html = '<div class="res-head"><span>총 ' + _shown.length + '개 병원' + _t + '</span>'
                + ((notSel.length >0 && notSel.length <= room)
                    ? '<button type="button" class="pickall-btn" id="pickAllBtn">모두 선택 (' + notSel.length + ')</button>' : '')
                + '</div>';
            _shown.forEach(function (h) {
                const isSel = selectedHospitals.some(function (x) { return x.hpid === h.hpid; });
                const badge = levelBadge[h.level] || levelBadge['기관'];
                html += '<div class="hospital-item' + (isSel ? ' selected' : '') + '" data-hpid="' + esc(h.hpid) + '">'
                     +   '<input type="checkbox" class="hospital-checkbox"' + (isSel ? ' checked' : '') + '>'
                     +   '<div class="hospital-content">'
                     +     '<div class="hospital-name">' + badge + esc(h.name) + '</div>'
                     +     '<div class="hospital-info">' + regionLine(h) + '</div>'
                     +     '<div class="bedline"><span class="bedslot">' + bedHtml(bedOf(h.hpid)) + '</span>'
                     +       '<button type="button" class="detail-btn" data-dt="' + esc(h.hpid) + '">자세히</button></div>'
                     +   '</div></div>';
            });
            resultsDiv.innerHTML = html;
        }

        resultsDiv.addEventListener('click', function (e) {
            var t = e.target;
            if (t && t.id === 'pickAllBtn') { pickAllShown(); return; }
            var db = t.closest ? t.closest('.detail-btn') : null;
            if (db) { openDetail(db.getAttribute('data-dt')); return; }
            var item = t.closest ? t.closest('.hospital-item') : null;
            if (!item) return;
            var hp = item.getAttribute('data-hpid');
            var h = (_shown.filter(function (x) { return x.hpid === hp; })[0])
                 || (hospitalsFullData.filter(function (x) { return x.hpid === hp; })[0]);
            if (!h) return;
            var on = selectedHospitals.some(function (x) { return x.hpid === hp; });
            if (t.tagName !== 'INPUT') {
                var cb = item.querySelector('.hospital-checkbox');
                if (cb) cb.checked = !on;
            }
            if (!on) {
                if (selectedHospitals.length >= MAX_SEL) {
                    alert('최대 ' + MAX_SEL + '개 병원까지만 선택할 수 있습니다.');
                    var cb2 = item.querySelector('.hospital-checkbox'); if (cb2) cb2.checked = false;
                    return;
                }
                selectedHospitals.push(selRec(h));
            } else {
                selectedHospitals = selectedHospitals.filter(function (x) { return x.hpid !== hp; });
            }
            afterSelChange();
        });

        function selRec(h) {
            return { hpid: h.hpid, name: h.name, level: h.level,
                     dutyAddr: h.dutyAddr || '', dutyTel1: h.dutyTel1 || '', dutyTel3: h.dutyTel3 || '',
                     sido: h.sido || sidoSelect.value, gugun: h.gugun || gugunSelect.value };
        }
        function pickAllShown() {
            var added = 0;
            _shown.forEach(function (h) {
                if (selectedHospitals.length >= MAX_SEL) return;
                if (selectedHospitals.some(function (x) { return x.hpid === h.hpid; })) return;
                selectedHospitals.push(selRec(h)); added++;
            });
            nsdbg('pickAll +' + added);
            afterSelChange();
        }
        function afterSelChange() {
            updateSelectedBox(); saveToLocalStorage();
            if (SEARCH_MODE === 'name') { /* 목록 유지 */ }
            displayHospitals(_shown);
            if (document.getElementById('satOverlay')) satSyncChecks();
        }

        function updateSelectedBox() {
            var t = document.getElementById('selTitle');
            if (t) t.textContent = '선택된 병원 (' + selectedHospitals.length + '/' + MAX_SEL + ')';
            var cb = document.getElementById('clearSelBtn');
            if (selectedHospitals.length === 0) {
                selectedBox.classList.remove('show');
                if (cb) cb.disabled = true;
                compareBtn.disabled = true;
                return;
            }
            selectedBox.classList.add('show');
            if (cb) cb.disabled = false;
            selectedList.innerHTML = selectedHospitals.map(function (h) {
                const badge = levelBadge[h.level] || levelBadge['기관'];
                return '<div class="selected-item">'
                     + '<input type="checkbox" checked onchange=\"removeHospital(&#39;' + esc(h.hpid) + '&#39;)\">'
                     + '<span>' + badge + esc(h.name) + '</span></div>';
            }).join('');
            compareBtn.disabled = selectedHospitals.length < 1;
        }
        function removeHospital(hpid) {
            selectedHospitals = selectedHospitals.filter(function (h) { return h.hpid !== hpid; });
            afterSelChange();
        }
        function updateCheckboxStates() { displayHospitals(_shown); }

        function clearSelection() {
            if (!selectedHospitals.length) return;
            if (!confirm('선택된 병원 ' + selectedHospitals.length + '개를 모두 해제할까요?')) return;
            selectedHospitals = [];
            nsdbg('selection cleared');
            afterSelChange();
        }
        try { document.getElementById('clearSelBtn').onclick = clearSelection; } catch (e) {}

        compareBtn.addEventListener('click', () => {
            if (selectedHospitals.length < 1) { alert('병원을 1개 이상 선택해주세요.'); return; }
            const cs = sidoSelect.value || '', cg = gugunSelect.value || '';
            const hParam = selectedHospitals.map(h => {
                const s2 = h.sido || cs, g2 = h.gugun || cg;
                if (!s2 || !g2) return null;
                return `${h.hpid}|${s2}|${g2}`;
            }).filter(Boolean).join(',');
            if (!hParam) { alert('지역 정보가 없습니다. 병원을 다시 검색하여 선택해주세요.'); return; }
            /*SRV-COMPARE-START*/
            window.open('/compare?h=' + encodeURIComponent(hParam), '_blank');
            /*SRV-COMPARE-END*/
        });

        // ══════════════════════════════════════════════════════════════
        //  자세히 팝업
        // ══════════════════════════════════════════════════════════════
        function dtClose() { var o = document.getElementById('dtOverlay'); if (o) o.remove(); }
        function kv(k, v) { return '<div class="dt-kv"><span>' + k + '</span><span>' + esc(v || '-') + '</span></div>'; }
        // 실시간 메시지 행 — 좌측열=분류(색상), 본문=같은 색, [진료과목]만 검정
        function kvMsg(m) {
            /* [2026-H2] 과목=분류색 / 세부내용=검정 (기존과 반전) */
            var col = m.color || '#333';
            var raw = String(m.msg || '-');
            var mm = /^\\[([^\\]]*)\\]\\s*([\\s\\S]*)$/.exec(raw);
            var body = mm
                ? ('<span style="color:' + col + ';font-weight:700;">[' + esc(mm[1])
                   + ']</span> <span style="color:#000;">' + esc(mm[2]) + '</span>')
                : ('<span style="color:#000;">' + esc(raw) + '</span>');
            return '<div class="dt-kv"><span style="color:' + col + ';font-weight:700;">'
                 + esc(m.label || '-') + '</span><span>' + body + '</span></div>';
        }
        function cellRows(obj, labels) {
            var out = '';
            Object.keys(labels).forEach(function (k) {
                var b = obj && obj[k];
                if (!b || b.total === undefined) return;
                if (b.total <= 0 && b.avail <= 0) return;
                out += '<div class="dt-cell"><span>' + labels[k] + '</span><span>'
                     + b.avail + ' / ' + b.total + '</span></div>';
            });
            return out || '<div class="dt-cell"><span>제공 항목 없음</span><span>-</span></div>';
        }
        async function openDetail(hpid) {
            var h = (_shown.filter(function (x) { return x.hpid === hpid; })[0])
                 || (hospitalsFullData.filter(function (x) { return x.hpid === hpid; })[0])
                 || (allHospitals.filter(function (x) { return x.hpid === hpid; })[0])
                 || (_satRows.filter(function (x) { return x.hpid === hpid; })[0]);
            if (!h) return;
            dtClose();
            var ov = document.createElement('div');
            ov.id = 'dtOverlay';
            ov.innerHTML = '<div class="dt-card"><div class="dt-head">'
                + '<h3>' + (levelBadge[h.level] || '') + esc(h.name) + '</h3>'
                + '<button class="sat-close" id="dtCloseBtn">닫기</button></div>'
                + '<div class="dt-body" id="dtBody">'
                + '<div class="loading"><div class="spinner"></div><p>상세 정보를 불러오는 중...</p></div>'
                + '</div></div>';
            document.body.appendChild(ov);
            ov.addEventListener('click', function (e) {
                if (e.target === ov || (e.target && e.target.id === 'dtCloseBtn')) dtClose();
            });
            var body = document.getElementById('dtBody');
            var base = '<div class="dt-sec"><h4>기관 정보</h4>'
                + kv('기관구분', (h.emclsName || h.level || '-'))
                + kv('주소', h.dutyAddr) + kv('대표전화', h.dutyTel1) + kv('응급실', h.dutyTel3)
                + kv('지역', (h.sido || '') + ' ' + (h.gugun || '')) + kv('HPID', h.hpid) + '</div>';
            try {
                /*SRV-DETAIL-START*/
                const d = await fetchJSON('/api/hospital_detail?hpid=' + encodeURIComponent(hpid)
                        + '&sido=' + encodeURIComponent(h.sido || '')
                        + '&gugun=' + encodeURIComponent(h.gugun || ''), 2);
                /*SRV-DETAIL-END*/
                if (!d.success) throw new Error(d.error || '상세 조회 실패');
                var x = d.detail || {};
                var msgs = Array.isArray(d.messages) ? d.messages : [];
                body.innerHTML = base
                    + '<div class="dt-sec"><h4>응급실</h4><div class="dt-grid">'
                    + cellRows(x.emergency, { hvec: '응급실 일반', hv28: '소아', hv29: '음압격리', hv30: '일반격리' })
                    + '</div></div>'
                    + '<div class="dt-sec"><h4>입원실 (일반병실)</h4><div class="dt-grid">'
                    + cellRows(x.general, { hvgc: '입원실', hv36: '외과', hv37: '신경외과', hv41: '내과' })
                    + '</div></div>'
                    + '<div class="dt-sec"><h4>중환자실</h4><div class="dt-grid">'
                    + cellRows(x.icu, { hvicc: '일반', hv2: '내과', hv3: '외과', hvncc: '신생아', hv32: '소아',
                                        hvcc: '신경', hv6: '흉부', hv34: '화상', hvccc: '심장', hv35: '외상',
                                        hv31: '신경외과', hv33: '정형외과' })
                    + '</div></div>'
                    + '<div class="dt-sec"><h4>격리 진료구역</h4><div class="dt-grid">'
                    + cellRows(x.isolation, { hv13: '음압격리', hv14: '일반격리', hv15: '소아음압', hv16: '소아일반',
                                              hv22: '코호트', hv23: '응급전용음압', hv24: '응급전용일반',
                                              hv25: '소아전용음압', hv26: '소아전용일반', hv27: '기타' })
                    + '</div></div>'
                    + '<div class="dt-sec"><h4>기타</h4><div class="dt-grid">'
                    + cellRows(x.other, { hvoc: '수술실' })
                    + '</div></div>'
                    + '<div class="dt-sec"><h4>장비</h4><div class="dt-grid">'
                    + Object.keys(x.equipment || {}).map(function (k) {
                        var lab = { ct: 'CT', mri: 'MRI', angio: '조영촬영', ventilator: '인공호흡기',
                                    ventilator_preemie: '조산아호흡기', incubator: '인큐베이터', crrt: 'CRRT',
                                    ecmo: 'ECMO', hypothermia: '저체온', hyperbaric: '고압산소' }[k] || k;
                        return '<div class="dt-cell"><span>' + lab + '</span><span>'
                             + (x.equipment[k].available ? '가능' : '불가') + '</span></div>';
                      }).join('') + '</div></div>'
                    + (msgs.length
                        ? '<div class="dt-sec"><h4>실시간 메시지 (' + msgs.length + ')</h4>'
                          + msgs.map(function (m) { return kvMsg(m); }).join('') + '</div>'
                        : '<div class="dt-sec"><h4>실시간 메시지</h4>'
                          + '<div class="dt-kv"><span>-</span><span>등록된 메시지 없음</span></div></div>')
                    + '<div class="dt-sec"><h4>갱신</h4>' + kv('병상 갱신시각', x.update_time) + '</div>';
            } catch (err) {
                body.innerHTML = base + '<div class="error">상세 조회 실패<br><span style="font-size:0.85rem;">'
                    + esc((err && err.message) || err) + '</span></div>';
            }
        }

        // ══════════════════════════════════════════════════════════════
        //  병상 포화도
        // ══════════════════════════════════════════════════════════════
        var _satRows = [], _satAt = '', _satFails = [], _satBusy = false, _satBuild = '';
        var _satFormulaOpen = false, SAT_METRIC = 'er', SORT_DESC = true;
        var satSido = '', satGugun = '';
        var METRIC_LABEL = { er: '응급실', ward: '일반병실', icu: '중환자실', load: '상대 과밀점수' };
        function fmtVal(v) {
            if (v === null || v === undefined) return '—';
            var n = Math.round(v * 100);
            return (SAT_METRIC === 'load') ? String(n) : (n + '%');
        }

        function mVal(r) {
            if (SAT_METRIC === 'load') return (r.load === null || r.load === undefined) ? null : r.load;
            var b = r[SAT_METRIC];
            return (b && b.r !== null && b.r !== undefined) ? b.r : null;
        }
        function satScope(r) {
            if (satSido && r.sido !== satSido) return false;
            if (satGugun && r.gugun !== satGugun
                && String(r.dutyAddr || '').indexOf(satGugun) === -1) return false;
            return true;
        }
        function satSyncChecks() {
            document.querySelectorAll('.sat-item').forEach(function (el) {
                var hp = el.getAttribute('data-hpid');
                var on = selectedHospitals.some(function (h) { return h.hpid === hp; });
                el.classList.toggle('selected', on);
                var cb = el.querySelector('input');
                if (cb) cb.checked = on;
            });
        }
        function satFormulaHtml() {
            return '<div style="background:#fdecea;border:1px solid #e0a0a0;border-radius:8px;'
                 + 'padding:8px 10px;margin-bottom:9px;color:#7a1f1f;">'
                 + '<b>이 점수는 검증된 의료 과밀지표가 아닙니다.</b><br>'
                 + 'NEDOCS·EDWIN 은 ED 환자수·중증도·입원대기 환자수·최장 보딩시간·의사 인력 등을 '
                 + '사용해 실제 ED 자료로 개발·검증된 지표입니다. 본 점수는 그 변수들을 전혀 '
                 + '사용하지 않으며, 공공 API 로 얻을 수 있는 <b>병상 가동률만</b>으로 계산한 '
                 + '<b>자체 설계 heuristic</b>입니다. 두 계열을 같은 수준으로 해석하지 마십시오.'
                 + '</div>'
                 + '<b>상대 과밀점수 (자체 설계 · 미검증)</b><br>'
                 + '응급실 병상이 비어 보여도 입원 대기(boarding)가 회전을 막으면 실제 부담은 커집니다. '
                 + '하류 병상 적체를 반영해 응급실 포화도를 보정합니다.<br><br>'
                 + '<b>[실측값]</b> <code>E</code>응급실 포화도 · <code>W</code>일반병실 · '
                 + '<code>I</code>중환자실 (각 1 − 가용/기준)<br><br>'
                 + '<b>[설계 상수]</b> — 아래 값은 모두 <u>본 모델이 임의로 정한 값</u>이며, '
                 + '실제 병원 데이터로 calibration·validation 되지 않았습니다.<br>'
                 + '<code>w_W = 0.74, w_I = 0.26</code><br>'
                 + '&nbsp;&nbsp;산정근거: 일반병동 입원 85%×보딩 6h = 5.1 vs 중환자실 15%×12h = 1.8 → '
                 + '5.1:1.8. 이 입원비율·보딩시간 자체가 가정값이며 병원마다 크게 다릅니다.<br>'
                 + '<code>θ = 2 (Dc = D^θ)</code><br>'
                 + '&nbsp;&nbsp;점유율이 높아질수록 병상 부족 위험이 비선형적으로 커질 수 있다는 '
                 + '기존 시뮬레이션 연구(Bagust 등, 약 85% 전후)의 <i>방향성</i>만 참고해 '
                 + '<u>본 모델에서 임의로 적용한 볼록 변환</u>입니다. '
                 + '해당 연구가 D² 함수를 제시한 것은 아니며, 85% 라는 기준도 병원 규모·'
                 + '병동 종류·case-mix 에 따라 달라집니다.<br>'
                 + '<code>a = 권역 0.35 · 센터 0.28 · 기관 0.20</code><br>'
                 + '&nbsp;&nbsp;응급실→입원 전환율 가정치. 실측 근거 없이 종별 규모차만 반영한 설계값.<br><br>'
                 + '<b>[산식]</b> <code>점수 = E ÷ (1 − a·Dc) × 100</code>, '
                 + '<code>D = w_W·W + w_I·I</code><br>'
                 + '하류가 비면(D=0) 점수 = 응급실 포화도와 동일합니다. '
                 + '완전 적체 시 상한은 권역 +54% · 센터 +39% · 기관 +25%.<br><br>'
                 + '<span style="color:#a06000;">※ 값은 <b>단위 없는 상대 점수</b>입니다. '
                 + '“178점”은 <b>“과밀 178%”나 “업무량 1.78배”가 아니라</b>같은 시각 다른 병원보다 '
                 + '높다는 순위 정보로만 사용하십시오. 절대 해석·의사결정 근거로 쓰려면 실제 ED '
                 + '자료를 이용한 calibration 이 선행되어야 합니다.</span>';
        }
        function satClose() { var o = document.getElementById('satOverlay'); if (o) o.remove(); }

        function satRender() {
            var body = document.getElementById('satBody'), sub = document.getElementById('satSub');
            if (!body) return;
            var list = _satRows.filter(function (r) { return lvOk(r) && satScope(r); }).slice();
            list.sort(function (x, y) {
                var vx = mVal(x), vy = mVal(y);
                var nx = (vx === null) ? 1 : 0, ny = (vy === null) ? 1 : 0;
                if (nx !== ny) return nx - ny;
                if (vx !== vy) return SORT_DESC ? vy - vx : vx - vy;
                return x.name < y.name ? -1 : 1;
            });
            var scope = (satSido ? satSido + (satGugun ? ' ' + satGugun : '') : '전국');
            if (sub) sub.textContent = lvLabel() + ' · ' + scope + ' · ' + METRIC_LABEL[SAT_METRIC]
                + ' · ' + list.length + '개 · ' + _satAt
                + (_satFails.length ? ' · 실패 ' + _satFails.length : '')
                + (_satBuild ? ' · b' + _satBuild : '');
            var html = '<div class="sat-formula' + (_satFormulaOpen ? ' show' : '') + '" id="satFormula">'
                     + satFormulaHtml() + '</div>';
            if (!list.length) { body.innerHTML = html + '<div class="loading">조건에 맞는 병원이 없습니다.</div>'; return; }
            // 막대 스케일: 현재 목록 최대값 기준 동적 (상단 고포화 구간도 길이 구분)
            var mx = 0;
            list.forEach(function (r) { var v = mVal(r); if (v !== null) mx = Math.max(mx, v * 100); });
            var SCALE = Math.max(110, Math.ceil((mx + 8) / 10) * 10);
            html += '<div class="cap-scale">막대 기준 0 ~ ' + SCALE + '% |  세로선 = 100%</div>';
            list.forEach(function (r, i) {
                var v = mVal(r), p = (v === null) ? null : Math.round(v * 100);
                var c = satColor(p);
                var on = selectedHospitals.some(function (h) { return h.hpid === r.hpid; });
                var badge = levelBadge[r.level] || levelBadge['기관'];
                var detail = (SAT_METRIC === 'load')
                    ? ('응급 ' + pctTxt(r.er.r) + ' · 병동 ' + pctTxt(r.ward.r)
                       + ' · 중환 ' + pctTxt(r.icu.r) + ' · a=' + r.adm)
                    : (METRIC_LABEL[SAT_METRIC] + ' ' + bedTxt(r[SAT_METRIC]));
                html += '<div class="sat-item' + (on ? ' selected' : '') + '" data-hpid="' + esc(r.hpid) + '">'
                     +   '<input type="checkbox"' + (on ? ' checked' : '') + '>'
                     +   '<div class="sat-rank">' + (i + 1) + '</div>'
                     +   '<div class="sat-main">'
                     +     '<div class="sat-name">' + badge + esc(r.name)
                     +       ' <span class="sat-pct" style="color:' + c + ';">' + fmtVal(v)
                     +       (SAT_METRIC === 'load' ? '<span style="font-size:0.7em;color:#999;">점</span>' : '')
                     +       '</span></div>'
                     +     '<div class="sat-meta">' + esc(r.sido) + ' ' + esc(r.gugun || '-') + ' · ' + detail
                     +       ' <button type="button" class="detail-btn" data-dt="' + esc(r.hpid)
                     +       '" style="margin-left:4px;">자세히</button></div>'
                     +     capBar(p, SCALE)
                     +   '</div></div>';
            });
            body.innerHTML = html;
        }

        async function satLoad(force) {
            if (_satBusy) return;
            _satBusy = true;
            var body = document.getElementById('satBody'), sub = document.getElementById('satSub');
            var rb = document.getElementById('satRefreshBtn');
            if (rb) rb.disabled = true;
            if (sub) sub.textContent = force ? '갱신 중...' : '조회 중...';
            if (body) body.innerHTML = '<div class="loading"><div class="spinner"></div>'
                + '<p>전국 실시간 병상 정보를 조회하는 중...<br>'
                + '<span style="font-size:0.8rem;color:#999;">시/도 17개 조회 — 수십 초가 걸릴 수 있습니다</span></p></div>';
            var t0 = Date.now();
            try {
                /*SRV-SAT-START*/
                const sd = await fetchJSON('/api/bed_saturation' + (force ? '?force=1' : ''), 3);
                /*SRV-SAT-END*/
                if (!sd.success) throw new Error(sd.error || '포화도 조회 실패');
                _satRows = sd.rows || []; _satAt = sd.queried_at || ''; _satFails = sd.failed || [];
                _satBuild = sd.build || '';
                nsdbg('sat n=' + _satRows.length + ' force=' + !!force + ' ' + (Date.now() - t0) + 'ms');
                satRender();
            } catch (err) {
                nsdbg('sat ERROR ' + ((err && err.message) || err));
                if (body) body.innerHTML = '<div class="error">병상 포화도 조회 실패<br>'
                    + '<span style="font-size:0.85rem;">' + esc((err && err.message) || err) + '</span></div>'
                    + '<div style="text-align:center;margin-top:12px;">'
                    + '<button class="sat-refresh" id="satRetryBtn">다시 시도</button></div>';
                if (sub) sub.textContent = '조회 실패 — 다시 시도해 주세요';
                /*SRV-RECON-START*/
                if (String((err && err.message) || err).match(/fetch|network|Failed/i))
                    startReconnect(function () { satLoad(true); });
                /*SRV-RECON-END*/
            } finally { _satBusy = false; if (rb) rb.disabled = false; }
        }

        function openSaturation() {
            if (document.getElementById('satOverlay')) return;
            satSido = sidoSelect.value || ''; satGugun = gugunSelect.value || '';
            var ov = document.createElement('div');
            ov.id = 'satOverlay';
            var sidoOpts = '<option value="">전국</option>'
                + Object.keys(districts).map(function (k) {
                    return '<option value="' + k + '"' + (k === satSido ? ' selected' : '') + '>' + k + '</option>';
                  }).join('');
            ov.innerHTML =
                '<div class="sat-head">'
              + ' <div class="sat-head-top">'
              + ' <div style="min-width:0;"><h2>병상 포화도</h2><div class="sub" id="satSub">조회 중...</div></div>'
              + ' <div class="sat-actions">'
              + ' <button class="sat-refresh" id="satRefreshBtn">갱신</button>'
              + ' <button class="sat-close" id="satCloseBtn">닫기</button></div>'
              + ' </div>'
              + ' <div class="sat-row" id="satMetrics">'
              + ' <button type="button" class="mt-btn active" data-mt="er">응급실</button>'
              + ' <button type="button" class="mt-btn" data-mt="ward">일반병실</button>'
              + ' <button type="button" class="mt-btn" data-mt="icu">중환자실</button>'
              + ' <button type="button" class="mt-btn" data-mt="load">과밀지수</button>'
              + ' <button type="button" class="mt-info" id="satInfoBtn">ⓘ</button>'
              + ' </div>'
              + ' <div class="sat-row" id="satLv">'
              + ' <button type="button" class="lv-btn" data-lv="권역">권역</button>'
              + ' <button type="button" class="lv-btn" data-lv="센터">센터</button>'
              + ' <button type="button" class="lv-btn" data-lv="기관">기관</button>'
              + ' <button type="button" class="lv-btn" data-lv="모두">모두</button>'
              + ' <button type="button" class="sort-btn" id="satSortBtn">↓ 내림</button>'
              + ' </div>'
              + ' <div class="sat-row">'
              + ' <select id="satSido">' + sidoOpts + '</select>'
              + ' <select id="satGugun"></select>'
              + ' </div></div>'
              + '<div class="sat-body" id="satBody"></div>';
            document.body.appendChild(ov);
            satFillGugun();
            lvPaint();

            document.getElementById('satSido').addEventListener('change', function (e) {
                satSido = e.target.value; satGugun = ''; satFillGugun(); satRender();
            });
            document.getElementById('satGugun').addEventListener('change', function (e) {
                satGugun = e.target.value; satRender();
            });
            ov.addEventListener('click', function (e) {
                var t = e.target;
                if (!t) return;
                if (t.id === 'satCloseBtn') { satClose(); return; }
                if (t.id === 'satRefreshBtn' || t.id === 'satRetryBtn') { satLoad(true); return; }
                if (t.id === 'satSortBtn') {
                    SORT_DESC = !SORT_DESC;
                    t.textContent = SORT_DESC ? '↓ 내림' : '↑ 오름';
                    satRender(); return;
                }
                if (t.id === 'satInfoBtn') {
                    _satFormulaOpen = !_satFormulaOpen;
                    var f = document.getElementById('satFormula');
                    if (f) f.classList.toggle('show', _satFormulaOpen);
                    return;
                }
                var lb = t.closest ? t.closest('#satLv .lv-btn') : null;
                if (lb) { lvClick(lb.getAttribute('data-lv')); return; }
                var mb = t.closest ? t.closest('.mt-btn') : null;
                if (mb) {
                    SAT_METRIC = mb.getAttribute('data-mt');
                    document.querySelectorAll('.mt-btn').forEach(function (x) { x.classList.toggle('active', x === mb); });
                    nsdbg('metric=' + SAT_METRIC); satRender(); return;
                }
                var db = t.closest ? t.closest('.detail-btn') : null;
                if (db) { openDetail(db.getAttribute('data-dt')); return; }
                var it = t.closest ? t.closest('.sat-item') : null;
                if (!it) return;
                var hp = it.getAttribute('data-hpid');
                var row = _satRows.filter(function (r) { return r.hpid === hp; })[0];
                if (!row) return;
                var on = selectedHospitals.some(function (h) { return h.hpid === hp; });
                if (t.tagName !== 'INPUT') { var c0 = it.querySelector('input'); if (c0) c0.checked = !on; }
                if (!on) {
                    if (selectedHospitals.length >= MAX_SEL) {
                        alert('최대 ' + MAX_SEL + '개 병원까지만 선택할 수 있습니다.');
                        var c1 = it.querySelector('input'); if (c1) c1.checked = false; return;
                    }
                    selectedHospitals.push(selRec(row));
                } else {
                    selectedHospitals = selectedHospitals.filter(function (h) { return h.hpid !== hp; });
                }
                updateSelectedBox(); saveToLocalStorage(); satSyncChecks();
            });
            satLoad(false);
        }
        function satFillGugun() {
            var g = document.getElementById('satGugun');
            if (!g) return;
            g.innerHTML = '<option value="">' + (satSido ? '시/군/구 전체' : '— 시/도 먼저') + '</option>';
            if (satSido && districts[satSido]) {
                districts[satSido].forEach(function (x) {
                    var o = document.createElement('option');
                    o.value = x; o.textContent = x;
                    if (x === satGugun) o.selected = true;
                    g.appendChild(o);
                });
            }
            g.disabled = !satSido;
        }
        try { document.getElementById('satBtn').onclick = openSaturation; } catch (e) {}

        try { updateSelectedBox(); } catch (e) {}
    </script>
</body>
</html>
'''

COMPARE_WINDOW_HTML = '''
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>응급의료상황판 ({{ num_hospitals }}개 병원)</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body { font-family: 'Malgun Gothic', sans-serif; background: #f5f5f5; padding: 10px; }
        .header {
            position: relative;
            background: white; padding: 10px 12px 12px; border-radius: 10px;
            margin-bottom: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); text-align: center;
        }
        /* 제목 축소 + 좌우 여백 제거로 정확한 중앙정렬 */
        .hdr-row { position: relative; display: flex; align-items: center;
                   justify-content: center; min-height: 2.2em; }
        .hdr-right { position: absolute; right: 0; top: 50%;
                     transform: translateY(-50%); display: flex;
                     align-items: center; gap: 8px; }
        /* 복귀 버튼: 헤더 좌상단 모서리에 밀착, 각진 형태 */
        .header { padding-top: 6px; }
        /* 제목: 축소본의 2배, 채도 +30% (#667eea -> #525DFE) */
        .header h1 { color: #525DFE; font-size: calc({{ title_font_size }} * 1.24);
                     margin: 0; padding-left: 0; }
        .back-sel {
            position: absolute; top: 0; left: 0; z-index: 5;
            background: #eef0f3; color: #7b8492; border: 1px solid #dfe3e8;
            border-radius: 0; padding: 1px 4px; font-size: 0.58rem;
            font-weight: 600; line-height: 1.15; cursor: pointer;
        }
        .back-sel:active { background: #e2e6ea; }
        /* 응급의료상황판 폰트 +30% */
        .header h1 .h1-main { font-size: 0.91em; }
        .header h1 .h1-sub,
        .header .hdr-right .h1-sub { font-size: calc({{ title_font_size }} * 1.24 * 0.55);
                                     font-weight: normal; color: #000; white-space: nowrap; }
        .header .time { color: #666; font-size: {{ base_font_size }}; }
        .comparison-wrapper {
            background: white; border-radius: 10px; padding: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1); overflow-x: auto;
        }
        .comparison-table {
            width: 100%; border-collapse: collapse;
            font-size: {{ table_font_size }}; table-layout: fixed;
        }
        .comparison-table th, .comparison-table td {
            border: 1px solid #ddd; padding: 1px 1px;
            text-align: center; vertical-align: middle; line-height: 1.1;
        }
        .comparison-table thead th {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white; font-weight: 600; position: sticky; top: 0; z-index: 10;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
            padding: 4px 2px !important; line-height: 1.2;
        }
        /* 병원명: 줄바꿈 없이 최대 크기. 상한은 갱신주기 라벨과 동일 */
        .comparison-table thead th.hospital-name {
            font-size: clamp(0.42rem, 2.0vw, {{ base_font_size }});
            white-space: nowrap; word-break: keep-all; line-height: 1.25;
            max-width: 150px; overflow: hidden; text-overflow: ellipsis;
        }
        .comparison-table thead th.hospital-name.long-name {
            font-size: clamp(0.36rem, 1.5vw, calc({{ base_font_size }} * 0.85));
            white-space: nowrap; word-break: keep-all; line-height: 1.25;
            max-width: 150px; overflow: hidden; text-overflow: ellipsis;
        }
        .comparison-table thead th.hospital-name.very-long-name {
            font-size: clamp(0.30rem, 1.15vw, calc({{ base_font_size }} * 0.7));
            white-space: nowrap; word-break: keep-all; line-height: 1.25;
            max-width: 150px; overflow: hidden; text-overflow: ellipsis;
        }
        .comparison-table thead th:first-child { width: 15%; min-width: 60px; }
        /* 조회화면 컨트롤 높이 — 갱신주기 콤보박스 기준(변경 전 높이)으로 통일 */
        /* '표시 항목' 버튼만 녹색 */
        .refresh-controls button#secBtn { background: #2e7d32; }
        .refresh-controls button,
        .refresh-controls select,
        .refresh-controls input[type="number"],
        .refresh-controls .rc-btn {
            height: auto; min-height: 0; box-sizing: border-box;
            padding: 0px 4px; line-height: 1.35;
            font-size: {{ base_font_size }};
            text-align: center; vertical-align: middle;
        }
        .refresh-controls { 
            display: flex; align-items: center; justify-content: center;
            gap: 4px; margin-top: 4px; flex-wrap: wrap;
        }
        /* 갱신주기 레이블 — 최대 크기 */
        .refresh-controls label {
            font-size: {{ base_font_size }};
            color: #666; font-weight: 700; white-space: nowrap;
        }
        /* 갱신주기 콤보박스 — 최대 크기, 여백 최소 */
        .refresh-controls select {
            padding: 0px 2px; border: 1px solid #667eea; border-radius: 4px;
            font-size: {{ base_font_size }};
            font-weight: 700;
            cursor: pointer; background: white;
            height: auto; box-sizing: border-box;
            white-space: nowrap;
        }
        /* 즉시갱신 + 백그라운드 버튼 — 최대 크기, 여백 최소 */
        .refresh-controls button,
        .refresh-controls .rc-btn {
            padding: 0px 4px; background: #667eea; color: white; border: none;
            border-radius: 4px; font-size: {{ base_font_size }};
            font-weight: 800; cursor: pointer; transition: background 0.2s;
            white-space: nowrap; height: auto; box-sizing: border-box;
        }
        .refresh-controls button:hover { background: #5568d3; }
        #pipBtn {
            background: #5b21b6 !important;
            padding: 0px 4px !important;
            font-size: {{ base_font_size }} !important;
            font-weight: 800 !important;
            height: auto !important; box-sizing: border-box !important;
        }
        #pipBtn:hover { background: #6d28d9 !important; }
        .category-header {
            background: #f0f4ff !important; font-weight: 700; color: #667eea;
            text-align: center; font-size: {{ category_font_size }};
            padding: 1px 1px !important; line-height: 1.1;
        }
        .item-label {
            background: #f9f9f9; font-weight: 600; text-align: center !important;
            padding: 1px 1px 1px 2px !important; font-size: {{ label_font_size }}; line-height: 1.1;
        }
        .bed-cell { padding: 1px 1px !important; }
        .bed-info { display: flex; flex-direction: column; align-items: center; position: relative; width: 100%; }
        .bar-container {
            width: 100%; height: 10px;
            background: #d4d4d4;
            border-radius: 4px; overflow: hidden; position: relative;
        }
        .bar { height: 100%; transition: width 0.3s; }
        .bar-green    { background: #75B878; }
        .bar-yellow   { background: #FFCC55; }
        .bar-red      { background: #E48785; }
        .bar-dark-red { background: #B71C1C; }
        /* 병상 텍스트 오버레이 */
        .bed-text-overlay {
            position: absolute; top: 50%; left: 50%;\n            transform: translate(-50%, -50%);\n            font-size: 0.87em; font-weight: 500; white-space: nowrap; z-index: 1;
        }
        /* 모든 병상 텍스트 동일 크기 0.87em, 굵기 500 */
        .bed-text-overlay.green-text { color: white; text-shadow: 1px 1px 2px rgba(0,0,0,0.7); }
        #globalRefreshOverlay { font-size: 0.60em !important; }
        .bed-text-overlay.yellow-text { color: #F57C00; text-shadow: 0 0 2px white, 0 0 2px white; -webkit-text-stroke: 0.4px #F57C00; }
        .bed-text-overlay.red-text { color: #9e1f1f; text-shadow: 0 0 2px white, 0 0 2px white; -webkit-text-stroke: 0.4px #9e1f1f; }
        .bed-text-overlay.dark-red-text { color: #000; text-shadow: 0 0 2px white, 0 0 2px white; }
        .bed-numbers { font-weight: 600; font-size: {{ bed_number_font_size }}; white-space: nowrap; }
        .bed-numbers .available { color: #d32f2f; font-size: 1.1em; }
        .bed-numbers .total { color: #999; font-size: 0.9em; }
        .no-data { color: #999; font-style: italic; }
        .equipment-cell { font-size: 1.29em; font-weight: 700; padding: 2px !important; line-height: 1.0; }
        .equipment-available { color: #2E7D32; }
        .equipment-unavailable { color: #C62828; }
        /* 중증질환 키오스크: 행높이 최소화, 텍스트 모두 표시 */
        .kiosk-cell {
            padding: 1px 2px !important;
            vertical-align: top;
            text-align: center;
            line-height: 1.1;
        }
        .kiosk-msg {
            font-size: 0.58em;
            white-space: normal;
            word-break: break-word;
            overflow: visible;
            line-height: 1.15;
            margin-top: 1px;
        }
        .exception-cell {
            font-size: calc({{ exception_font_size }} * 1.2); padding: 4px 2px !important;
            word-wrap: break-word; white-space: normal; max-width: 200px; line-height: 1.3;
        }
        .exception-ok { color: #4CAF50; font-weight: 600; }
        .exception-warning { color: #f44336; font-weight: 600; line-height: 1.4; }
        /* 예외상황 '열 제목'만 적색 (셀 내용 색상은 기존 유지) */
        .category-header.cat-exception { color: #f44336; }
        .bell-btn {
            position: absolute; top: 1px; right: 1px; z-index: 12;
            font-size: 0.8rem; line-height: 1; padding: 2px 3px;
            border-radius: 6px; cursor: pointer; opacity: 0.75;
            background: rgba(0,0,0,0.25);
        }
        .bell-btn.bell-on {
            opacity: 1; background: #ffc107; box-shadow: 0 0 5px #ffc107;
        }
        .bell-btn.pin-btn { right: auto; left: 1px; }
    </style>
</head>
<body>
    <div class="header">
        <button class="back-sel" id="backSelBtn" title="병원 선택 화면으로"
                onclick="ermonBackToSelect()">← 병원 선택</button>
        <div class="hdr-row">
            <h1><span class="h1-main">응급의료상황판</span></h1>
            <div class="hdr-right">
                <span class="h1-sub">(<span id="queryTime">{{ current_time }}</span>기준)</span>
            </div>
        </div>
        <div class="refresh-controls">
            <label for="refreshInterval">갱신주기:</label>
            <select id="refreshInterval">
                <option value="0">수동</option>
                <option value="60000">1분</option>
                <option value="180000" selected>3분</option>
                <option value="300000">5분</option>
                <option value="600000">10분</option>
                <option value="1800000">30분</option>
                <option value="custom">직접입력</option>
            </select>
            <button id="refreshNow">즉시 갱신</button>
            <button id="pipBtn">백그라운드</button>
            <button id="saveHtmlBtn">저장</button>
            <button id="monitorBtn">모니터</button>
            <button id="secBtn">항목</button>
        </div>
        <!-- 갱신 진행바 -->
        <script>
        // 병원 선택 화면 복귀 + 안드로이드 백버튼 가로채기(앱 종료 방지)
        function ermonBackToSelect() {
            // 저장본에서는 이 화면이 상위 문서의 iframe(srcdoc) 안에 있다.
            // history.back() 을 쓰면 최상위 문서가 원본 content:// 로 되돌아가
            // 권한 만료로 ERR_FILE_NOT_FOUND 가 난다. 오버레이만 닫는다.
            var inFrame = false;
            try { inFrame = !!(window.parent && window.parent !== window); }
            catch (e) { inFrame = true; }
            // ① 동일 출처(앱 내부 iframe): 부모 API 직접 호출
            try {
                if (inFrame && window.parent.EXAPP && window.parent.EXAPP.closeCompare) {
                    window.parent.EXAPP.closeCompare();
                    return;
                }
            } catch (e) {}
            // ② [ROOT-FIX 2026-G2] 저장본의 비교화면은 about:srcdoc 프레임이고,
            //    부모 문서가 file:// · content:// 이면 opaque origin 이 되어
            //    window.parent.EXAPP 접근이 SecurityError 로 차단된다.
            //    → ①이 조용히 실패하고 ③④도 조건 불일치라 [뒤로가기]·[← 병원 선택]
            //      양쪽이 완전 무반응이 되었다. postMessage 는 교차출처에서도
            //      동작하므로 프레임 안에서는 이 경로를 정규 경로로 사용한다.
            if (inFrame) {
                try {
                    window.parent.postMessage({ ermonClose: 1 }, '*');
                    return;
                } catch (e) {}
            }
            try {
                if (location.protocol === 'http:' || location.protocol === 'https:') {
                    location.href = '/';
                    return;
                }
            } catch (e) {}
            try {
                if (!inFrame && history.length > 1) { history.back(); return; }
            } catch (e) {}
            try { window.close(); } catch (e) {}
        }
        (function () {
            // 백버튼 가로채기는 최상위 문서에서만. iframe 에서 걸면 상위가 이탈한다.
            //  (프레임일 때는 부모 글루가 popstate 를 잡아 오버레이를 닫는다)
            try {
                if (window.top !== window) return;
                history.pushState({ ermon: 1 }, '', location.href);
                window.addEventListener('popstate', function () {
                    ermonBackToSelect();
                });
            } catch (e) {}
        })();
        </script>
        <div style="margin: 0 0 0 0; padding-top: 0;">
            <div class="bed-cell" style="max-width: 400px; margin: 0 auto; padding: 0;">
                <div class="bed-info">
                    <div class="bar-container" style="height: 10px; overflow: visible;">
                        <div class="bar bar-green" id="globalRefreshBar" style="width: 100%"></div>
                        <div class="bed-text-overlay green-text" id="globalRefreshOverlay"
                             style="font-size:0.60em;white-space:nowrap;overflow:visible;
                                    top:50%;transform:translate(-50%,-50%);line-height:1;">
                              <span id="globalRefreshText">3:00</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    {{ content|safe }}
    <script>
        // ── 타이머 변수 ─────────────────────────────────────────────
        let refreshTimer   = null;   // setTimeout 핸들
        let countdownTimer = null;   // setInterval 핸들 (1초 카운트다운)
        let currentInterval = 180000;
        let nextRefreshTime = null;  // 절대 타임스탬프 (ms)
        let isRefreshing    = false; // AJAX 갱신 중 플래그

        // ── AJAX 갱신 ──────────────────────────────────────────────
        function _hapticBeep() {
            try { if(navigator.vibrate) navigator.vibrate(40); } catch(e){}
            try {
                var AC = window.AudioContext || window.webkitAudioContext;
                if(AC) {
                    var ctx = new AC();
                    var osc = ctx.createOscillator();
                    var gain = ctx.createGain();
                    osc.connect(gain); gain.connect(ctx.destination);
                    osc.type = 'sine';
                    osc.frequency.setValueAtTime(1047, ctx.currentTime);
                    osc.frequency.setValueAtTime(1319, ctx.currentTime + 0.06);
                    gain.gain.setValueAtTime(0.12, ctx.currentTime);
                    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.20);
                    osc.start(ctx.currentTime); osc.stop(ctx.currentTime + 0.20);
                }
            } catch(e){}
        }

        // ── 병상 알림 모니터 () ──────────────────────────────
        function bedToast(msg, dur) {
            try {
                const t = document.createElement('div');
                t.textContent = msg;
                t.style.cssText = 'position:fixed;bottom:70px;left:50%;transform:translateX(-50%);'
                    + 'background:rgba(30,30,30,0.92);color:#fff;padding:9px 16px;border-radius:20px;'
                    + 'font-size:0.85rem;z-index:9999;max-width:86vw;text-align:center;';
                document.body.appendChild(t);
                setTimeout(() =>t.remove(), dur || 2500);
            } catch(e) {}
        }
        // ──  병상 모니터 패널 (복수 병원 선택 · 주기 · 방식 · 카운트다운) ──
        function _monHospitalsFromPage() {
            const q = new URLSearchParams(location.search);
            let h = q.get('h') || '';
            if (!h) {
                const hp = q.get('hpids') || '', sd = q.get('sido') || '', gg = q.get('gugun') || '';
                if (hp && sd && gg) h = hp.split(',').map(x =>x + '|' + sd + '|' + gg).join(',');
            }
            const ths = document.querySelectorAll('.comparison-table thead th');
            const names = [];
            for (let i = 1; i < ths.length; i++) names.push(ths[i].innerText.split('\\n')[0].trim());
            const out = [];
            h.split(',').forEach((t, i) => {
                const p = t.split('|');
                if (p.length >= 3 && p[0].trim())
                    out.push({ hpid: p[0].trim(), sido: p[1].trim(), gugun: p[2].trim(),
                               name: names[i] || p[0].trim() });
            });
            return { list: out, h: h };
        }
        let _monTimer = null;
        async function openMonitorPanel() {
            closeMonitorPanel();
            const info = _monHospitalsFromPage();
            let st = { running: false, hospitals: [], mode: 'notify', iv: 180, next_epoch: 0 };
            try { st = await (await fetch('/api/bed_notify_status')).json(); } catch (e) {}
            const runSet = new Set((st.hospitals || []).map(x =>x.hpid));
            const wrap = document.createElement('div');
            wrap.id = 'monPanel';
            wrap.style.cssText = 'position:fixed;inset:0;z-index:9998;background:rgba(0,0,0,0.45);'
                + 'display:flex;align-items:center;justify-content:center;';
            const rows = info.list.map(hh =>
                '<label style="display:flex;align-items:center;gap:8px;padding:7px 4px;'
                + 'border-bottom:1px solid #eee;font-size:0.92rem;">'
                + '<input type="checkbox" class="mon-hp" value="' + hh.hpid + '" '
                + ((!st.running || runSet.has(hh.hpid)) ? 'checked' : '') + '>'
                + '<span>' + hh.name + '</span></label>').join('');
            wrap.innerHTML =
                '<div style="background:#fff;border-radius:14px;max-width:340px;width:88vw;'
                + 'padding:16px 16px 12px;box-shadow:0 8px 30px rgba(0,0,0,0.3);">'
                + '<div style="font-weight:700;margin-bottom:8px;">병상 모니터 '
                + '<span id="monState" style="font-size:0.75rem;color:#667eea;font-weight:600;"></span></div>'
                + '<div style="max-height:38vh;overflow:auto;">' + rows + '</div>'
                + '<div style="display:flex;gap:10px;align-items:center;margin:10px 0 4px;font-size:0.85rem;">'
                + '주기 <select id="monIv" style="padding:4px 6px;border-radius:8px;">'
                + '<option value="60">1분</option><option value="180" selected>3분</option>'
                + '<option value="300">5분</option><option value="600">10분</option></select>'
                + '방식 <select id="monMode" style="padding:4px 6px;border-radius:8px;">'
                + '<option value="notify">알림</option><option value="overlay">오버레이</option>'
                + '<option value="mini">미니창(PiP 그래프)</option></select></div>'
                + '<div style="display:flex;gap:8px;margin-top:10px;">'
                + '<button id="monStart" style="flex:1;padding:9px;border:none;border-radius:10px;'
                + 'background:#667eea;color:#fff;font-weight:700;">시작/변경</button>'
                + '<button id="monStop" style="flex:1;padding:9px;border:none;border-radius:10px;'
                + 'background:#e0e0e0;font-weight:700;">중지</button>'
                + '<button id="monMiniStyle" title="미니창 스타일" style="padding:9px 12px;'
                + 'border:none;border-radius:10px;background:#ede7f6;font-weight:700;">스타일</button>'
                + '<button id="monClose" style="padding:9px 12px;border:none;border-radius:10px;'
                + 'background:#f5f5f5;">닫기</button></div></div>';
            document.body.appendChild(wrap);
            if (st.running) {
                document.getElementById('monIv').value = String(st.iv);
                document.getElementById('monMode').value = st.mode;
            }
            const stateEl = document.getElementById('monState');
            function tick() {
                if (!st.running || !st.next_epoch) {
                    stateEl.textContent = st.running ? '· 실행 중' : '· 꺼짐';
                    return;
                }
                const s = Math.max(0, Math.round(st.next_epoch - Date.now() / 1000));
                stateEl.textContent = '· 실행 중 · 다음 갱신 '
                    + Math.floor(s / 60) + ':' + ('0' + (s % 60)).slice(-2);
            }
            tick();
            _monTimer = setInterval(tick, 1000);
            wrap.addEventListener('click', e => { if (e.target === wrap) closeMonitorPanel(); });
            document.getElementById('monClose').onclick = closeMonitorPanel;
            document.getElementById('monStart').onclick = () => _monSend('start', info);
            document.getElementById('monStop').onclick = () => _monSend('stop', info);
            document.getElementById('monMiniStyle').onclick = () => {
                closeMonitorPanel();
                try { LIVE_MINI.stylePanel(); } catch (e) { bedToast('오류: ' + e.message); }
            };
        }
        function closeMonitorPanel() {
            if (_monTimer) { clearInterval(_monTimer); _monTimer = null; }
            const p = document.getElementById('monPanel');
            if (p) p.remove();
        }
        async function _monSend(action, info) {
            const _md = document.getElementById('monMode').value;
            if (_md === 'mini') {   // 브라우저 PiP 미니창 — 서버 개입 없이 페이지가 구동
                closeMonitorPanel();
                if (action === 'stop') { try { LIVE_MINI.close(); } catch (e) {} bedToast('미니창 닫음'); }
                else { try { LIVE_MINI.toggle(); } catch (e) { bedToast('미니창 오류: ' + e.message); } }
                return;
            }
            const sel = Array.from(document.querySelectorAll('.mon-hp:checked')).map(c =>c.value);
            const hospitals = info.list.filter(hh =>sel.includes(hh.hpid));
            const body = { action: action, hospitals: hospitals, h: info.h,
                           iv: parseInt(document.getElementById('monIv').value),
                           mode: document.getElementById('monMode').value };
            try {
                const r = await fetch('/api/bed_notify', { method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body) });
                const d = await r.json();
                bedToast(d.msg || (d.ok ? '완료' : '실패'));
                if (d.warn) setTimeout(() =>bedToast(' ' + d.warn, 5500), 700);
            } catch (e) { bedToast('요청 실패: ' + e.message); }
            closeMonitorPanel();
        }
        try { document.getElementById('monitorBtn').onclick = openMonitorPanel; } catch (e) {}

        // ──  저장: 현재 병원 구성 그대로 단독 HTML 다운로드 ─────
        try {
            document.getElementById('saveHtmlBtn').onclick = function () {
                let h = new URLSearchParams(location.search).get('h') || '';
                if (!h) {
                    const q = new URLSearchParams(location.search);
                    const hp = q.get('hpids') || '', sd = q.get('sido') || '', gg = q.get('gugun') || '';
                    if (hp && sd && gg) h = hp.split(',').map(x =>x + '|' + sd + '|' + gg).join(',');
                }
                if (!h) { bedToast('저장할 병원 구성이 없습니다.'); return; }
                const iv = document.getElementById('refreshInterval').value;
                location.href = '/export?h=' + encodeURIComponent(h) + '&iv=' + iv;
            };
        } catch (e) {}

        // ── 서버 연결 끊김 자동 재접속 (Pydroid 프로세스 재시작 대비) ──
        let _reconT = null;
        function startReconnect(after) {
            if (_reconT) return;
            const bar = document.createElement('div');
            bar.id = 'reconBar';
            bar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;'
                + 'background:#c62828;color:#fff;padding:8px;text-align:center;'
                + 'font-size:0.85rem;font-weight:600;';
            bar.textContent = ' 서버 연결 끊김 — Pydroid 3(파이썬 앱)를 다시 열어주세요. 자동 재접속 대기 중...';
            document.body.appendChild(bar);
            _reconT = setInterval(async () => {
                try {
                    const r = await fetch('/api/bed_notify_status', { cache: 'no-store' });
                    if (r.ok) {
                        clearInterval(_reconT); _reconT = null;
                        bar.style.background = '#2e7d32';
                        bar.textContent = ' 서버 재연결됨 — 다시 시도합니다';
                        setTimeout(() => { try { bar.remove(); } catch (e) {} }, 1500);
                        if (after) { try { after(); } catch (e) {} }
                    }
                } catch (e) {}
            }, 4000);
        }

        // ──  라이브 미니창 (저장본과 동일: PiP 그래프 · 메인 조회와 완전 동기) ──
        const LIVE_MINI = (function () {
            var docWin = null, video = null, canvas = null, track = null, canvasTimer = null;
            var lastMs = null, lastTs = '';
            var lockW = 0, lockH = 0;   // PiP 중 캔버스 해상도 고정
            var STYLE = { radius: 0, border: '1px solid #555', bg: 'rgba(0,0,0,0.85)',
                          bgSolid: '#000000', color: '#ffffff', weight: '700', fontSize: 44, opacity: 85 };
            var DEF = JSON.parse(JSON.stringify(STYLE));
            try {
                var _sv = JSON.parse(localStorage.getItem('exMiniStyle') || 'null');
                if (_sv) Object.keys(STYLE).forEach(function (k) { if (_sv[k] !== undefined) STYLE[k] = _sv[k]; });
            } catch (e) {}
            // ── 스타일 샘플(프리셋)·투명도·탭 변경 유틸 ──
            var PRESETS = [
                { name: '검정', s: { radius: 0,  border: '1px solid #555', bg: 'rgba(0,0,0,0.85)', bgSolid: '#000000', color: '#ffffff', weight: '700', fontSize: 44, opacity: 85 } },
                { name: '화이트', s: { radius: 10, border: '1px solid #bbb', bg: 'rgba(255,255,255,0.92)', bgSolid: '#f2f2f2', color: '#111111', weight: '700', fontSize: 44, opacity: 92 } },
                { name: '유리', s: { radius: 14, border: '1px solid #9ec1d9', bg: 'rgba(210,230,245,0.55)', bgSolid: '#d7e6f2', color: '#0b2b45', weight: '700', fontSize: 44, opacity: 55 } },
                { name: '고대비', s: { radius: 0, border: '2px solid #ffffff', bg: 'rgba(0,0,0,0.95)', bgSolid: '#000000', color: '#ffee00', weight: '800', fontSize: 48, opacity: 95 } },
                { name: '녹색', s: { radius: 6,  border: '1px solid #00aa55', bg: 'rgba(0,40,25,0.85)', bgSolid: '#002819', color: '#4dff9d', weight: '700', fontSize: 44, opacity: 85 } }
            ];
            var presetIdx = 0;
            function styleGet(k) { return STYLE[k]; }
            function _miniKick() { try { refreshPage(); } catch (e) {} }
            var IV_CYCLE = [60000, 180000, 300000, 600000, 0];
            function _ivLabel() {
                var v = (typeof currentInterval !== 'undefined') ? currentInterval : 0;
                if (!(v >0)) return '수동';
                return v >= 60000 ? Math.round(v / 60000) + '분' : Math.round(v / 1000) + '초';
            }
            function setMainInterval(ms) {   // 미니창 ↔ 메인 주기 완전 동기
                try {
                    var s = document.getElementById('refreshInterval');
                    s.value = String(ms);
                    startAutoRefresh();
                } catch (e) {}
            }
            function cycleMainInterval() {
                var cur = (typeof currentInterval !== 'undefined') ? currentInterval : 180000;
                var i = IV_CYCLE.indexOf(cur);
                setMainInterval(IV_CYCLE[(i + 1 + IV_CYCLE.length) % IV_CYCLE.length]);
            }
            function withAlpha(cs, pct) {
                var a = Math.max(0, Math.min(100, parseInt(pct))) / 100;
                if (isNaN(a)) return cs;
                var s2 = String(cs).trim();
                if (s2.slice(0, 4) === 'rgba' || s2.slice(0, 4) === 'rgb(') {
                    var inner = s2.slice(s2.indexOf('(') + 1, s2.lastIndexOf(')'));
                    var p = inner.split(',');
                    if (p.length >= 3)
                        return 'rgba(' + p[0].trim() + ',' + p[1].trim() + ',' + p[2].trim() + ',' + a + ')';
                }
                if (s2.charAt(0) === '#' && s2.length === 7) {
                    var v = parseInt(s2.slice(1), 16);
                    if (!isNaN(v))
                        return 'rgba(' + ((v >>16) & 255) + ',' + ((v >>8) & 255) + ',' + (v & 255) + ',' + a + ')';
                }
                return cs;
            }
            function applyStyleObj(s, save) {
                Object.keys(s).forEach(function (k) { STYLE[k] = s[k]; });
                if (save) { try { localStorage.setItem('exMiniStyle', JSON.stringify(STYLE)); } catch (e) {} }
                if (docWin) { applyDocCss(); renderDoc(); }
                if (canvas) drawCanvas();
            }
            function announcePreset(nm) {
                try {
                    if (navigator.mediaSession && window.MediaMetadata)
                        navigator.mediaSession.metadata = new MediaMetadata({ title: '미니창 스타일: ' + nm });
                } catch (e) {}
                try { bedToast('미니창 스타일: ' + nm); } catch (e) {}
            }
            function cyclePreset(dir) {
                presetIdx = (presetIdx + (dir || 1) + PRESETS.length) % PRESETS.length;
                applyStyleObj(PRESETS[presetIdx].s, true);
                announcePreset(PRESETS[presetIdx].name);
            }
            function hParam() {
                const q = new URLSearchParams(location.search);
                let h = q.get('h') || '';
                if (!h) {
                    const hp = q.get('hpids') || '', sd = q.get('sido') || '', gg = q.get('gugun') || '';
                    if (hp && sd && gg) h = hp.split(',').map(x =>x + '|' + sd + '|' + gg).join(',');
                }
                return h;
            }
            function ratioColor(a, t) {
                if (a < 0 || t <= 0) return '#9e9e9e';
                var p = a / t;
                return p >= 0.5 ? '#2eff7b' : (p >= 0.2 ? '#ffd54f' : '#ff5252');
            }
            // PiP 모니터와 동일 팔레트 — bright=가용, dark=사용(옅은 동일계열)
            function ratioPair(a, t) {
                if (a < 0 && t <= 0) return { bright: '#333333', dark: '#222222' };
                if (t <= 0)          return { bright: '#E05550', dark: '#511210' };
                var p = Math.max(0, a) / t;
                if (p >= 0.5) return { bright: '#6BC96E', dark: '#1E421F' };
                if (p >= 0.2) return { bright: '#EDBB4A', dark: '#58400A' };
                return { bright: '#E05550', dark: '#511210' };
            }
            function valTxt(a, t) { return (a < 0 ? '-' : a) + '/' + (t >0 ? t : '-'); }
            async function fetchMetrics() {
                try {
                    const r = await fetch('/pip_data?h=' + encodeURIComponent(hParam()), { cache: 'no-store' });
                    const d = await r.json();
                    lastTs = String(d.fetched_at || '').slice(0, 5);
                    lastMs = (d.hospitals || []).map(function (x) {
                        var ga = (x.hvgc < 0 && x.hv36 < 0) ? -1 : Math.max(x.hvgc, 0) + Math.max(x.hv36, 0);
                        var gt = Math.max(x.hvgc_t || 0, 0) + Math.max(x.hv36_t || 0, 0);
                        return { name: x.name,
                                 m: [ { lbl: '응급', a: x.hvec, t: (x.hvec_t >0 ? x.hvec_t : 0) },
                                      { lbl: '입원', a: ga, t: gt },
                                      { lbl: '중환', a: x.hicu, t: (x.hicu_t >0 ? x.hicu_t : 0) } ] };
                    });
                    return;
                } catch (e) { /* 서버 끊김 → 직접조회 데이터로 폴백 */ }
                const hd = window.__lastHd;
                if (!hd || !hd.length) throw new Error('서버 끊김 · 폴백 데이터 없음');
                const now = new Date();
                lastTs = ('0' + now.getHours()).slice(-2) + ':' + ('0' + now.getMinutes()).slice(-2);
                lastMs = hd.map(function (h) {
                    function pv(p) { return { a: (p && p.avail !== undefined) ? p.avail : -1,
                                              t: (p && p.total >0) ? p.total : 0 }; }
                    var e2 = pv((h.emergency || {}).hvec);
                    var g1 = pv((h.general || {}).hvgc), g2 = pv((h.general || {}).hv36);
                    var ga = (g1.a < 0 && g2.a < 0) ? -1 : Math.max(g1.a, 0) + Math.max(g2.a, 0);
                    var i2 = pv((h.icu || {}).hvicc);
                    return { name: h.name,
                             m: [ { lbl: '응급', a: e2.a, t: e2.t },
                                  { lbl: '입원', a: ga, t: g1.t + g2.t },
                                  { lbl: '중환', a: i2.a, t: i2.t } ] };
                });
            }
            function applyDocCss() {
                if (!docWin) return;
                var _fs = parseInt(STYLE.fontSize) || 15;
                docWin.document.body.style.cssText =
                    'margin:0;padding:8px 10px;font-family:sans-serif;'
                    + 'font-size:' + _fs + 'px;'
                    + 'background:' + withAlpha(STYLE.bg, STYLE.opacity === undefined ? 100 : STYLE.opacity)
                    + ';border:' + STYLE.border + ';'
                    + 'border-radius:' + STYLE.radius + 'px;box-sizing:border-box;'
                    + 'color:' + STYLE.color + ';font-weight:' + STYLE.weight + ';';
            }
            function tick(d) {
                var bar = d.getElementById('lmBar'), cnt = d.getElementById('lmCnt');
                if (!bar || !cnt) return;
                if (!(currentInterval >0) || !nextRefreshTime) {
                    bar.style.width = '100%'; cnt.textContent = '수동'; return;
                }
                var remain = Math.max(0, nextRefreshTime - Date.now());
                bar.style.width = Math.max(0, Math.min(100, remain / currentInterval * 100)) + '%';
                var s = Math.ceil(remain / 1000);
                cnt.textContent = Math.floor(s / 60) + ':' + ('0' + (s % 60)).slice(-2);
                var ib = d.getElementById('mnIv') || d.getElementById('lmIv');
                if (ib) ib.textContent = '' + _ivLabel();
            }
            function renderDoc() {
                if (!docWin || !lastMs) return;
                var fs = parseInt(STYLE.fontSize) || 44;
                var fsName = Math.max(12, Math.round(fs * 0.46));
                var fsSm = Math.max(10, Math.round(fs * 0.40));
                docWin.document.body.innerHTML = lastMs.map(function (H) {
                    var ms = H.m;
                    var cols = ms.map(function (m) {
                        var pr = ratioPair(m.a, m.t);
                        var p = (m.a >= 0 && m.t >0) ? Math.min(1, m.a / m.t) : 0;
                        return '<div style="flex:1;min-width:0;">'
                             + '<div style="height:9px;background:' + pr.dark + ';">'
                             + '<div style="height:100%;width:' + (p * 100) + '%;'
                             + 'background:' + pr.bright + ';"></div></div>'
                             + '<div style="display:flex;justify-content:space-between;gap:4px;'
                             + 'font-size:' + fsSm + 'px;margin-top:2px;white-space:nowrap;">'
                             + '<span style="color:#aaaaaa;">(' + m.lbl + ')</span>'
                             + '<b style="color:' + pr.bright + ';">' + valTxt(m.a, m.t) + '</b></div></div>';
                    }).join('');
                    return '<div style="padding:2px 0 6px;">'
                         + '<div style="font-weight:800;font-size:' + fsName + 'px;white-space:nowrap;'
                         + 'overflow:hidden;text-overflow:ellipsis;">' + H.name + '</div>'
                         + '<div style="display:flex;gap:8px;margin-top:3px;">' + cols + '</div></div>';
                }).join('')
                 + '<div style="display:flex;align-items:center;gap:6px;margin-top:6px;">'
                 + '<div style="flex:1;height:7px;background:rgba(255,255,255,0.18);border:1px solid #000;">'
                 + '<div id="lmBar" style="height:100%;width:100%;background:'
                 + STYLE.color + ';"></div></div>'
                 + '<span id="lmCnt" style="font-weight:800;min-width:42px;'
                 + 'text-align:right;">--:--</span>'
                 + '<button id="lmIv" title="갱신 주기 변경(메인과 동기)" style="border:1px solid #000;'
                 + 'background:#fff;color:#000;font-weight:800;padding:1px 6px;cursor:pointer;">'
                 + _ivLabel() + '</button>'
                 + '<button id="lmRef" title="즉시 갱신" style="border:1px solid #000;background:#fff;'
                 + 'color:#000;font-weight:800;padding:1px 8px;cursor:pointer;">⟳</button>'
                 + '<button id="lmCls" title="닫기" style="border:1px solid #000;background:#fff;'
                 + 'color:#000;font-weight:800;padding:1px 8px;cursor:pointer;">X</button></div>'
                 + '<div style="text-align:right;font-weight:800;'
                 + 'margin-top:3px;">' + lastTs + ' 갱신</div>';
                tick(docWin.document);
            }
            function drawCanvas() {
                if (!canvas || !lastMs || !lastMs.length) return;
                var CW = 1280, CH = 720;   // 16:9 고정 = PiP 창 비율 (무왜곡)
                if (canvas.width !== CW || canvas.height !== CH) { canvas.width = CW; canvas.height = CH; }
                var g = canvas.getContext('2d');
                g.setTransform(1, 0, 0, 1, 0, 0);
                g.fillStyle = '#000';
                g.fillRect(0, 0, CW, CH);
                g.fillStyle = withAlpha(STYLE.bgSolid, STYLE.opacity === undefined ? 100 : STYLE.opacity);
                g.fillRect(0, 0, CW, CH);
                var fscale = Math.max(0.6, Math.min(1.6, (parseInt(STYLE.fontSize) || 44) / 44));
                var n = lastMs.length, hFoot = 62, top = 10;
                var rowH = (CH - top - hFoot) / n;
                var padX = 26, colW = (CW - padX * 2) / 3, gap = 14;
                lastMs.forEach(function (H, i) {
                    var ms = H.m;
                    var y0 = top + rowH * i;
                    var fsName = Math.max(18, Math.min(54, rowH * 0.30 * fscale));
                    var fsVal  = Math.max(15, Math.min(46, rowH * 0.25 * fscale));
                    g.textBaseline = 'top';
                    g.textAlign = 'left';
                    g.fillStyle = STYLE.color;
                    g.font = '800 ' + Math.round(fsName) + 'px sans-serif';
                    var nm = H.name;
                    while (nm.length >2 && g.measureText(nm).width >CW - padX * 2) nm = nm.slice(0, -1);
                    g.fillText(nm, padX, y0 + rowH * 0.04);   // 병원명 = 병상정보 위
                    var barH = Math.max(10, rowH * 0.15);
                    var barY = y0 + rowH * 0.44;
                    var labY = barY + barH + Math.max(4, rowH * 0.05);
                    ms.forEach(function (m, k) {              // 응급·입원·중환 = 고정 3열
                        var x = padX + colW * k, w = colW - gap;
                        var pr = ratioPair(m.a, m.t);
                        var p = (m.a >= 0 && m.t >0) ? Math.min(1, m.a / m.t) : 0;
                        g.fillStyle = pr.dark;               // 사용 병상 = 옅은 동일계열
                        g.fillRect(x, barY, w, barH);
                        g.fillStyle = pr.bright;             // 가용 병상 = 밝은색
                        g.fillRect(x, barY, w * p, barH);
                        g.font = '700 ' + Math.round(fsVal) + 'px sans-serif';
                        g.textAlign = 'left';
                        g.fillStyle = '#aaaaaa';
                        g.fillText('(' + m.lbl + ')', x, labY);
                        g.textAlign = 'right';
                        g.fillStyle = pr.bright;
                        g.fillText(valTxt(m.a, m.t), x + w, labY);
                    });
                });
                var remainMs = (currentInterval >0 && nextRefreshTime)
                    ? Math.max(0, nextRefreshTime - Date.now()) : 0;
                var pct = (currentInterval >0)
                    ? Math.max(0, Math.min(1, remainMs / currentInterval)) : 1;
                var by = CH - hFoot + 8;
                g.fillStyle = 'rgba(255,255,255,0.22)';
                g.fillRect(26, by, CW - 52, 12);
                g.fillStyle = STYLE.color;
                g.fillRect(26, by, (CW - 52) * pct, 12);
                var sL = Math.ceil(remainMs / 1000);
                var cdt = (currentInterval >0)
                    ? (Math.floor(sL / 60) + ':' + ('0' + (sL % 60)).slice(-2)) : '수동';
                g.textBaseline = 'top';
                g.fillStyle = STYLE.color;
                g.font = '800 26px sans-serif';
                g.textAlign = 'left';
                g.fillText(cdt, 26, by + 20);
                g.font = '800 28px sans-serif';
                g.textAlign = 'right';
                g.fillText(lastTs + ' 갱신', CW - 26, by + 18);
                if (track && track.requestFrame) { try { track.requestFrame(); } catch (e) {} }
            }
            async function open() {
                try { if (!lastMs) await fetchMetrics(); }
                catch (e) { bedToast('미니창 데이터 실패: ' + e.message); return; }
                if (window.documentPictureInPicture) {
                    try {
                        var _k = (parseInt(STYLE.fontSize) || 15) / 15;
                        docWin = await window.documentPictureInPicture.requestWindow(
                            { width: Math.round(360 * _k),
                              height: Math.round((64 * lastMs.length + 96) * _k) });
                        docWin.__exBaseW = Math.round(360 * _k);
                        docWin.__exBaseH = docWin.innerHeight || 300;
                        applyDocCss();
                        docWin.addEventListener('pagehide', function () { docWin = null; });
                        renderDoc();
                        docWin.document.addEventListener('click', function (ev) {
                            var id = ev.target && ev.target.id;
                            if (id === 'lmRef') { try { refreshPage(); } catch (e) {} }
                            else if (id === 'lmCls') { try { close(); } catch (e) {} }
                            else if (id === 'lmIv') { try { cycleMainInterval(); renderDoc(); } catch (e) {} }
                            else { try { cyclePreset(1); } catch (e) {} }
                        });
                        docWin.setInterval(function () {
                            try {
                                tick(docWin.document);
                                if (nextRefreshTime && Date.now() >= nextRefreshTime
                                    && !isRefreshing) refreshPage();
                            } catch (e) {}
                        }, 1000);
                        var _fitZoom = function () {
                            try {
                                var z = Math.max(0.4, Math.min(
                                    docWin.innerWidth / (docWin.__exBaseW || 360),
                                    docWin.innerHeight / (docWin.__exBaseH || docWin.innerHeight || 300)));
                                docWin.document.body.style.zoom = z;
                            } catch (e) {}
                        };
                        _fitZoom();
                        docWin.addEventListener('resize', _fitZoom);
                        return;
                    } catch (e) { docWin = null; }
                }
                try {
                    canvas = document.createElement('canvas');
                    drawCanvas();
                    var stream = canvas.captureStream(0);
                    track = stream.getVideoTracks()[0];
                    video = document.createElement('video');
                    video.muted = true; video.playsInline = true; video.srcObject = stream;
                    video.style.cssText = 'position:fixed;left:-9999px;top:0;width:2px;height:2px;';
                    document.body.appendChild(video);
                    await video.play();
                    drawCanvas();
                    if (track && track.requestFrame) { try { track.requestFrame(); } catch (e) {} }
                    await new Promise(function (res) {   // 첫 프레임 크기 확정 후 PiP 진입 (비율 왜곡 방지)
                        var t0 = Date.now();
                        (function chk() {
                            if (video.videoWidth >0 || Date.now() - t0 >800) res();
                            else setTimeout(chk, 40);
                        })();
                    });
                    await video.requestPictureInPicture();
                    video.addEventListener('leavepictureinpicture', close);
                    try {
                        if (navigator.mediaSession) {
                            navigator.mediaSession.playbackState = 'playing';
                            navigator.mediaSession.setActionHandler('play', function () {
                                try { video.play(); } catch (e) {}
                            });
                            navigator.mediaSession.setActionHandler('pause', function () {
                                try { video.play(); } catch (e) {}
                                _miniKick();                        //  = 즉시 갱신
                            });
                            navigator.mediaSession.setActionHandler('nexttrack', function () { cyclePreset(1); });          //  = 디자인
                            navigator.mediaSession.setActionHandler('previoustrack', function () { cycleMainInterval(); }); //  = 주기
                            if (window.MediaMetadata)
                                navigator.mediaSession.metadata = new MediaMetadata(
                                    { title: '병상 미니창', artist: '갱신 · 주기 · 디자인 · 크기=핀치' });
                        }
                    } catch (e) {}
                    video.addEventListener('pause', function () { try { video.play(); } catch (e) {} });
                    canvasTimer = setInterval(function () {
                        drawCanvas();
                        if (track && track.requestFrame) { try { track.requestFrame(); } catch (e) {} }
                    }, 1000);
                } catch (e) {
                    bedToast('미니창 미지원: ' + (e && e.message ? e.message : e));
                    close();
                }
            }
            function close() {
                try { if (canvasTimer) clearInterval(canvasTimer); } catch (e) {}
                canvasTimer = null;
                lockW = 0; lockH = 0;
                try { if (docWin) docWin.close(); } catch (e) {}
                docWin = null;
                try { if (document.pictureInPictureElement) document.exitPictureInPicture(); } catch (e) {}
                try { if (track) track.stop(); } catch (e) {}
                try { if (video) video.remove(); } catch (e) {}
                video = null; canvas = null; track = null;
            }
            function toggle() { if (docWin || video) close(); else open(); }
            async function onMainRefreshed() {
                if (!(docWin || canvas)) return;
                try { await fetchMetrics(); } catch (e) { return; }
                if (docWin) renderDoc();
                if (canvas) {
                    drawCanvas();
                    if (track && track.requestFrame) { try { track.requestFrame(); } catch (e) {} }
                }
            }
            function stylePanel() {
                var old = document.getElementById('miniStylePanel');
                if (old) { old.remove(); return; }
                var wrap = document.createElement('div');
                wrap.id = 'miniStylePanel';
                wrap.style.cssText = 'position:fixed;inset:0;z-index:9999;'
                    + 'background:rgba(0,0,0,0.45);display:flex;align-items:center;justify-content:center;';
                function row(lbl, key) {
                    var v = styleGet(key);
                    if (key === 'radius' || key === 'fontSize' || key === 'opacity') {
                        var mn = key === 'fontSize' ? 12 : 0;
                        var mx = key === 'radius' ? 24 : (key === 'fontSize' ? 72 : 100);
                        return '<label style="display:flex;gap:8px;align-items:center;'
                            + 'padding:6px 0;font-size:0.85rem;">'
                            + '<span style="width:74px;">' + lbl + '</span>'
                            + '<input type="range" data-k="' + key + '" min="' + mn + '" max="' + mx
                            + '" value="' + (parseInt(v) || mn) + '" style="flex:1;">'
                            + '<b data-v="' + key + '" style="width:34px;text-align:right;">'
                            + (parseInt(v) || mn) + '</b></label>';
                    }
                    var OPTS = {
                        color: [['#ffffff', '흰색'], ['#111111', '검정'], ['#00e676', '녹색'],
                                ['#ffee00', '노랑'], ['#4dc3ff', '하늘']],
                        weight: [['400', '보통'], ['700', '굵게'], ['800', '아주 굵게']],
                        border: [['none', '없음'], ['1px solid #555', '얇은 회색'],
                                 ['1px solid #000', '얇은 검정'], ['2px solid #ffffff', '굵은 흰색'],
                                 ['2px solid #000000', '굵은 검정']],
                        bg: [['rgba(0,0,0,1)', '검정'], ['rgba(255,255,255,1)', '흰색'],
                             ['rgba(10,25,60,1)', '남색'], ['rgba(0,40,25,1)', '짙은 녹색'],
                             ['rgba(60,60,60,1)', '회색']],
                        bgSolid: [['#000000', '검정'], ['#f2f2f2', '흰색'], ['#0a193c', '남색'],
                                  ['#002819', '짙은 녹색'], ['#3c3c3c', '회색']]
                    };
                    var cur = String(v), found = false;
                    var os = (OPTS[key] || []).map(function (o) {
                        var s2 = cur === o[0];
                        if (s2) found = true;
                        return '<option value="' + o[0] + '"' + (s2 ? ' selected' : '') + '>'
                             + o[1] + '</option>';
                    }).join('');
                    if (!found) os = '<option value="' + cur + '" selected>사용자값</option>' + os;
                    return '<label style="display:flex;gap:8px;align-items:center;'
                        + 'padding:6px 0;font-size:0.85rem;">'
                        + '<span style="width:74px;">' + lbl + '</span>'
                        + '<select data-k="' + key + '" style="flex:1;padding:5px;border:1px solid #ccc;'
                        + 'border-radius:8px;">' + os + '</select></label>';
                }
                wrap.innerHTML = '<div style="background:#fff;border-radius:14px;max-width:330px;'
                    + 'width:88vw;padding:16px;box-shadow:0 8px 30px rgba(0,0,0,0.3);">'
                    + '<div style="font-weight:700;margin-bottom:6px;">미니창 스타일</div>'
                    + '<div style="display:flex;gap:5px;margin:2px 0 8px;">'
                    + PRESETS.map(function (p, i) {
                        return '<button data-pi="' + i + '" style="flex:1;padding:6px 2px;'
                             + 'border:1px solid #999;border-radius:8px;background:' + p.s.bgSolid
                             + ';color:' + p.s.color + ';font-weight:700;font-size:0.72rem;">'
                             + p.name + '</button>';
                    }).join('') + '</div>'
                    + row('모서리(px)', 'radius') + row('테두리', 'border')
                    + row('배경', 'bg') + row('배경(영상)', 'bgSolid')
                    + row('글자색', 'color') + row('굵기', 'weight')
                    + row('글자크기(px)', 'fontSize') + row('투명도(0-100)', 'opacity')
                    + '<div style="display:flex;gap:8px;margin-top:10px;">'
                    + '<button id="lmsApply" style="flex:1;padding:8px;border:none;border-radius:10px;'
                    + 'background:#667eea;color:#fff;font-weight:700;">적용</button>'
                    + '<button id="lmsReset" style="flex:1;padding:8px;border:none;border-radius:10px;'
                    + 'background:#e0e0e0;font-weight:700;">기본값</button>'
                    + '<button id="lmsClose" style="padding:8px 12px;border:none;border-radius:10px;'
                    + 'background:#f5f5f5;">닫기</button></div></div>';
                document.body.appendChild(wrap);
                wrap.addEventListener('click', function (e) { if (e.target === wrap) wrap.remove(); });
                wrap.querySelectorAll('button[data-pi]').forEach(function (b) {
                    b.onclick = function () {
                        presetIdx = parseInt(b.getAttribute('data-pi'));
                        applyStyleObj(PRESETS[presetIdx].s, true);
                        wrap.querySelectorAll('input[data-k]').forEach(function (inp) {
                            inp.value = String(styleGet(inp.getAttribute('data-k')));
                        });
                    };
                });
                wrap.querySelector('#lmsClose').onclick = function () { wrap.remove(); };
                wrap.addEventListener('input', function (e) {
                    var k = e.target && e.target.getAttribute && e.target.getAttribute('data-k');
                    if (!k) return;
                    var v = e.target.value;
                    var o = {};
                    o[k] = (k === 'radius' || k === 'fontSize' || k === 'opacity') ? parseInt(v) : v;
                    applyStyleObj(o, true);   // 슬라이더/선택 즉시 반영·저장 (실시간 미리보기)
                    var bb = wrap.querySelector('b[data-v="' + k + '"]');
                    if (bb) bb.textContent = v;
                });
                function refreshMini() {
                    if (docWin) { applyDocCss(); renderDoc(); }
                    if (canvas) drawCanvas();
                }
                wrap.querySelector('#lmsApply').onclick = function () {
                    wrap.querySelectorAll('input[data-k]').forEach(function (inp) {
                        var k = inp.getAttribute('data-k'), v = inp.value;
                        STYLE[k] = (k === 'radius') ? (parseInt(v) || 0)
                            : (k === 'fontSize') ? (parseInt(v) || 15)
                            : (k === 'opacity') ? Math.max(0, Math.min(100, parseInt(v) || 0)) : v;
                    });
                    try { localStorage.setItem('exMiniStyle', JSON.stringify(STYLE)); } catch (e) {}
                    refreshMini(); wrap.remove();
                };
                wrap.querySelector('#lmsReset').onclick = function () {
                    Object.keys(DEF).forEach(function (k) { STYLE[k] = DEF[k]; });
                    try { localStorage.removeItem('exMiniStyle'); } catch (e) {}
                    refreshMini(); wrap.remove();
                };
            }
            return { toggle: toggle, close: close,
                     onMainRefreshed: onMainRefreshed, stylePanel: stylePanel };
        })();

        // ──  표시 항목 · 순서 설정 (py/저장본 공용, localStorage 영구 기억) ──
        var EXSEC = (function () {
            var CATS = ['응급실', '중환자실', '격리진료구역', '입원실', '기타', '의료장비',
                        '중증질환 수용가능', '예외상황'];
            var MINSET = { '응급실': 1, '중환자실': 1, '입원실': 1, '예외상황': 1 };
            function load() {
                var c = null;
                try { c = JSON.parse(localStorage.getItem('exSections') || 'null'); } catch (e) {}
                if (!c || !c.order || !c.order.length) c = { order: CATS.slice(), hidden: {} };
                if (!c.hidden) c.hidden = {};
                CATS.forEach(function (nm) { if (c.order.indexOf(nm) === -1) c.order.push(nm); });
                c.order = c.order.filter(function (nm) { return CATS.indexOf(nm) !== -1; });
                return c;
            }
            function save(c) { try { localStorage.setItem('exSections', JSON.stringify(c)); } catch (e) {} }
            function groups(tb) {
                var out = [], cur = null;
                Array.prototype.forEach.call(tb.rows, function (tr) {
                    var td = tr.querySelector('td.category-header');
                    if (td) {
                        var nm = td.textContent.trim();
                        var name = CATS.filter(function (c2) { return nm.indexOf(c2) === 0; })[0] || nm;
                        cur = { name: name, rows: [tr] };
                        out.push(cur);
                    } else if (cur) {
                        cur.rows.push(tr);
                    }
                });
                return out;
            }
            function apply() {
                var tb = document.querySelector('.comparison-table tbody');
                if (!tb) return;
                var c = load(), by = {};
                groups(tb).forEach(function (g) { by[g.name] = g; });
                c.order.forEach(function (nm) {
                    var g = by[nm];
                    if (!g) return;
                    g.rows.forEach(function (r) {
                        tb.appendChild(r);
                        r.style.display = c.hidden[nm] ? 'none' : '';
                    });
                    delete by[nm];
                });
                Object.keys(by).forEach(function (nm) {
                    by[nm].rows.forEach(function (r) { tb.appendChild(r); });
                });
            }
            function panel() {
                var old = document.getElementById('secPanel');
                if (old) { old.remove(); return; }
                var c = load();
                var wrap = document.createElement('div');
                wrap.id = 'secPanel';
                wrap.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.45);'
                    + 'display:flex;align-items:center;justify-content:center;';
                function build() {
                    var rows = c.order.map(function (nm, i) {
                        return '<div style="display:flex;align-items:center;gap:8px;padding:6px 4px;'
                             + 'border-bottom:1px solid #eee;font-size:0.9rem;">'
                             + '<input type="checkbox" data-sc="' + i + '"'
                             + (c.hidden[nm] ? '' : ' checked') + '>'
                             + '<span style="flex:1;">' + nm + '</span>'
                             + '<button data-up="' + i + '" style="border:none;background:#eee;'
                             + 'border-radius:6px;padding:3px 9px;">▲</button>'
                             + '<button data-dn="' + i + '" style="border:none;background:#eee;'
                             + 'border-radius:6px;padding:3px 9px;">▼</button></div>';
                    }).join('');
                    wrap.innerHTML = '<div style="background:#fff;border-radius:14px;max-width:340px;'
                        + 'width:88vw;padding:16px;box-shadow:0 8px 30px rgba(0,0,0,0.3);">'
                        + '<div style="font-weight:700;margin-bottom:6px;">표시 항목 · 순서</div>'
                        + '<div style="max-height:52vh;overflow:auto;">' + rows + '</div>'
                        + '<div style="display:flex;gap:8px;margin-top:10px;">'
                        + '<button id="secMin" style="flex:1;padding:8px;border:none;border-radius:10px;'
                        + 'background:#667eea;color:#fff;font-weight:700;">최소</button>'
                        + '<button id="secAll" style="flex:1;padding:8px;border:none;border-radius:10px;'
                        + 'background:#e0e0e0;font-weight:700;">전체</button>'
                        + '<button id="secClose" style="padding:8px 12px;border:none;border-radius:10px;'
                        + 'background:#f5f5f5;">닫기</button></div></div>';
                }
                build();
                document.body.appendChild(wrap);
                wrap.addEventListener('click', function (e) {
                    var t = e.target;
                    if (t === wrap || t.id === 'secClose') { wrap.remove(); return; }
                    if (t.id === 'secMin') {
                        c.hidden = {};
                        c.order.forEach(function (nm) { if (!MINSET[nm]) c.hidden[nm] = true; });
                        save(c); apply(); build(); return;
                    }
                    if (t.id === 'secAll') { c.hidden = {}; save(c); apply(); build(); return; }
                    var up = t.getAttribute ? t.getAttribute('data-up') : null;
                    var dn = t.getAttribute ? t.getAttribute('data-dn') : null;
                    if (up !== null) {
                        var i = parseInt(up);
                        if (i >0) { var x = c.order[i]; c.order[i] = c.order[i - 1]; c.order[i - 1] = x; }
                        save(c); apply(); build(); return;
                    }
                    if (dn !== null) {
                        var j = parseInt(dn);
                        if (j < c.order.length - 1) {
                            var y = c.order[j]; c.order[j] = c.order[j + 1]; c.order[j + 1] = y;
                        }
                        save(c); apply(); build(); return;
                    }
                });
                wrap.addEventListener('change', function (e) {
                    var sc = e.target && e.target.getAttribute ? e.target.getAttribute('data-sc') : null;
                    if (sc === null) return;
                    var nm = c.order[parseInt(sc)];
                    if (e.target.checked) delete c.hidden[nm]; else c.hidden[nm] = true;
                    save(c); apply();
                });
            }
            try { var b = document.getElementById('secBtn'); if (b) b.onclick = panel; } catch (e) {}
            return { apply: apply, panel: panel };
        })();
        try { EXSEC.apply(); } catch (e) {}

        // ── 서버 사망 폴백: 내장 엔진으로 브라우저가 직접 조회 ──
        let fallbackMode = false;
        window.__lastHd = null;
        async function fallbackRefresh() {
            if (typeof EX === 'undefined') throw new Error('엔진 없음');
            const r = await EX.loadAll();
            if (!r.hd.length) throw new Error((r.errors || []).join(' / ') || '데이터 없음');
            window.__lastHd = r.hd;
            const w = document.querySelector('.comparison-wrapper');
            if (w) w.outerHTML = EX.renderComparison(r.hd, false);
            const now = new Date();
            const qt = document.getElementById('queryTime');
            if (qt) qt.textContent = ('0' + now.getHours()).slice(-2) + ':'
                + ('0' + now.getMinutes()).slice(-2) + ':' + ('0' + now.getSeconds()).slice(-2);
            try { EXSEC.apply(); } catch (e) {}
            try { if (typeof fitBedTexts === 'function') { fitBedTexts(); setTimeout(fitBedTexts, 300); } } catch (e) {}
            try { LIVE_MINI.onMainRefreshed(); } catch (e) {}
            try {
                const bar = document.getElementById('reconBar');
                if (bar) bar.textContent = ' 서버 끊김 — 직접조회 모드로 갱신 중 (Pydroid 재실행 시 자동 복귀)';
            } catch (e) {}
        }

        async function refreshPage() {
            if (isRefreshing) return;
            if (fallbackMode) {
                isRefreshing = true;
                try { await fallbackRefresh(); }
                catch (e) {
                    // 실패를 조용히 삼키면 '작동 안 함' 으로만 보인다. 배너에 사유 표시.
                    console.error('직접조회 실패:', e);
                    try {
                        const bar = document.getElementById('reconBar');
                        if (bar) {
                            bar.style.display = 'block';
                            bar.textContent = ' 직접조회 실패 — ' + ((e && e.message) || e);
                        }
                    } catch (e2) {}
                }
                isRefreshing = false;
                startAutoRefresh();
                return;
            }
            isRefreshing = true;
            clearTimers(); // 진행 중 카운트다운 즉시 정지
            const bar = document.getElementById('globalRefreshBar');
            const txt = document.getElementById('globalRefreshText');
            if (txt) txt.textContent = '갱신 중...';
            if (bar) { bar.className = 'bar'; bar.style.background = 'linear-gradient(to bottom,#7EB481,#4F7A52)'; bar.style.width = '0%'; }
            try {
                // 캐시 우회: 타임스탬프 파라미터 추가
                const sep = location.href.includes('?') ? '&' : '?';
                const url = location.href + sep + '_t=' + Date.now();
                const resp = await fetch(url, { cache: 'no-cache' });
                if (!resp.ok) throw new Error(`서버 오류 HTTP ${resp.status}`);
                const html = await resp.text();
                const parser = new DOMParser();
                const doc = parser.parseFromString(html, 'text/html');
                // 테이블 콘텐츠 교체
                const newWrapper = doc.querySelector('.comparison-wrapper');
                const oldWrapper = document.querySelector('.comparison-wrapper');
                if (!newWrapper || !oldWrapper) {
                    throw new Error('응답 페이지에서 테이블을 찾을 수 없습니다 (서버 오류 가능)');
                }
                oldWrapper.innerHTML = newWrapper.innerHTML;
                // 조회시각 업데이트
                const newTime = doc.querySelector('#queryTime');
                const oldTime = document.querySelector('#queryTime');
                if (newTime && oldTime) oldTime.textContent = newTime.textContent;
                // 갱신 완료 햅틱+알림음
                _hapticBeep();
                // ── 동기화: 브라우저 갱신 완료 → PiP에 알림 + Kivy 햅틱 요청
                try {
                    // 브라우저 갱신 완료 ts를 _syncLastTs에 즉시 반영
                    // → checkPipSync가 자기 신호를 PiP 신호로 오인하는 무한루프 방지
                    fetch('/api/notify_refresh', {method:'POST',
                        headers:{'Content-Type':'application/json'},
                        body:JSON.stringify({h: new URLSearchParams(location.search).get('h')||''})
                    })
                        .then(function(r){return r.json();})
                        .then(function(d){if(d.ts) _syncLastTs = d.ts;})
                        .catch(function(){});
                    fetch('/api/haptic', {method:'POST'}).catch(()=>{});
                } catch(e){}
                // 폰트 재조절
                setTimeout(fitBedTexts, 100);
                try { EXSEC.apply(); } catch (e) {}
                try { LIVE_MINI.onMainRefreshed(); } catch (e) {}
            } catch(err) {
                console.error('갱신 실패:', err);
                if (String((err && err.message) || err).match(/fetch|network/i)) {
                    fallbackMode = true;
                    startReconnect(function () { fallbackMode = false; refreshPage(); });
                    try { await fallbackRefresh(); }
                    catch (fe) { console.error('직접조회 전환 실패:', fe); }
                }
                if (txt) txt.textContent = ' 갱신 실패: ' + err.message;
                if (bar) { bar.className = 'bar bar-red'; bar.style.width = '100%'; }
                // 오류 메시지를 3초간 표시한 후 타이머 재시작
                await new Promise(r =>setTimeout(r, 3000));
            } finally {
                isRefreshing = false;
                startAutoRefresh();
            }
        }

        // 타이머 완전 시작 (interval 변경 또는 최초 실행 시)
        function startAutoRefresh() {
            clearTimers();
            const _sel = document.getElementById('refreshInterval');
            if (_sel.value === 'custom') {
                const _s = parseInt(prompt('갱신 주기(초, 최소 20초)', '90'));
                const _ms = (_s && _s >= 20) ? _s * 1000 : 180000;
                const _o = _sel.options[_sel.selectedIndex];
                _o.value = String(_ms);
                _o.textContent = '직접(' + Math.round(_ms / 1000) + '초)';
                const _c = document.createElement('option');
                _c.value = 'custom'; _c.textContent = '직접입력';
                _sel.appendChild(_c);
            }
            currentInterval = parseInt(_sel.value);
            if (currentInterval >0) {
                nextRefreshTime = Date.now() + currentInterval;
                // sessionStorage에 저장 (이슈2: 창 전환 후에도 유지)
                try { sessionStorage.setItem('nrt', String(nextRefreshTime)); } catch(e){}
                scheduleRefresh(currentInterval);
                updateProgressBar();
                countdownTimer = setInterval(updateProgressBar, 1000);
            } else {
                nextRefreshTime = null;
                try { sessionStorage.removeItem('nrt'); } catch(e){}
                updateProgressBar();
            }
        }

        // setTimeout으로 갱신 예약 (남은 시간 기준)
        function scheduleRefresh(delay) {
            if (refreshTimer !== null) { clearTimeout(refreshTimer); refreshTimer = null; }
            refreshTimer = setTimeout(refreshPage, Math.max(delay, 0));
        }

        // 타이머 전체 정리
        function clearTimers() {
            if (refreshTimer  !== null) { clearTimeout(refreshTimer);   refreshTimer = null; }
            if (countdownTimer !== null) { clearInterval(countdownTimer); countdownTimer = null; }
        }

        function updateProgressBar() {
            const bar = document.getElementById('globalRefreshBar');
            const txt = document.getElementById('globalRefreshText');
            if (!bar || !txt) return;
            if (!nextRefreshTime || currentInterval === 0) {
                bar.style.width = '0%';
                bar.style.background = '#9e9e9e';
                txt.textContent = '수동';
                return;
            }
            const remaining = Math.max(0, Math.floor((nextRefreshTime - Date.now()) / 1000));
            const pct = Math.max(0, (remaining / (currentInterval / 1000)) * 100);
            bar.style.width = pct + '%';
            // 남은 비율에 따라 그라데이션 색상 전환
            let grad;
            if (pct >60) {
                grad = 'linear-gradient(to bottom,#80B382,#507A52)';
            } else if (pct >30) {
                grad = 'linear-gradient(to bottom,#F8BB47,#E0952A)';
            } else {
                grad = 'linear-gradient(to bottom,#E36460,#C53533)';
            }
            bar.style.background = grad;
            txt.textContent = remaining === 0
                ? '갱신 중...'
                : `${Math.floor(remaining/60)}:${String(remaining%60).padStart(2,'0')}`;
        }

        // ── 가시성/포커스 변경 처리 (이슈2: 백그라운드 진행) ─────────
        function onPageVisible() {
            if (currentInterval <= 0) return;
            // sessionStorage에서 nextRefreshTime 복원
            try {
                const saved = sessionStorage.getItem('nrt');
                if (saved) {
                    const savedTime = parseInt(saved);
                    if (!isNaN(savedTime) && savedTime >nextRefreshTime) {
                        nextRefreshTime = savedTime;
                    }
                }
            } catch(e){}

            if (nextRefreshTime) {
                const remaining = nextRefreshTime - Date.now();
                if (remaining <= 0) {
                    refreshPage();
                } else {
                    scheduleRefresh(remaining);
                    if (!countdownTimer) {
                        countdownTimer = setInterval(updateProgressBar, 1000);
                    }
                    updateProgressBar();
                }
            }
        }

        function onPageHidden() {
            // 자원 절약을 위해 타이머 중지 (nextRefreshTime은 유지)
            clearTimers();
            try {
                if (nextRefreshTime) sessionStorage.setItem('nrt', String(nextRefreshTime));
            } catch(e){}
        }

        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) onPageVisible();
            else onPageHidden();
        });
        // Android WebView에서 visibilitychange가 불안정한 경우 대비 (이슈2)
        window.addEventListener('focus', onPageVisible);
        window.addEventListener('blur',  onPageHidden);

        document.getElementById('refreshInterval').addEventListener('change', startAutoRefresh);
        document.getElementById('refreshNow').addEventListener('click', refreshPage);

        // ── 백그라운드(PiP) 버튼 ───────────────────────────────────
        // [구조] 브라우저에서 직접 Android PiP API 호출 불가.
        // 대신 Flask POST → _pip_state 공유 → Kivy Clock 감지 →
        // enterPictureInPictureMode() 호출 순서로 동작.
        // [buildozer.spec 필수] android.add_activity_args 에
        //   android:supportsPictureInPicture="true" 추가 필요
        //  [로그 2026-H2] 비교화면 이벤트를 통합 로그(ermon.log)로 전송
        function pipLog(m) {
            try { console.log('[PiP] ' + m); } catch (e) {}
            try {
                fetch('/api/ns_dbg?m=' + encodeURIComponent('[비교화면] ' + m),
                      { cache: 'no-store' });
            } catch (e) {}
        }
        (function() {
            document.getElementById('pipBtn').addEventListener('click', function() {
                var hParam = new URLSearchParams(location.search).get('h') || '';
                pipLog('[백그라운드] 버튼 탭');
                var ivMs   = document.getElementById('refreshInterval').value || '180000';
                var ivSec  = Math.round(parseInt(ivMs) / 1000);
                var btn    = document.getElementById('pipBtn');
                btn.disabled = true;
                btn.textContent = ' PiP 전환 중...';
                fetch('/api/enter_pip', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({h: hParam, iv: ivSec})
                })
                .then(function(r) { return r.json(); })
                .then(function(d) {
                    if (!d.ok) {
                        btn.textContent = ' 백그라운드';
                        btn.disabled = false;
                        return;
                    }
                    btn.textContent = ' PiP 요청됨';
                    /* [FIX 2026-H2] 기존에는 요청만 던지고 결과를 알 수 없어
                       실패해도 '무반응'으로만 보였다. 실제 진입 결과를 폴링해
                       사유를 즉시 표시하고 통합 로그에도 남긴다. */
                    var tries = 0;
                    var poll = setInterval(function () {
                        tries += 1;
                        fetch('/api/pip_status', { cache: 'no-store' })
                        .then(function (r2) { return r2.json(); })
                        .then(function (st) {
                            if (st.ok) {
                                clearInterval(poll);
                                btn.textContent = ' 백그라운드';
                                btn.disabled = false;
                                return;
                            }
                            if (st.reason && st.age < 30) {
                                clearInterval(poll);
                                btn.textContent = 'PiP 실패';
                                btn.title = st.reason;
                                btn.disabled = false;
                                pipLog('PiP 실패 stage=' + st.stage + ' reason=' + st.reason
                                       + ' api=' + st.api + ' 기기지원=' + st.device_feature
                                       + ' 매니페스트=' + st.manifest_flag);
                                alert('PiP 진입 실패\\n\\n[' + st.stage + ']\\n' + st.reason
                                      + '\\n\\n매니페스트 선언: ' + st.manifest_flag
                                      + '\\n기기 지원: ' + st.device_feature
                                      + '\\n\\n상세 로그: /diag');
                                return;
                            }
                            if (tries >= 8) {
                                clearInterval(poll);
                                btn.textContent = ' 백그라운드';
                                btn.disabled = false;
                                pipLog('PiP 결과 미확인 (타임아웃) stage=' + st.stage);
                            }
                        })
                        .catch(function () {
                            /* PiP 진입 성공 시 브라우저가 백그라운드로 밀려
                               fetch 가 끊기는 것이 정상 동작이다. */
                            clearInterval(poll);
                            btn.textContent = ' 백그라운드';
                            btn.disabled = false;
                        });
                    }, 700);
                })
                .catch(function(e) {
                    pipLog('요청 실패: ' + e);
                    btn.textContent = ' 백그라운드';
                    btn.disabled = false;
                });
            });
        })();

        // ── 병상 텍스트 폰트 자동 조절 (이슈3) ──────────────────────
        function fitBedTexts() {
            document.querySelectorAll('.bed-text-overlay').forEach(overlay => {
                if (overlay.id === 'globalRefreshOverlay') return;
                const container = overlay.closest('.bar-container');
                if (!container) return;

                // 폰트 사이즈 리셋
                overlay.style.fontSize = '';

                const containerW = container.clientWidth;
                if (!containerW) return;

                // 임시 hidden span으로 실제 텍스트 너비 측정
                const span = document.createElement('span');
                const curFontSize = parseFloat(window.getComputedStyle(overlay).fontSize);
                span.style.cssText = [
                    'position:fixed',
                    'visibility:hidden',
                    'white-space:nowrap',
                    `font-size:${curFontSize}px`,
                    'font-weight:700',
                    'pointer-events:none',
                    'top:-9999px'
                ].join(';');
                span.textContent = overlay.textContent;
                document.body.appendChild(span);
                const textW = span.getBoundingClientRect().width;
                document.body.removeChild(span);

                if (textW >containerW - 2) {
                    const ratio = (containerW - 2) / textW;
                    const newSz = Math.max(curFontSize * ratio * 0.97, 5);
                    overlay.style.fontSize = newSz + 'px';
                }
            });

            // 데이터 셀 텍스트 자동 조절 (병원 수에 따른 셀 너비 대응)
            const numCols = document.querySelectorAll('.comparison-table thead th').length - 1;
            if (numCols >= 2) {
                const tableW = document.querySelector('.comparison-table')?.clientWidth || 0;
                const firstColW = document.querySelector('.comparison-table thead th')?.clientWidth || 0;
                const cellW = tableW >0 ? (tableW - firstColW) / numCols : 0;
                if (cellW >0) {
                    document.querySelectorAll('.comparison-table td:not(.item-label):not(.category-header)').forEach(td => {
                        const inner = td.querySelector('.bed-numbers, .equipment-cell, .bed-cell');
                        const el = inner || td;
                        el.style.fontSize = '';
                        const curSz = parseFloat(window.getComputedStyle(el).fontSize);
                        if (el.scrollWidth >cellW + 4) {
                            const ratio = cellW / el.scrollWidth;
                            el.style.fontSize = Math.max(curSz * ratio * 0.95, 6) + 'px';
                        }
                    });
                }
            }
        }

        document.addEventListener('DOMContentLoaded', () => {
            // sessionStorage에 저장된 nextRefreshTime이 있으면 복원 (이슈2)
            try {
                const saved = sessionStorage.getItem('nrt');
                if (saved) {
                    const savedTime = parseInt(saved);
                    const now = Date.now();
                    if (!isNaN(savedTime) && savedTime >now) {
                        // 저장된 시간으로 타이머 복원
                        currentInterval = parseInt(document.getElementById('refreshInterval').value);
                        nextRefreshTime = savedTime;
                        const remaining = savedTime - now;
                        scheduleRefresh(remaining);
                        updateProgressBar();
                        countdownTimer = setInterval(updateProgressBar, 1000);
                        fitBedTexts();
                        setTimeout(fitBedTexts, 300);
                        return;
                    }
                }
            } catch(e){}
            startAutoRefresh();
            fitBedTexts();
            setTimeout(fitBedTexts, 300);
        });
        window.addEventListener('resize', fitBedTexts);

        // ── PiP 동기화: PiP 갱신 시 상황판도 자동 갱신 ──────────────
        var _syncLastTs = 0;
        function checkPipSync() {
            fetch('/api/notify_refresh').then(function(r){return r.json();})
            .then(function(d){
                if (d.ts && d.ts > _syncLastTs + 1) {
                    _syncLastTs = d.ts;
                    if (!isRefreshing && currentInterval >0) {
                        // PiP 쪽에서 갱신됨 → 상황판도 갱신
                        refreshPage();
                    }
                }
            }).catch(function(){});
        }
        setInterval(checkPipSync, 15000);  // 15초마다 동기화 확인
    </script>
</body>
</html>
'''

@flask_app.route('/autostart')
def autostart():
    """앱 시작 시 브라우저가 여는 진입점.
    항상 병원 선택 화면(/)으로 이동. PiP 상태복원은 Kivy 사이드에서 처리.
    (뒤로가기로 compare 화면 재진입 가능)
    """
    _log('[autostart] → / (병원 선택 화면)')
    return redirect('/')


def _inject_live_engine(page_html):
    """선택화면에 직접조회 폴백 엔진(EX)을 상시 탑재.
    서버가 죽어도 브라우저가 공공API를 직접 호출해 계속 동작한다."""
    try:
        if '/*EX-ENGINE-START*/' in page_html:
            return page_html
        return page_html.replace('</body>', _live_engine_script([]) + '</body>', 1)
    except Exception as _ie:
        _log(f'[live] 폴백 엔진 주입 실패 (무시): {_ie}', 'ERROR')
        return page_html


@flask_app.route('/')
def index():
    return _inject_live_engine(_render_cached(HTML, districts=DISTRICTS))

# ── 지역 병원목록 서버 캐시 (30분) ─────────────────────────────
_REGION_CACHE = {}
_REGION_TTL   = 30 * 60
_REGION_LOCK  = _threading.Lock()


# ══════════════════════════════════════════════════════════════════
#  전국 로스터 / 병상 / 상세  (v2)
#   [원인분석] Q0=광주광역시, Q0=전라남도 가 두 API 모두에서 0건을 반환하는
#   현상이 로그에서 재현됨(항상 동일). 원인이 파라미터 표기든 API 측 결함이든
#   무관하게 복구되도록 (a) 시도 별칭 재시도 (b) 전국 페이징 합집합 (c) 오류
#   봉투 명시적 검출 을 적용한다.
# ══════════════════════════════════════════════════════════════════
_SIDO_ALIAS = {
    '서울특별시': ['서울'], '부산광역시': ['부산'],
    '대구광역시': ['대구'], '인천광역시': ['인천'],
    '대전광역시': ['대전'],
    '울산광역시': ['울산'], '세종특별자치시': ['세종', '세종시'],
    '경기도': ['경기'], '강원특별자치도': ['강원', '강원도'],
    '충청북도': ['충북'], '충청남도': ['충남'],
    '전북특별자치도': ['전북', '전라북도'],
    #  통합시: API 는 아직 구 표기('광주'·'전남')로만 결과를 준다.
    #  두 축 결과가 서로 달라(73건 / 72건) 하나만 쓰면 누락되므로 합집합.
    '전남광주통합특별시': ['전남광주시', '전남광주', '광주특별시',
                          '광주', '전남', '광주광역시', '전라남도'],
    '경상북도': ['경북'], '경상남도': ['경남'],
    '제주특별자치도': ['제주', '제주도'],
    #  ↓ 폐지된 시/도. 저장된 구 설정(h 파라미터)·구 주소 호환용으로만 남긴다.
    '광주광역시': ['광주', '전남광주통합특별시', '전남광주', '전남'],
    '전라남도': ['전남', '전남광주통합특별시', '전남광주', '광주'],
}

#  폐지된 시/도 표기 → 현행 시/도 (주소 해석 시 자동 승격)
_SIDO_LEGACY = {'광주광역시': SIDO_MERGED, '전라남도': SIDO_MERGED}

#  별칭마다 결과 집합이 다른 시/도 → 첫 성공에서 멈추지 않고 합집합
_UNION_SIDO = {SIDO_MERGED}

_ER_TAGS   = [('hvec', 'HVS01')]
_WARD_TAGS = [('hvgc', 'HVS38'), ('hv36', 'HVS19'), ('hv37', 'HVS20'), ('hv41', 'HVS25')]
_ICU_TAGS  = [('hvicc', 'HVS17'), ('hv2', 'HVS06'), ('hv3', 'HVS07'), ('hvncc', 'HVS08'),
              ('hv32', 'HVS09'), ('hvcc', 'HVS11'), ('hv6', 'HVS12'), ('hv34', 'HVS15'),
              ('hvccc', 'HVS16'), ('hv35', 'HVS18'), ('hv31', 'HVS05'), ('hv33', 'HVS10')]

# ── BACI v2 계수 ────────────────────────────────────────────────
#  a : 응급실→입원 전환율(기관 종별)
#  WI_W/WI_I : boarding-hour share 기반 하류 가중 (0.85*6h : 0.15*12h)
#  THETA : 고점유 볼록 변환 지수 (85% 룰 반영)
#  C_S : 실시간 수용불가 신고 가중
#  ※ 아래는 모두 "설계 상수(design constants)"이며 실측 데이터로
#     calibration/validation 된 값이 아니다. 문헌에서 검증된 계수가 아님.
_ADM_RATE = {'권역': 0.35, '센터': 0.28, '기관': 0.20}   # 응급실→입원 전환율 가정
_WI_W, _WI_I = 0.74, 0.26                                # 하류 가중 (설계 산정)
_THETA = 2.0                                             # 고점유 볼록 변환 지수
_BACI_CAP = 4.0


def _api_root(resp, ctx=''):
    """공통 응답 파서. data.go.kr 오류 봉투를 무음 통과시키지 않는다."""
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    rc = root.findtext('.//resultCode')
    if rc is None:
        auth = (root.findtext('.//returnAuthMsg') or root.findtext('.//errMsg')
                or root.findtext('.//returnReasonCode'))
        if auth:
            raise RuntimeError('API 오류봉투(%s): %s' % (ctx, auth))
    elif rc not in ('', '00'):
        raise RuntimeError('%s resultCode=%s %s' % (ctx, rc, root.findtext('.//resultMsg', '')))
    return root


def _api_items(resp, ctx=''):
    """[메모리 상한] 응답을 iterparse 로 흘려보내며 <item>단위로만 넘긴다.

    기존 ET.fromstring 은 응답 전체(항목 400개 x 태그 60개 ≈ 24,000 Element)를
    메모리에 상주시켰고, 이것이 동시 조회 시 Android 프로세스 강제종료
    (ERR_CONNECTION_REFUSED)의 직접 원인이었다. 스트리밍으로 바꾸면
    상주량이 <item>1개 수준으로 고정된다.

    yield: (item_element, totalCount_or_-1)
    소비자는 item 을 즉시 사용해야 하며(다음 반복에서 해제됨) 보관하면 안 된다.
    """
    resp.raise_for_status()
    data = resp.content
    total = [-1]
    rc = [None]
    try:
        ctxp = ET.iterparse(_BytesIO(data), events=('start', 'end'))
        _ev, root = next(ctxp)
        for ev, el in ctxp:
            if ev != 'end':
                continue
            tag = el.tag.rsplit('}', 1)[-1]
            if tag == 'item':
                yield el, total[0]
                el.clear()
                root.clear()
                continue
            if tag == 'resultCode' and rc[0] is None:
                rc[0] = (el.text or '').strip()
                if rc[0] not in ('', '00'):
                    raise RuntimeError('%s resultCode=%s' % (ctx, rc[0]))
            elif tag in ('returnAuthMsg', 'errMsg') and rc[0] is None:
                raise RuntimeError('API 오류봉투(%s): %s' % (ctx, (el.text or '').strip()))
            elif tag == 'totalCount':
                try:
                    total[0] = int((el.text or '-1').strip())
                except ValueError:
                    total[0] = -1
    finally:
        del data


def _list_row(it, sido_hint=''):
    """단일 <item> → 로스터 레코드 (없으면 None)."""
    hpid = (it.findtext('hpid') or '').strip()
    if not hpid:
        return None
    name  = (it.findtext('dutyName') or '').strip() or '알 수 없음'
    addr  = (it.findtext('dutyAddr') or '').strip()
    emcls = (it.findtext('dutyEmcls') or '').strip()
    sido  = _sido_of(addr) or sido_hint
    return {
        'hpid': hpid, 'name': name, 'dutyAddr': addr,
        'dutyTel1': (it.findtext('dutyTel1') or '').strip(),
        'dutyTel3': (it.findtext('dutyTel3') or '').strip(),
        'emcls': emcls,
        'emclsName': (it.findtext('dutyEmclsName') or '').strip(),
        'level': _get_hospital_level(emcls, name),
        'sido': sido, 'gugun': _split_gugun(sido, addr),
    }


def _list_rows_stream(resp, sido_hint='', ctx=''):
    out, total = [], -1
    for it, tc in _api_items(resp, ctx):
        if tc >= 0:
            total = tc
        r = _list_row(it, sido_hint)
        if r:
            out.append(r)
    return out, total


def _list_rows(root, sido_hint=''):
    out = []
    for it in root.findall('.//item'):
        hpid = (it.findtext('hpid') or '').strip()
        if not hpid:
            continue
        name  = (it.findtext('dutyName') or '').strip() or '알 수 없음'
        addr  = (it.findtext('dutyAddr') or '').strip()
        emcls = (it.findtext('dutyEmcls') or '').strip()
        sido  = _sido_of(addr) or sido_hint
        out.append({
            'hpid': hpid, 'name': name, 'dutyAddr': addr,
            'dutyTel1': (it.findtext('dutyTel1') or '').strip(),
            'dutyTel3': (it.findtext('dutyTel3') or '').strip(),
            'emcls': emcls,
            'emclsName': (it.findtext('dutyEmclsName') or '').strip(),
            'level': _get_hospital_level(emcls, name),
            'sido': sido, 'gugun': _split_gugun(sido, addr),
        })
    return out


_BytesIO = __import__('io').BytesIO

_SIDO_EXACT = None


def _sido_reset_lookup():
    """DISTRICTS 가 갱신(자동학습)되면 캐시를 무효화한다."""
    global _SIDO_EXACT
    _SIDO_EXACT = None


def _sido_of(addr):
    """주소 첫 토큰으로 시/도 판별.

    [ROOT-FIX 2026-H1] 이전에는 startswith() 접두 매칭이라
      '전남광주통합특별시 동구' 가 별칭 '전남'(2자)에 걸려
      '전라남도' 로 잘못 분류됐다 → 통합시 병원 전량이 광주에서 소멸.
    이제 '토큰 정확일치' 를 1순위로 하고, 접두 폴백은 3자 이상
    정식 표기에만 허용한다(2자 약칭 오탐 차단)."""
    global _SIDO_EXACT
    if _SIDO_EXACT is None:
        m = {}
        for k in DISTRICTS:
            m[k] = k
        for k in DISTRICTS:
            for a in _SIDO_ALIAS.get(k, []):
                m.setdefault(a, k)
        for ok_, nk in _SIDO_LEGACY.items():          # 폐지 표기 → 현행
            if nk in DISTRICTS:
                m[ok_] = nk
        _SIDO_EXACT = m
    head = (addr or '').strip()
    if not head:
        return ''
    tok = head.split()[0]
    hit = _SIDO_EXACT.get(tok)
    if hit:
        return hit
    for pre in sorted((k for k in _SIDO_EXACT if len(k) >= 3),
                      key=len, reverse=True):
        if tok.startswith(pre):
            return _SIDO_EXACT[pre]
    return ''


def _addr_head(addr):
    """주소의 첫 토큰(시/도 자리). 자동학습용."""
    t = (addr or '').strip().split()
    return t[0] if t else ''


# ══════════════════════════════════════════════════════════════════
#  [ROOT-FIX 2026-D1] 시/도 파라미터(Q0 / STAGE1) 공용 폴백 계층
#
#  근본원인:
#    data.go.kr 의 Q0/STAGE1 필터는 일부 시/도(광주광역시·전라남도 등)에서
#    0건 또는 오류봉투를 반환한다. 기존 코드는 (a) 별칭 재시도를 일부
#    함수에만 넣었고 (b) 그 재시도 루프가 try/except 로 감싸여 있지 않아
#    첫 후보에서 예외가 나면 나머지 폴백(별칭·구단위)에 도달조차 못했다.
#    → 광주/전남에서 로스터 0건 → 포화도 0개 · compare "병원 없음" 이 발생.
#
#  대책: 모든 시/도 기반 API 호출을 아래 두 헬퍼로 단일화한다.
#    _sido_variants()   : 정식표기 → 별칭 후보 목록
#    _region_api_root() : 별칭 × (STAGE2 사용/생략) 순차 시도, 예외 흡수
#  (JS 내보내기 엔진의 listBySido/bedsBySido/fetchKiosk/regionTask 도
#   동일 정책으로 동기 수정 — 국소최적화 금지)
# ══════════════════════════════════════════════════════════════════
def _sido_variants(sido):
    """STAGE1/Q0 에 시도할 시/도 표기 후보 (정식 → 별칭)."""
    if not sido:
        return ['']
    out = [sido]
    for a in _SIDO_ALIAS.get(sido, []):
        if a not in out:
            out.append(a)
    return out


def _region_api_root(url, sido, gugun='', extra=None, timeout=12, ctx='',
                     key1='STAGE1', key2='STAGE2'):
    """시/도 별칭 × (시군구 사용→생략) 순으로 시도해 <item>이 1건 이상인
    응답 root 를 반환. 전 후보 실패 시 마지막 root(없으면 None).
    개별 후보의 예외는 로그만 남기고 흡수한다(폴백 도달 보장)."""
    last = None
    variants = _sido_variants(sido)
    modes = [True, False] if gugun else [False]
    for q in variants:
        for use_gu in modes:
            params = {'serviceKey': SERVICE_KEY, key1: q, 'pageNo': '1',
                      'numOfRows': ('100' if use_gu else '400')}
            if extra:
                params.update(extra)
            if use_gu:
                params[key2] = gugun
            try:
                resp = _http_get(url, params=params, timeout=timeout)
                if resp.status_code != 200:
                    _ns_dbg('[RGN] %s %s=%s%s HTTP %s'
                            % (ctx, key1, q, ('/' + gugun) if use_gu else '',
                               resp.status_code))
                    continue
                root = ET.fromstring(resp.content)
                rc = root.findtext('.//resultCode')
                if rc is not None and rc not in ('', '00'):
                    _ns_dbg('[RGN] %s %s=%s resultCode=%s' % (ctx, key1, q, rc))
                    continue
                last = root
                if root.find('.//item') is not None:
                    if q != sido or not use_gu:
                        _ns_dbg('[RGN] %s 폴백성공: %s=%s%s (원본 %s/%s)'
                                % (ctx, key1, q,
                                   ('' if use_gu else ' · %s생략' % key2),
                                   sido, gugun or '-'))
                    return root
            except Exception as _re:
                _ns_dbg('[RGN] %s %s=%s%s 예외: %s'
                        % (ctx, key1, q, ('/' + gugun) if use_gu else '', _re))
    _ns_dbg('[RGN] %s 전 후보 0건 (%s/%s)' % (ctx, sido, gugun or '-'))
    return last


# ══════════════════════════════════════════════════════════════════
#  [ROOT-FIX 2026-G4] 시/도 파라미터 '축' 전면 탐색기 (probe)
#
#  이전 수정은 최종 폴백을 '_fetch_list_nationwide(Q0 없는 전국 페이징)'
#  에 걸었으나, 그 축이 처음부터 죽어 있으면(필수 파라미터 요구 등)
#  전 계층이 동시에 0건이 된다 — 광주광역시가 계속 0개였던 이유.
#  따라서 '어떤 축이 살아 있는지'를 추측하지 않고 전부 시도한다.
#    ① LIST Q0=정식/별칭        ② LIST Q0+Q1(구단위)
#    ③ LIST Q1 단독(Q0 없음)     ④ LIST QZ=A / 무파라미터 전국
#    ⑤ STRM STAGE1=정식/별칭     ⑥ BED STAGE1 / STAGE2 단독 / 전국
#  성공한 축의 결과는 _PROBE_CACHE 에 보관하고, 모든 시도 결과를
#  [G4] 로그와 /diag/sido 페이지에 그대로 남긴다(재현 가능한 진단).
# ══════════════════════════════════════════════════════════════════
_PROBE_CACHE = {}
_PROBE_TTL = 600
_PROBE_LOCK = _threading.Lock()


def _probe_try(url, params, timeout=12):
    """후보 1건 실행 → (item elements, 요약문자열). 예외는 전부 흡수."""
    p = {'serviceKey': SERVICE_KEY, 'pageNo': '1', 'numOfRows': '400'}
    p.update(params)
    try:
        r = _http_get(url, params=p, timeout=timeout)
        if r.status_code != 200:
            return [], 'HTTP %s' % r.status_code
        root = ET.fromstring(r.content)
        rc = root.findtext('.//resultCode')
        msg = (root.findtext('.//resultMsg') or root.findtext('.//returnAuthMsg')
               or root.findtext('.//errMsg') or root.findtext('.//returnReasonCode') or '')
        tc = root.findtext('.//totalCount') or '-'
        its = root.findall('.//item')
        return its, 'rc=%s total=%s item=%d %s' % (rc, tc, len(its), str(msg)[:48])
    except Exception as e:
        return [], 'EXC %s' % str(e)[:70]


def _rows_from_list_items(its, sido, hint):
    """목록 API <item> → 해당 시/도 로스터 레코드만 추출."""
    out = []
    for it in its:
        try:
            r = _list_row(it, hint)
        except Exception:
            r = None
        if r and r['sido'] == sido:
            out.append(r)
    return out


def _rows_from_idname_items(its, sido):
    """주소가 없는 응답(중증질환/병상 API) → 최소 로스터 레코드.
    포화도 화면은 hpid/name/sido/level 만 있으면 표출 가능하다."""
    out, seen = [], set()
    for it in its:
        hp = (it.findtext('hpid') or '').strip()
        if not hp or hp in seen:
            continue
        seen.add(hp)
        nm = (it.findtext('dutyName') or '').strip() or hp
        ad = (it.findtext('dutyAddr') or '').strip()
        sd = _sido_of(ad) or sido
        if sd != sido:
            continue
        out.append({
            'hpid': hp, 'name': nm, 'dutyAddr': ad,
            'dutyTel1': (it.findtext('dutyTel1') or '').strip(),
            'dutyTel3': (it.findtext('dutyTel3') or '').strip(),
            'emcls': '', 'emclsName': '',
            'level': _get_hospital_level('', nm),
            'sido': sido, 'gugun': _split_gugun(sido, ad),
        })
    return out


_HPID_INFO = {}
_HPID_LOCK = _threading.Lock()


def _hpid_info(hpid):
    """HPID 단건 기본정보 → 로스터 레코드(주소·종별 포함). 결과는 영구 캐시."""
    with _HPID_LOCK:
        if hpid in _HPID_INFO:
            return _HPID_INFO[hpid]
    row = None
    try:
        its, _meta = _probe_try(BASS_API_URL, {'HPID': hpid, 'numOfRows': '10'}, 10)
        for it in its:
            r = _list_row(it, '')
            if r and r['sido']:
                row = r
                break
    except Exception:
        row = None
    with _HPID_LOCK:
        _HPID_INFO[hpid] = row
    return row


def _rows_by_hpid_lookup(its, sido, limit=150):
    """[SAFETY 2026-G4] 주소 없는 응답(STRM·BED)은 질의 파라미터만 믿고
    시/도를 붙이면 오분류된다(예: STAGE2=동구 는 전국의 모든 '동구'를 준다).
    → hpid 를 기관 기본정보로 역조회해 '실제 주소'로만 소속을 확정한다."""
    seen, out, n, miss = set(), [], 0, 0
    for it in its:
        hp = (it.findtext('hpid') or '').strip()
        if not hp or hp in seen:
            continue
        seen.add(hp)
        ad = (it.findtext('dutyAddr') or '').strip()
        if ad and _sido_of(ad) == sido:            # 주소가 실려온 경우 즉시 채택
            r = _list_row(it, sido)
            if r:
                out.append(r)
            continue
        if ad:                                     # 주소가 있는데 타 시/도
            continue
        if n >= limit:
            miss += 1
            continue
        n += 1
        r = _hpid_info(hp)
        if r and r['sido'] == sido:
            out.append(r)
    if miss:
        _ns_dbg('[G4] %s hpid역조회 상한(%d) 초과 %d건 미해석' % (sido, limit, miss))
    return out


def _hpid_set(its):
    out = set()
    for it in its:
        h = (it.findtext('hpid') or '').strip()
        if h:
            out.add(h)
    return out


def _filter_honored(url, key, val, sido, its, timeout=10):
    """[SAFETY 2026-G4] 주소가 없는 응답(STRM·BED)은 '이 시/도 소속'을
    질의 파라미터만 믿고 라벨링하게 된다. 만약 API 가 그 필터를 무시하고
    전국을 돌려주면 전혀 다른 지역 병원이 이 시/도로 오분류된다.
    → 다른 시/도 값으로 같은 질의를 1회 더 던져 hpid 집합이 동일하면
      '필터 무시'로 판정하고 해당 축을 폐기한다."""
    try:
        a = _hpid_set(its)
        if not a:
            return False, '결과없음'
        ctrl = '제주특별자치도' if sido != '제주특별자치도' else '서울특별시'
        b_its, _m = _probe_try(url, {key: ctrl, 'numOfRows': '400'}, timeout)
        b = _hpid_set(b_its)
        if b and a == b:
            return False, '※%s 필터무시(대조군 %s 동일) → 폐기' % (key, ctrl)
        return True, ''
    except Exception as e:
        return False, '대조군검증 예외 %s' % str(e)[:40]


def _probe_sido(sido, full=False, timeout=10):
    """모든 축을 순차 시도. full=True 면 성공해도 끝까지(진단 페이지용).
    반환 (rows, trace). 절대 예외를 던지지 않는다."""
    gus = DISTRICTS.get(sido) or []
    vs = _sido_variants(sido)
    trace, best, best_by = [], [], ''

    def note(label, meta, rows):
        trace.append({'q': label, 'meta': meta, 'n': len(rows)})
        _ns_dbg('[G4] %s | %s | %s | rows=%d' % (sido, label, meta, len(rows)))

    def stop():
        return bool(best) and not full

    # ① LIST Q0=정식/별칭
    for q in vs:
        if stop():
            break
        its, meta = _probe_try(LIST_API_URL, {'Q0': q, 'numOfRows': '500'}, timeout)
        rows = _rows_from_list_items(its, sido, sido)
        note('LIST Q0=%s' % q, meta, rows)
        if rows and not best:
            best, best_by = rows, 'LIST Q0=%s' % q
    # ② LIST Q0+Q1 (구단위 합집합)
    for q in vs:
        if stop():
            break
        merged, seen, metas = [], set(), []
        for g in gus:
            its, meta = _probe_try(LIST_API_URL,
                                   {'Q0': q, 'Q1': g, 'numOfRows': '200'}, timeout)
            metas.append('%s:%d' % (g, len(its)))
            for r in _rows_from_list_items(its, sido, sido):
                if r['hpid'] not in seen:
                    seen.add(r['hpid'])
                    merged.append(r)
        note('LIST Q0=%s+Q1' % q, ' '.join(metas)[:120], merged)
        if merged and not best:
            best, best_by = merged, 'LIST Q0=%s+Q1' % q
    # ③ LIST Q1 단독 (Q0 없음) — 시/도 축이 통째로 고장난 경우용
    if not stop():
        merged, seen, metas = [], set(), []
        for g in gus:
            its, meta = _probe_try(LIST_API_URL, {'Q1': g, 'numOfRows': '300'}, timeout)
            metas.append('%s:%d' % (g, len(its)))
            for r in _rows_from_list_items(its, sido, ''):
                if r['hpid'] not in seen:
                    seen.add(r['hpid'])
                    merged.append(r)
        note('LIST Q1단독(Q0없음)', ' '.join(metas)[:120], merged)
        if merged and not best:
            best, best_by = merged, 'LIST Q1단독'
    # ④ LIST 전국 (QZ=A / 무파라미터)
    for lab, pr in (('LIST QZ=A 전국', {'QZ': 'A', 'numOfRows': '500'}),
                    ('LIST 무파라미터 전국', {'numOfRows': '500'})):
        if stop():
            break
        its, meta = _probe_try(LIST_API_URL, pr, timeout + 5)
        rows = _rows_from_list_items(its, sido, '')
        note(lab, meta, rows)
        if rows and not best:
            best, best_by = rows, lab
    # ⑤ STRM(중증질환자 수용정보) STAGE1 — 다른 백엔드 테이블
    for q in vs:
        if stop():
            break
        its, meta = _probe_try(STRM_API_URL, {'STAGE1': q}, timeout)
        rows = []
        if its:
            ok, why = _filter_honored(STRM_API_URL, 'STAGE1', q, sido, its, timeout)
            if ok:
                rows = _rows_by_hpid_lookup(its, sido)
            else:
                meta += ' ' + why
        note('STRM STAGE1=%s' % q, meta, rows)
        if rows and not best:
            best, best_by = rows, 'STRM STAGE1=%s' % q
    # ⑥ BED(실시간병상) STAGE1 / STAGE2단독 / 전국
    for q in vs:
        if stop():
            break
        its, meta = _probe_try(API_URL, {'STAGE1': q, 'numOfRows': '400'}, timeout)
        rows = []
        if its:
            ok, why = _filter_honored(API_URL, 'STAGE1', q, sido, its, timeout)
            if ok:
                rows = _rows_by_hpid_lookup(its, sido)
            else:
                meta += ' ' + why
        note('BED STAGE1=%s' % q, meta, rows)
        if rows and not best:
            best, best_by = rows, 'BED STAGE1=%s' % q
    if not stop():
        pool, metas, sets = [], [], []
        for g in gus:
            its, meta = _probe_try(API_URL, {'STAGE2': g, 'numOfRows': '200'}, timeout)
            metas.append('%s:%d' % (g, len(its)))
            sets.append(frozenset(_hpid_set(its)))
            pool.extend(its)
        #  구마다 완전히 같은 집합이면 STAGE2 가 무시된 것 → 전국 오분류 방지
        if len(sets) >= 2 and sets[0] and all(x == sets[0] for x in sets):
            pool = []
            metas.append('※STAGE2 필터무시(구별 결과 동일) → 폐기')
        #  '동구·남구' 등은 전국 여러 시/도에 존재하므로 질의값을 믿으면 안 된다.
        #  hpid 기본정보 역조회로 실제 주소를 확인해 소속을 확정한다.
        merged = _rows_by_hpid_lookup(pool, sido) if pool else []
        note('BED STAGE2단독(STAGE1없음)', ' '.join(metas)[:140], merged)
        if merged and not best:
            best, best_by = merged, 'BED STAGE2단독'
    if not stop():
        its, meta = _probe_try(API_URL, {'numOfRows': '500'}, timeout + 5)
        #  전국 응답은 주소가 없으므로 hpid 역조회로만 귀속 확정 (상한 적용)
        rows = _rows_by_hpid_lookup(its, sido, limit=200) if its else []
        note('BED 무파라미터 전국', meta, rows)
        if rows and not best:
            best, best_by = rows, 'BED 무파라미터 전국'

    _ns_dbg('[G4] %s PROBE 종료 → %d건 (승리축: %s)'
            % (sido, len(best), best_by or '없음'))
    return best, trace


def _probe_sido_cached(sido):
    now = time.time()
    with _PROBE_LOCK:
        ent = _PROBE_CACHE.get(sido)
    if ent and (now - ent[0] < _PROBE_TTL) and ent[1]:
        return ent[1]
    rows, _tr = _probe_sido(sido, full=False)
    with _PROBE_LOCK:
        _PROBE_CACHE[sido] = (time.time(), rows)
    return rows


def _fetch_sido_list(sido):
    """단일 시/도 목록. 별칭 → 구단위 순으로 폴백. 절대 예외를 던지지 않는다."""
    # ① Q0(정식/별칭) 페이징
    #  통합시는 '광주'·'전남' 두 축이 서로 다른 부분집합을 주므로 합집합
    _union = sido in _UNION_SIDO
    _acc, _accseen = [], set()
    for q in _sido_variants(sido):
        rows, page = [], 1
        while page <= 5:
            params = {'serviceKey': SERVICE_KEY, 'Q0': q,
                      'pageNo': str(page), 'numOfRows': '500'}
            try:
                got, total = _list_rows_stream(
                    _http_get(LIST_API_URL, params=params, timeout=12),
                    sido, 'list ' + q)
            except Exception as _le:
                #  ROOT-FIX: 여기서 예외를 전파하면 아래 구단위 폴백에
                #   영원히 도달하지 못한다(광주/전남 0건의 직접 원인).
                _ns_dbg(' %s: Q0="%s" p%d 실패 %s' % (sido, q, page, _le))
                break
            rows.extend(got)
            if not got or page * 500 >= total:
                break
            page += 1
        if rows:
            if q != sido:
                _ns_dbg(' %s: 별칭 "%s" 로 %d건' % (sido, q, len(rows)))
            if not _union:
                return rows
            for _r in rows:
                if _r['hpid'] not in _accseen:
                    _accseen.add(_r['hpid'])
                    _acc.append(_r)
    if _union and _acc:
        _ns_dbg(' %s: 별칭 %d개 합집합 → %d건'
                % (sido, len(_sido_variants(sido)), len(_acc)))
        return _acc
    # ② Q0 가 0건/오류만 주는 시/도 → Q1(시/군/구) 단위 합집합
    gus = DISTRICTS.get(sido) or []
    if not gus:
        _ns_dbg(' %s: 구/군 목록 없음 → 폴백 불가' % sido)
        return []
    for q in _sido_variants(sido):
        merged, seen = [], set()
        for gu in gus:
            try:
                got, _t = _list_rows_stream(
                    _http_get(LIST_API_URL, params={
                        'serviceKey': SERVICE_KEY, 'Q0': q, 'Q1': gu,
                        'pageNo': '1', 'numOfRows': '200'},
                        timeout=12), sido, 'list %s/%s' % (q, gu))
            except Exception as _ge:
                _ns_dbg(' %s/%s: 구단위 조회 실패 %s' % (sido, gu, _ge))
                continue
            for r in got:
                if r['hpid'] not in seen:
                    seen.add(r['hpid'])
                    merged.append(r)
        if merged:
            _ns_dbg(' %s: 구단위 재조회로 %d건 복구 (Q0="%s")' % (sido, len(merged), q))
            return merged
    # ③ [ROOT-FIX 2026-G1] Q0/Q1 필터 자체가 고장난 경우 → 전국 스냅샷에서
    #    주소 기준으로 추출 (시/도 파라미터를 전혀 쓰지 않는 유일한 경로)
    try:
        pick = [h for h in _nat_list_cached() if h.get('sido') == sido]
    except Exception as _pe:
        pick = []
        _ns_dbg(' %s: 전국스냅샷 폴백 예외 %s' % (sido, _pe))
    if pick:
        _ns_dbg(' %s: 전국스냅샷에서 %d건 복구' % (sido, len(pick)))
        return pick
    #  ④ 파라미터 축 전면탐색(probe)은 /diag/sido 수동 진단 전용으로 분리했다.
    #     자동 경로에 두면 최대 수십 회 호출로 응답이 지연되고, 실제 원인은
    #     API 가 아니라 행정구역 테이블이었다(2026-H1).
    _ns_dbg(' %s: Q0·별칭·구단위·전국 모두 0건 → /diag/sido 로 진단 필요' % sido)
    return []


def _fetch_list_nationwide():
    """Q0 없이 전국 페이징. 지원되지 않으면 빈 리스트."""
    rows, page = [], 1
    while page <= 4:
        params = {'serviceKey': SERVICE_KEY, 'pageNo': str(page), 'numOfRows': '500'}
        got, total = _list_rows_stream(
            _http_get(LIST_API_URL, params=params, timeout=15), '', 'list-all')
        rows.extend(got)
        if not got or page * 500 >= total:
            break
        page += 1
    return rows


# ══════════════════════════════════════════════════════════════════
#  [ROOT-FIX 2026-G1] 시/도 필터 무관 전국 스냅샷 (목록/병상 공용)
#
#  근본원인: Q0/STAGE1(시/도) 필터가 광주광역시·전라남도에서 0건을
#   반환한다. 기존 폴백(별칭·구단위)은 전부 '같은 고장난 필터'를 다시
#   쓰므로 필터 자체가 죽으면 전 계층이 동시에 0건이 된다.
#   → 포화도(과밀지수) 화면은 로스터 캐시만 보므로 광주가 통째로 사라졌다.
#     (지역검색은 라이브 폴백이 있어 증상이 가려져 있었다)
#  대책: '시/도 파라미터를 아예 쓰지 않는' 전국 페이징 결과를 캐시해
#   목록·병상 양쪽의 최종 폴백으로 삼는다. 주소/hpid 로 사후 분류한다.
# ══════════════════════════════════════════════════════════════════
_NAT_LIST = {'ts': 0.0, 'rows': []}
_NAT_BEDS = {'ts': 0.0, 'map': {}}
_NAT_LIST_TTL = 300
_NAT_BEDS_TTL = 90
_NAT_LOCK = _threading.Lock()
#  17개 시/도가 동시에 폴백에 진입해도 전국조회는 1회만 나가도록 직렬화
#  (Android 소켓/메모리 폭주 = 과거 ERR_CONNECTION_REFUSED 의 직접 원인)
_NAT_FETCH_LIST_LOCK = _threading.Lock()
_NAT_FETCH_BEDS_LOCK = _threading.Lock()


def _nat_list_cached(force=False):
    """전국 기관목록 스냅샷 (Q0 미사용). 실패 시 직전 캐시 유지."""
    now = time.time()
    with _NAT_LOCK:
        rows, ts = _NAT_LIST['rows'], _NAT_LIST['ts']
    if (not force) and rows and (now - ts < _NAT_LIST_TTL):
        return rows
    with _NAT_FETCH_LIST_LOCK:
        with _NAT_LOCK:                       # 대기 중 다른 스레드가 채웠으면 재사용
            rows, ts = _NAT_LIST['rows'], _NAT_LIST['ts']
        if rows and (time.time() - ts < _NAT_LIST_TTL):
            return rows
        try:
            got = _fetch_list_nationwide()
        except Exception as e:
            _ns_dbg('[NAT] 전국목록 실패 %s' % e)
            return rows
        if got:
            with _NAT_LOCK:
                _NAT_LIST['rows'] = got
                _NAT_LIST['ts'] = time.time()
            _ns_dbg('[NAT] 전국목록 %d건 캐시' % len(got))
            return got
        return rows


def _fetch_beds_nationwide():
    """STAGE1 없이 전국 실시간병상 페이징. {hpid: bedrow}"""
    out, page = {}, 1
    while page <= 4:
        params = {'serviceKey': SERVICE_KEY, 'pageNo': str(page), 'numOfRows': '500'}
        total = -1
        n0 = len(out)
        for it, tc in _api_items(_http_get(API_URL, params=params, timeout=20),
                                 'bed-all p%d' % page):
            if tc >= 0:
                total = tc
            hpid = (it.findtext('hpid') or '').strip()
            if hpid and hpid not in out:
                out[hpid] = _bed_item_row(it)
        if len(out) == n0 or (total >= 0 and page * 500 >= total):
            break
        page += 1
    return out


def _nat_beds_cached(force=False):
    """전국 병상 스냅샷 (STAGE1 미사용). 실패 시 직전 캐시 유지."""
    now = time.time()
    with _NAT_LOCK:
        m, ts = _NAT_BEDS['map'], _NAT_BEDS['ts']
    if (not force) and m and (now - ts < _NAT_BEDS_TTL):
        return m
    with _NAT_FETCH_BEDS_LOCK:
        with _NAT_LOCK:                       # 대기 중 다른 스레드가 채웠으면 재사용
            m, ts = _NAT_BEDS['map'], _NAT_BEDS['ts']
        if m and (time.time() - ts < _NAT_BEDS_TTL):
            return m
        try:
            got = _fetch_beds_nationwide()
        except Exception as e:
            _ns_dbg('[NAT] 전국병상 실패 %s' % e)
            return m
        if got:
            with _NAT_LOCK:
                _NAT_BEDS['map'] = got
                _NAT_BEDS['ts'] = time.time()
            _ns_dbg('[NAT] 전국병상 %d건 캐시' % len(got))
            return got
        return m


def _learn_admin(rows):
    """[재발방지 2026-H1] 주소 데이터를 진실의 원천으로 삼아, DISTRICTS 에
    없는 시/도 표기가 나타나면 자동 등록하고 강한 경고를 남긴다.

    이번 사고(전남광주통합특별시 신설)처럼 행정구역이 개편되면
    하드코딩 테이블만 믿는 코드는 해당 지역을 통째로 잃는다.
    이제는 앱이 스스로 따라가고, 사람이 즉시 알아챌 수 있게 기록한다."""
    unknown = {}
    for h in rows:
        if h.get('sido'):
            continue
        head = _addr_head(h.get('dutyAddr'))
        if len(head) < 3:
            continue
        unknown.setdefault(head, []).append(h)
    added = []
    for head, hs in unknown.items():
        if len(hs) < 2:                       # 오타·1회성 표기는 무시
            continue
        gus = set()
        for h in hs:
            t = (h.get('dutyAddr') or '').split()
            if len(t) >= 2 and t[1][-1:] in ('시', '군', '구'):
                gus.add(t[1])
        DISTRICTS[head] = sorted(gus) if gus else [head]
        _sido_reset_lookup()
        for h in hs:
            h['sido'] = head
            h['gugun'] = _split_gugun(head, h.get('dutyAddr'))
        added.append('%s(%d건·하위%d)' % (head, len(hs), len(DISTRICTS[head])))
    if added:
        _ulog('ADMIN', '★ 미등록 시/도 자동등록: ' + ' / '.join(added)
              + '  ← 행정구역 개편으로 보임. 소스 DISTRICTS 갱신 필요.')
    return bool(added)


def _all_hospitals(force=False):
    """전국 로스터. 전국페이징 ∪ 시도별(별칭·구단위 폴백) 합집합.
    [ROOT-FIX 2026-D2] 0건 시/도가 남으면
      ① 해당 시/도만 순차(직렬) 재조회로 자동복구
      ② 그래도 남으면 캐시 TTL 을 12h → 3분 으로 낮춰 다음 요청에 재시도
    기존에는 0건이어도 12시간 캐시되어 광주/전남이 반나절 동안 사라졌다."""
    now = time.time()
    with _ALL_CACHE_LOCK:
        d, ts = _ALL_CACHE['data'], _ALL_CACHE['ts']
        ttl = _ALL_CACHE.get('ttl', _ALL_CACHE_TTL)
    if (not force) and d and (now - ts < ttl):
        _ns_dbg('roster cache HIT n=%d age=%ds ttl=%ds' % (len(d), int(now - ts), ttl))
        return d, [], True

    t0 = time.time()
    _ns_dbg('roster fetch START force=%s' % force)
    rows, fails = [], []
    futs = {_NET_POOL.submit(_fetch_sido_list, s): s for s in DISTRICTS}
    futs[_NET_POOL.submit(_nat_list_cached)] = '*전국'
    for f in as_completed(futs):
        s = futs[f]
        try:
            got = f.result()
            rows.extend(got)
            _ns_dbg(' %s: %d' % (s, len(got)))
        except Exception as e:
            fails.append('%s: %s' % (s, e))
            _ns_dbg(' %s: FAIL %s' % (s, e))

    if not rows:
        _ns_dbg('roster FAILED %s' % fails[:3])
        return [], fails, False

    def _dedup(src):
        seen2, out2 = set(), []
        for h2 in src:
            if h2['hpid'] in seen2:
                continue
            seen2.add(h2['hpid'])
            out2.append(h2)
        out2.sort(key=lambda x: (x['sido'], x['gugun'], x['name']))
        return out2

    def _by_sido(src):
        m = {}
        for h2 in src:
            m[h2['sido']] = m.get(h2['sido'], 0) + 1
        return m

    uniq = _dedup(rows)
    #  [진단 2026-H1] 미분류 주소 자동학습 → 분포를 매번 로그에 남긴다.
    #   '어느 시/도가 몇 건인지'가 파일에 남아야 이번 같은 오진을 반복하지 않는다.
    try:
        _learn_admin(uniq)
    except Exception as _le2:
        _ulog('ADMIN', '자동학습 예외 %s' % _le2)
    by_sido = _by_sido(uniq)
    try:
        _unk = sum(1 for h in uniq if not h.get('sido'))
        _ulog('ADMIN', '로스터 분포(n=%d): %s%s'
              % (len(uniq),
                 ' '.join('%s=%d' % (k, by_sido.get(k, 0)) for k in DISTRICTS),
                 (' | 미분류=%d' % _unk) if _unk else ''))
    except Exception:
        pass
    missing = [k for k in DISTRICTS if by_sido.get(k, 0) == 0]

    # ── [ROOT-FIX 2026-D2] 0건 시/도 순차 재조회 (동시호출 폭주 회피) ──
    if missing:
        _ns_dbg('WARN 로스터 0건 시도: %s → 순차 재조회 시작' % missing)
        for _ms in list(missing):
            try:
                _got = _fetch_sido_list(_ms)
            except Exception as _me:
                _got = []
                _ns_dbg(' repair %s: 예외 %s' % (_ms, _me))
            _ns_dbg(' repair %s: %d건' % (_ms, len(_got)))
            if _got:
                rows.extend(_got)
        uniq = _dedup(rows)
        by_sido = _by_sido(uniq)
        missing = [k for k in DISTRICTS if by_sido.get(k, 0) == 0]

    _ttl = _ALL_CACHE_TTL if not missing else _ALL_CACHE_TTL_SHORT
    if missing:
        _ns_dbg('WARN 재조회 후에도 0건: %s → 캐시 TTL %ds (자동 재시도)'
                % (missing, _ttl))

    with _ALL_CACHE_LOCK:
        _ALL_CACHE['data'] = uniq
        _ALL_CACHE['ts'] = time.time()
        _ALL_CACHE['ttl'] = _ttl
        _ALL_CACHE['missing'] = missing
    _ns_dbg('roster DONE n=%d sido=%d fails=%d miss=%d ttl=%ds %.1fs'
            % (len(uniq), len(by_sido), len(fails), len(missing), _ttl, time.time() - t0))
    return uniq, fails, False


@flask_app.route('/diag/sido')
def diag_sido():
    """[2026-G4] 특정 시/도에 대해 모든 파라미터 축을 실제로 호출해
    resultCode·totalCount·item수를 그대로 보여준다. 추측 제거용.
    사용: http://127.0.0.1:5000/diag/sido?sido=광주광역시"""
    sido = (request.args.get('sido') or '광주광역시').strip()
    t0 = time.time()
    try:
        rows, trace = _probe_sido(sido, full=True, timeout=8)
        err = ''
    except Exception as e:
        rows, trace, err = [], [], str(e)
    el = time.time() - t0
    with _ALL_CACHE_LOCK:
        rcnt = len(_ALL_CACHE.get('data') or [])
        rmiss = list(_ALL_CACHE.get('missing') or [])
        rage = int(time.time() - (_ALL_CACHE.get('ts') or 0))
    rsido = 0
    with _ALL_CACHE_LOCK:
        for h in (_ALL_CACHE.get('data') or []):
            if h.get('sido') == sido:
                rsido += 1
    lines = ['BUILD=%s  sido=%s  %.1fs' % (BUILD_ID, sido, el),
             '통합 로그: %s' % _log_path(),
             '로스터캐시: 전체 %d건 / %s %d건 / age %ds / missing=%s'
             % (rcnt, sido, rsido, rage, ','.join(rmiss) or '-'),
             '-' * 72]
    for t in trace:
        lines.append('%-26s | %-52s | rows=%d' % (t['q'][:26], t['meta'][:52], t['n']))
    lines.append('-' * 72)
    with _HPID_LOCK:
        _hn = len(_HPID_INFO)
        _hok = sum(1 for v in _HPID_INFO.values() if v)
    lines.append('HPID 기본정보 역조회: 시도 %d건 / 성공 %d건' % (_hn, _hok))
    lines.append('PROBE 최종: %d건' % len(rows))
    for r in rows[:40]:
        lines.append('  %s %s [%s] %s' % (r['hpid'], r['name'], r['level'],
                                          r.get('dutyAddr') or '(주소없음)'))
    if err:
        lines.append('EXC: ' + err)
    txt = '\n'.join(lines)
    _ns_dbg('[G4] diag %s 완료 %d건 %.1fs' % (sido, len(rows), el))
    body = ('<!doctype html><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>diag %s</title>'
            '<style>body{font-family:monospace;font-size:12px;margin:8px;}'
            'textarea{width:100%%;height:60vh;font-family:monospace;font-size:11px;}'
            'button{padding:8px 14px;font-size:14px;margin:4px 4px 8px 0;}'
            'a{font-size:13px;}</style>'
            '<button onclick="navigator.clipboard.writeText('
            'document.getElementById(&quot;t&quot;).value)">전체 복사</button>'
            '<a href="/">← 홈</a><br>'
            '<textarea id="t" readonly>%s</textarea>') % (
                sido, txt.replace('&', '&amp;').replace('<', '&lt;'))
    return body


@flask_app.errorhandler(Exception)
def _flask_crash(e):
    """[일원화 2026-H1] 라우트에서 새어 나온 예외를 전부 통합 로그에 남긴다.
    (이전에는 500 만 반환되고 원인이 어느 파일에도 남지 않았다)"""
    import traceback as _tb
    try:
        _ulog('CRASH', 'FLASK %s %s -> %s\n%s'
              % (request.method, request.full_path, e, _tb.format_exc()))
    except Exception:
        pass
    code = getattr(e, 'code', 500)
    if not isinstance(code, int):
        code = 500
    return jsonify({'success': False, 'error': str(e), 'build': BUILD_ID}), code


@flask_app.route('/diag')
def diag_home():
    """[진단 2026-H1] 단일 진단 화면 — 빌드/경로/로스터 분포/최근 로그.
    http://127.0.0.1:5000/diag"""
    with _ALL_CACHE_LOCK:
        base = list(_ALL_CACHE.get('data') or [])
        age = int(time.time() - (_ALL_CACHE.get('ts') or 0))
        miss = list(_ALL_CACHE.get('missing') or [])
    cnt = {}
    for h in base:
        cnt[h.get('sido') or '(미분류)'] = cnt.get(h.get('sido') or '(미분류)', 0) + 1
    L = ['BUILD=%s   %s' % (BUILD_ID, datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
         '통합 로그   : %s (%s)' % (_log_path(), _log_size_str()),
         '통합 상태   : %s' % _state_path(),
         '로그 중복   : %s' % (_log_strays() or '없음'),
         '로스터      : %d건 / age %ds / missing=%s' % (len(base), age, ','.join(miss) or '-'),
         '',
         '[PiP 최근 시도]',
         '  단계        : %s' % (_PIP_LAST.get('stage') or '-'),
         '  성공        : %s' % _PIP_LAST.get('ok'),
         '  실패사유    : %s' % (_PIP_LAST.get('reason') or '-'),
         '  API레벨     : %s' % _PIP_LAST.get('api'),
         '  기기지원    : %s' % _PIP_LAST.get('device_feature'),
         '  매니페스트  : %s  (false 면 Pydroid3 등 PiP 미선언 호스트)'
         % _PIP_LAST.get('manifest_flag'),
         '  h_param     : %s' % (_pip_state.get('h_param') or '-')[:60],
         '',
         '[시/도 분포]']
    for k in list(DISTRICTS.keys()) + ['(미분류)']:
        if k in cnt or k in DISTRICTS:
            L.append('  %-16s %d' % (k, cnt.get(k, 0)))
    L += ['', '[최근 로그 %d줄]' % len(_LOG_RING)]
    L += list(_LOG_RING)
    txt = '\n'.join(L).replace('&', '&amp;').replace('<', '&lt;')
    return ('<!doctype html><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>ERmon diag</title>'
            '<style>body{font-family:monospace;font-size:12px;margin:8px;}'
            'textarea{width:100%;height:70vh;font-family:monospace;font-size:11px;}'
            'button,a{padding:8px 12px;font-size:14px;margin:4px 6px 8px 0;'
            'display:inline-block;}</style>'
            '<button onclick="navigator.clipboard.writeText('
            'document.getElementById(&quot;t&quot;).value)">전체 복사</button>'
            '<a href="/diag/sido?sido=' + SIDO_MERGED + '">시/도 축 진단</a>'
            '<a href="/">홈</a><br>'
            '<textarea id="t" readonly>' + txt + '</textarea>')


@flask_app.route('/api/ns_dbg')
def api_ns_dbg():
    """클라이언트(JS) 디버그 라인을 서버 로그 파일로 전달."""
    _ulog('JS', request.args.get('m', ''))
    return ('', 204)


@flask_app.route('/api/hospitals_all')
def get_hospitals_all():
    rows, fails, cached = _all_hospitals(request.args.get('force') == '1')
    if not rows:
        return jsonify({'success': False, 'error': '전국 목록 조회 실패: ' + '; '.join(fails[:3])})
    with _ALL_CACHE_LOCK:
        _miss = list(_ALL_CACHE.get('missing') or [])
    return jsonify({'success': True, 'cached': cached, 'count': len(rows),
                    'failed': fails, 'missing': _miss, 'hospitals': rows})


@flask_app.route('/api/hospitals')
def get_hospitals():
    """지역 목록. gugun 생략 시 시/도 전체.
    로스터 캐시에서 필터 → 0건이면 라이브 Q0/Q1 폴백."""
    sido  = request.args.get('sido', '').strip()
    gugun = request.args.get('gugun', '').strip()
    if not sido:
        return jsonify({'success': False, 'error': '시/도를 선택해주세요.'})
    t0 = time.time()
    base, _f, _c = _all_hospitals(False)

    def ok(h):
        if h['sido'] != sido:
            return False
        if not gugun:
            return True
        return h['gugun'] == gugun or gugun in (h['dutyAddr'] or '')

    rows = [h for h in base if ok(h)]
    src = 'roster'
    if not rows:
        src = 'live'
        try:
            # 공공API 는 일부 시/도명(예: 광주광역시, 전라남도)에 0건을 반환한다.
            # 별칭 재시도 + 페이징이 내장된 _fetch_sido_list 를 사용해 복구한다.
            rows = _fetch_sido_list(sido)
            if not rows and gugun:
                #  ROOT-FIX 2026-D1: 여기도 별칭 폴백을 경유해야 한다.
                #   (Q0=정식표기 고정은 광주/전남에서 항상 0건)
                _rt = _region_api_root(LIST_API_URL, sido, gugun, timeout=12,
                                       ctx='region-live', key1='Q0', key2='Q1')
                rows = _list_rows(_rt, sido) if _rt is not None else []
            if gugun:
                rows = [h for h in rows if h['gugun'] == gugun or gugun in (h['dutyAddr'] or '')]
        except Exception as e:
            _ns_dbg('region live FAIL %s %s %s' % (sido, gugun, e))
            return jsonify({'success': False, 'error': '서버 오류: %s' % e})
    rows = sorted(rows, key=lambda x: x['name'])
    _ns_dbg('region %s %s n=%d src=%s %.2fs' % (sido, gugun or '전체', len(rows), src, time.time() - t0))
    return jsonify({'success': True, 'cached': (src == 'roster'), 'source': src, 'hospitals': rows})


# ── 병상 (시/도 단위 캐시 90초) ────────────────────────────────
_BED_CACHE = {}
_BED_TTL   = 90
_BED_LOCK  = _threading.Lock()


def _sum_beds(item, tags):
    """기준병상>0 인 항목만 합산. 가용 미제공→0, 실제 음수(초과수용)는 보존."""
    a = t = 0
    for av_tag, tt_tag in tags:
        tt = get_hvs(item, tt_tag)
        if tt is None or tt <= 0:
            continue
        rawv = (item.findtext(av_tag) or '').strip()
        try:
            av = int(rawv)
        except (TypeError, ValueError):
            av = 0
        t += tt
        a += av
    return (a, t)


def _bed_obj(pair):
    a, t = pair
    return {'a': a, 't': t, 'r': (round(max(0.0, (t - a) / float(t)), 4) if t >0 else None)}


def _bed_item_row(it):
    """<item> → 병상 레코드 (목록/구단위 폴백 공용)."""
    def _yn(tag):
        return (it.findtext(tag) or 'N').strip().upper().startswith('Y')
    return {
        'er':   _bed_obj(_sum_beds(it, _ER_TAGS)),
        'ward': _bed_obj(_sum_beds(it, _WARD_TAGS)),
        'icu':  _bed_obj(_sum_beds(it, _ICU_TAGS)),
        # 자원검색용 장비 보유 플래그
        #   TTM = 중심체온조절유도기(hvhypoayn), HBO = 고압산소치료기(hvoxyayn)
        'eq': {'crrt': _yn('hvcrrtayn'), 'ecmo': _yn('hvecmoayn'),
               'ttm': _yn('hvhypoayn'), 'hbo': _yn('hvoxyayn')},
        'tel3': (it.findtext('dutyTel3') or '').strip(),
        'upd': (it.findtext('hvidate') or '').strip(),
    }


def _fetch_sido_beds(sido):
    """단일 시/도 실시간 병상. 별칭 → 구단위(STAGE2) 순 폴백.
    ROOT-FIX: 기존에는 (a) 예외가 별칭 루프를 탈출시키고 (b) 목록조회에만
    있던 구단위 폴백이 병상조회에는 없어 광주/전남 병상이 통째로 비었다."""
    _union = sido in _UNION_SIDO
    _acc = {}
    for q in _sido_variants(sido):
        params = {'serviceKey': SERVICE_KEY, 'STAGE1': q, 'pageNo': '1', 'numOfRows': '400'}
        out = {}
        try:
            for it, _tc in _api_items(_http_get(API_URL, params=params, timeout=15), 'bed ' + q):
                hpid = (it.findtext('hpid') or '').strip()
                if not hpid:
                    continue
                out[hpid] = _bed_item_row(it)
        except Exception as _be:
            _ns_dbg(' bed %s: STAGE1="%s" 실패 %s' % (sido, q, _be))
            continue
        if out:
            if q != sido:
                _ns_dbg(' bed %s: 별칭 "%s" 로 %d건' % (sido, q, len(out)))
            if not _union:
                return out
            for _k, _v in out.items():
                _acc.setdefault(_k, _v)
    if _union and _acc:
        _ns_dbg(' bed %s: 별칭 합집합 → %d건' % (sido, len(_acc)))
        return _acc
    # ── STAGE1 이 0건만 주는 시/도 → STAGE2(시/군/구) 단위 합집합 ──
    gus = DISTRICTS.get(sido) or []
    for q in _sido_variants(sido):
        merged = {}
        for gu in gus:
            try:
                for it, _tc in _api_items(_http_get(API_URL, params={
                        'serviceKey': SERVICE_KEY, 'STAGE1': q, 'STAGE2': gu,
                        'pageNo': '1', 'numOfRows': '200'}, timeout=15),
                        'bed %s/%s' % (q, gu)):
                    hpid = (it.findtext('hpid') or '').strip()
                    if hpid and hpid not in merged:
                        merged[hpid] = _bed_item_row(it)
            except Exception as _ge:
                _ns_dbg(' bed %s/%s: 구단위 조회 실패 %s' % (sido, gu, _ge))
                continue
        if merged:
            _ns_dbg(' bed %s: 구단위 재조회로 %d건 복구 (STAGE1="%s")'
                    % (sido, len(merged), q))
            return merged
    # ③ [ROOT-FIX 2026-G1] STAGE1/STAGE2 필터가 고장난 시/도 → 전국 스냅샷에서
    #    해당 시/도 소속 hpid 만 추출 (병상 API 는 주소가 없으므로 로스터로 대조)
    try:
        ids = set(h['hpid'] for h in _nat_list_cached() if h.get('sido') == sido)
        if not ids:
            with _ALL_CACHE_LOCK:
                _base = _ALL_CACHE.get('data') or []
            ids = set(h['hpid'] for h in _base if h.get('sido') == sido)
        nb = _nat_beds_cached() if ids else {}
        pick = {k: v for k, v in nb.items() if k in ids}
    except Exception as _pe:
        pick = {}
        _ns_dbg(' bed %s: 전국스냅샷 폴백 예외 %s' % (sido, _pe))
    if pick:
        _ns_dbg(' bed %s: 전국스냅샷에서 %d건 복구' % (sido, len(pick)))
        return pick
    # ④ [ROOT-FIX 2026-G4] STAGE1 축을 아예 빼고 STAGE2(시군구)만으로 조회
    merged = {}
    for gu in (DISTRICTS.get(sido) or []):
        try:
            for it, _tc in _api_items(_http_get(API_URL, params={
                    'serviceKey': SERVICE_KEY, 'STAGE2': gu,
                    'pageNo': '1', 'numOfRows': '200'}, timeout=15),
                    'bed STAGE2=%s' % gu):
                hpid = (it.findtext('hpid') or '').strip()
                if hpid and hpid not in merged:
                    merged[hpid] = _bed_item_row(it)
        except Exception as _s2e:
            _ns_dbg(' bed %s/%s: STAGE2단독 실패 %s' % (sido, gu, _s2e))
    if merged:
        _ns_dbg(' bed %s: STAGE2단독으로 %d건 복구' % (sido, len(merged)))
        return merged
    _ns_dbg(' bed %s: 전 축(STAGE1·별칭·구단위·전국·STAGE2단독) 모두 0건' % sido)
    return {}


def _sido_beds_cached(sido, force=False):
    now = time.time()
    with _BED_LOCK:
        ent = _BED_CACHE.get(sido)
    if (not force) and ent and (now - ent[0] < _BED_TTL):
        return ent[1], True
    got = _fetch_sido_beds(sido)
    with _BED_LOCK:
        _BED_CACHE[sido] = (time.time(), got)
    return got, False


@flask_app.route('/api/beds')
def api_beds():
    sido = request.args.get('sido', '').strip()
    if not sido:
        return jsonify({'success': False, 'error': 'sido 필요'})
    try:
        beds, cached = _sido_beds_cached(sido, request.args.get('force') == '1')
        _ns_dbg('beds %s n=%d cached=%s' % (sido, len(beds), cached))
        return jsonify({'success': True, 'cached': cached, 'beds': beds})
    except Exception as e:
        _ns_dbg('beds %s FAIL %s' % (sido, e))
        return jsonify({'success': False, 'error': str(e)})


@flask_app.route('/api/hospital_detail')
def api_hospital_detail():
    """개별 병원 전체 세부 (병상 전 구간 + 장비 + 실시간 메시지)."""
    hpid  = request.args.get('hpid', '').strip()
    sido  = request.args.get('sido', '').strip()
    gugun = request.args.get('gugun', '').strip()
    if not hpid:
        return jsonify({'success': False, 'error': 'hpid 필요'})
    detail = None
    for q in ([sido] + _SIDO_ALIAS.get(sido, [])) if sido else []:
        try:
            params = {'serviceKey': SERVICE_KEY, 'STAGE1': q, 'pageNo': '1', 'numOfRows': '400'}
            if gugun:
                params['STAGE2'] = gugun
            root = _api_root(_http_get(API_URL, params=params, timeout=12), 'detail ' + q)
            for it in root.findall('.//item'):
                if (it.findtext('hpid') or '').strip() == hpid:
                    detail = parse_hospital_data(it)
                    break
            if detail is not None:
                break
        except Exception as e:
            _ns_dbg('detail %s (%s) 조회실패 %s' % (hpid, q, e))
    if detail is None:
        detail = {'emergency': {}, 'icu': {}, 'general': {}, 'isolation': {},
                  'other': {}, 'equipment': {}, 'update_time': ''}
    msgs = []
    try:
        r = _fetch_one_hospital_msgs(hpid)
        # (hpid, text) 튜플 / 문자열 / 리스트 어느 형태든 [{label,msg}] 로 정규화
        if isinstance(r, tuple):
            r = r[1]
        if isinstance(r, str):
            for ln in [x.strip() for x in r.split('\n') if x.strip()]:
                # 예외 없음 센티넬은 메시지로 표시하지 않는다
                if ln in ('정상', '정보 없음'):
                    continue
                # '[분류] [진료과목] 본문' → 좌측열=분류, 본문=[진료과목] 본문
                cat_, dept_, body_ = _split_msg_line(ln)
                lbl_, col_ = EXC_STYLE.get(cat_, (cat_ or '-', '#333'))
                msgs.append({'label': lbl_, 'color': col_, 'cat': cat_,
                             'dept': dept_,
                             'msg': (f'[{dept_}] {body_}' if dept_ else body_)})
        elif isinstance(r, (list, tuple)):
            for x in r:
                if isinstance(x, dict):
                    msgs.append({'label': x.get('label') or x.get('type') or '-',
                                 'msg': x.get('msg') or x.get('message') or str(x)})
                else:
                    _c, _d, _b = _split_msg_line(str(x))
                    _l, _col = EXC_STYLE.get(_c, (_c or '-', '#333'))
                    msgs.append({'label': _l, 'color': _col, 'cat': _c,
                                 'dept': _d,
                                 'msg': (f'[{_d}] {_b}' if _d else _b)})
    except Exception as me:
        _ns_dbg('detail msgs %s FAIL %s' % (hpid, me))
    _ns_dbg('detail %s msgs=%d' % (hpid, len(msgs)))
    return jsonify({'success': True, 'detail': detail, 'messages': msgs})


# ══════════════════════════════════════════════════════════════════
#  병상 포화도 + BACI v2
# ══════════════════════════════════════════════════════════════════
_SAT_CACHE = {'ts': 0.0, 'rows': None, 'at': '', 'fails': []}
_SAT_TTL   = 60
_SAT_BATCH = 4      # 동시 외부호출 상한 (Android 메모리/소켓 보호)
_SAT_LOCK  = _threading.Lock()


def _baci(er, ward, icu, adm):
    """상대 과밀점수 (자체 설계 heuristic — 검증된 지표 아님).
      D  = w_W*W + w_I*I     (하류 적체 압력)
      Dc = D**THETA          (고점유 구간 가중)
      L  = E / (1 - a*Dc)
    a, w_W, w_I, THETA 는 모두 '설계 상수'이며 실측 calibration 을 거치지 않았다.
    D=0 이면 L=E 로 수렴한다."""
    E = er['r']
    if E is None:
        return None
    W, I = ward['r'], icu['r']
    if W is not None and I is not None:
        D = _WI_W * W + _WI_I * I
    elif W is not None:
        D = W
    elif I is not None:
        D = I
    else:
        D = 0.0
    D = min(1.0, max(0.0, D))
    Dc = D ** _THETA
    L = E / (1.0 - adm * Dc)
    return round(min(_BACI_CAP, L), 4)


@flask_app.route('/api/bed_saturation')
def api_bed_saturation():
    force = request.args.get('force') == '1'
    now = time.time()
    with _SAT_LOCK:
        rows, ts, at, fl = (_SAT_CACHE['rows'], _SAT_CACHE['ts'],
                            _SAT_CACHE['at'], _SAT_CACHE['fails'])
    if (not force) and rows and (now - ts < _SAT_TTL):
        _ns_dbg('sat cache HIT n=%d age=%ds' % (len(rows), int(now - ts)))
        return jsonify({'success': True, 'cached': True, 'queried_at': at,
                        'build': BUILD_ID,
                        'failed': fl, 'count': len(rows), 'rows': rows})

    t0 = time.time()
    #  ROOT-FIX: [갱신] 은 병상캐시만 무효화하고 로스터는 12h 캐시를 그대로
    #   써서, 0건 시/도(광주 등)가 갱신으로 절대 복구되지 않았다.
    base, base_fails, _ = _all_hospitals(force)
    if not base:
        return jsonify({'success': False,
                        'error': '기관 목록 조회 실패: ' + '; '.join(base_fails[:3])})

    #  [ROOT-FIX 2026-G1] 포화도 화면은 로스터 캐시만 보므로, 0건 시/도가
    #   남아 있으면 그 지역(광주광역시 등)이 화면에서 통째로 사라진다.
    #   지역검색에만 있던 라이브 폴백을 여기에도 동일 적용한다.
    _cnt = {}
    for _h in base:
        _cnt[_h.get('sido')] = _cnt.get(_h.get('sido'), 0) + 1
    _miss_sd = [k for k in DISTRICTS if _cnt.get(k, 0) == 0]
    if _miss_sd:
        _ns_dbg('[SAT] 로스터 0건 시/도 %s → 라이브 복구 시도' % _miss_sd)
        _seen = set(h['hpid'] for h in base)
        _add = 0
        for _sd in _miss_sd:
            try:
                _got = _fetch_sido_list(_sd)
            except Exception as _se:
                _got = []
                _ns_dbg('[SAT] %s 라이브 복구 예외 %s' % (_sd, _se))
            for _h in _got:
                if _h['hpid'] not in _seen:
                    _seen.add(_h['hpid'])
                    base.append(_h)
                    _add += 1
            _ns_dbg('[SAT] repair %s: %d건' % (_sd, len(_got)))
        if _add:
            base = sorted(base, key=lambda x: (x['sido'], x['gugun'], x['name']))
            with _ALL_CACHE_LOCK:
                _ALL_CACHE['data'] = base
                _ALL_CACHE['ts'] = time.time()
                _ALL_CACHE['ttl'] = _ALL_CACHE_TTL_SHORT
                _ALL_CACHE['missing'] = [k for k in DISTRICTS
                                         if not any(h.get('sido') == k for h in base)]
            _ns_dbg('[SAT] 로스터 라이브 복구 +%d건 (총 %d)' % (_add, len(base)))

    _ns_dbg('sat fetch START force=%s (batch=%d)' % (force, _SAT_BATCH))
    beds, fails = {}, []
    sidos = list(DISTRICTS)
    for bi in range(0, len(sidos), _SAT_BATCH):
        chunk = sidos[bi:bi + _SAT_BATCH]
        futs = {_NET_POOL.submit(_sido_beds_cached, sd, force): sd for sd in chunk}
        for f in as_completed(futs):
            sd = futs[f]
            try:
                got, was_cached = f.result()
                beds.update(got)
                _ns_dbg(' sat %s: %d%s' % (sd, len(got), ' (cache)' if was_cached else ''))
            except Exception as e:
                fails.append('%s: %s' % (sd, e))
                _ns_dbg(' sat %s: FAIL %s' % (sd, e))
        futs.clear()

    #  [ROOT-FIX 2026-G1] STAGE1 이 죽은 시/도의 병상이 통째로 비는 것을
    #   막기 위해, 로스터에는 있으나 병상이 없는 hpid 를 전국 스냅샷으로 보충.
    try:
        _lack = [h['hpid'] for h in base if h['hpid'] not in beds]
        if _lack:
            _nb = _nat_beds_cached(force)
            _fill = 0
            for _hp in _lack:
                _row = _nb.get(_hp)
                if _row:
                    beds[_hp] = _row
                    _fill += 1
            _ns_dbg('[SAT] 병상 누락 %d건 중 전국스냅샷으로 %d건 보충'
                    % (len(_lack), _fill))
    except Exception as _fe:
        _ns_dbg('[SAT] 전국스냅샷 보충 예외 %s' % _fe)

    if not beds:
        _ns_dbg('sat FAILED %s' % fails[:3])
        return jsonify({'success': False,
                        'error': '실시간 병상 조회 실패: ' + '; '.join(fails[:3])})

    zero = {'a': 0, 't': 0, 'r': None}
    out = []
    for h in base:
        b = beds.get(h['hpid'])
        er   = b['er']   if b else dict(zero)
        ward = b['ward'] if b else dict(zero)
        icu  = b['icu']  if b else dict(zero)
        adm  = _ADM_RATE.get(h['level'], 0.25)
        out.append({
            'hpid': h['hpid'], 'name': h['name'], 'sido': h['sido'], 'gugun': h['gugun'],
            'level': h['level'], 'dutyAddr': h['dutyAddr'],
            'dutyTel1': h.get('dutyTel1', ''),
            'dutyTel3': (b['tel3'] if b and b['tel3'] else h.get('dutyTel3', '')),
            'emclsName': h.get('emclsName', ''),
            'er': er, 'ward': ward, 'icu': icu,
            'load': _baci(er, ward, icu, adm), 'adm': adm,
            'upd': (b['upd'] if b else ''),
        })
    out.sort(key=lambda r: (1 if r['er']['r'] is None else 0,
                            -(r['er']['r'] if r['er']['r'] is not None else 0), r['name']))
    at = datetime.now().strftime('%m/%d %H:%M:%S')
    with _SAT_LOCK:
        _SAT_CACHE['rows'] = out
        _SAT_CACHE['ts'] = time.time()
        _SAT_CACHE['at'] = at
        _SAT_CACHE['fails'] = fails
    try:
        beds.clear()
        del beds
        import gc as _gc
        _gc.collect()
    except Exception:
        pass
    with _ALL_CACHE_LOCK:
        _miss = list(_ALL_CACHE.get('missing') or [])
    if _miss:
        fails = list(fails) + ['로스터 0건 시/도: ' + ','.join(_miss)]
    _ns_dbg('sat DONE n=%d fails=%d miss=%d %.1fs'
            % (len(out), len(fails), len(_miss), time.time() - t0))
    return jsonify({'success': True, 'cached': False, 'queried_at': at,
                    'build': BUILD_ID,
                    'failed': fails, 'missing': _miss,
                    'count': len(out), 'rows': out})

# ══ 병원명검색 · 포화도 공용 캐시/디버그 ══
_ALL_CACHE      = {'ts': 0.0, 'data': None, 'ttl': 12 * 3600, 'missing': []}
_ALL_CACHE_TTL  = 12 * 3600
_ALL_CACHE_TTL_SHORT = 180      # 0건 시/도가 남은 경우 자동 재시도 주기
_ALL_CACHE_LOCK = _threading.Lock()

#  [일원화 2026-H1] er_name_search.log 별도 파일 폐지 → ermon.log 단일화
_NS_IO_LOCK = _threading.Lock()


def _ns_dbg(msg):
    """조회 파이프라인 실시간 로그. 통합 로그(ermon.log)에만 기록한다."""
    try:
        ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]
    except Exception:
        ts = '??:??:??'
    try:
        _DEBUG_LINES.append('%s [NS] %s' % (ts, msg))
        if len(_DEBUG_LINES) > 300:
            _DEBUG_LINES.pop(0)
    except Exception:
        pass
    _ulog('NS', msg)


def _split_gugun(sido, addr):
    """주소에서 시/군/구 추출. DISTRICTS 우선 매칭 → 토큰 폴백."""
    toks = (addr or '').split()
    cand = DISTRICTS.get(sido, [])
    for t in toks[:4]:
        if t in cand:
            return t
    for t in toks[:4]:
        if t != sido and (t.endswith('시') or t.endswith('군') or t.endswith('구')):
            return t
    return cand[0] if len(cand) == 1 else ''


def _get_hospital_level(emcls, name=''):
    """응급의료기관 분류코드로 권역/센터/기관 구분"""
    if emcls in ('G001', 'G002') or '권역' in name:
        return '권역'
    if emcls in ('G003', 'G004', 'G006') or '센터' in name:
        return '센터'
    return '기관'

# ══════════════════════════════════════════════════════════════════
#  진료과목 D코드 → 한국어명 매핑 (API 매뉴얼 33/45페이지)
#  메시지 API에서 symTypCodMag가 비어있고 symTypCod가 D코드일 때 사용
# ══════════════════════════════════════════════════════════════════
D_CODE_MAP = {
    'D001': '내과', 'D002': '소아청소년과', 'D003': '신경과',
    'D004': '정신건강의학과', 'D005': '피부과', 'D006': '외과',
    'D007': '흉부외과', 'D008': '정형외과', 'D009': '신경외과',
    'D010': '성형외과', 'D011': '산부인과', 'D012': '안과',
    'D013': '이비인후과', 'D014': '비뇨기과', 'D016': '재활의학과',
    'D017': '마취통증의학과','D018': '영상의학과', 'D019': '치료방사선과',
    'D020': '임상병리과', 'D021': '해부병리과', 'D022': '가정의학과',
    'D023': '핵의학과', 'D024': '응급의학과', 'D026': '치과',
    'D034': '구강악안면외과',
}


# ══════════════════════════════════════════════════════════════════
#  중증질환 수용가능 키오스크 코드 → 항목명 매핑 (getSrsillDissAceptncPosblInfoInqire)
# ══════════════════════════════════════════════════════════════════
MKIOSK_MAP = {
    # 공식 매핑(NIA OpenAPI 활용가이드 V4, 2025-02-26 / 실측 확인):
    #   Ty1~Ty27 = 중증응급질환 27종, Ty28 = 응급실
    'MKioskTy1': '[재관류중재술] 심근경색',
    'MKioskTy2': '[재관류중재술] 뇌경색',
    'MKioskTy3': '[뇌출혈수술] 거미막하출혈',
    'MKioskTy4': '[뇌출혈수술] 거미막하출혈 외',
    'MKioskTy5': '[대동맥응급] 흉부',
    'MKioskTy6': '[대동맥응급] 복부',
    'MKioskTy7': '[담낭담관질환] 담낭질환',
    'MKioskTy8': '[담낭담관질환] 담도포함질환',
    'MKioskTy9': '[복부응급수술] 비외상',
    'MKioskTy10': '[장중첩/폐색] 영유아',
    'MKioskTy11': '[응급내시경] 성인 위장관',
    'MKioskTy12': '[응급내시경] 영유아 위장관',
    'MKioskTy13': '[응급내시경] 성인 기관지',
    'MKioskTy14': '[응급내시경] 영유아 기관지',
    'MKioskTy15': '[저체중출생아] 집중치료',
    'MKioskTy16': '[산부인과응급] 분만',
    'MKioskTy17': '[산부인과응급] 산과수술',
    'MKioskTy18': '[산부인과응급] 부인과수술',
    'MKioskTy19': '[중증화상] 전문치료',
    'MKioskTy20': '[사지접합] 수족지접합',
    'MKioskTy21': '[사지접합] 수족지접합 외',
    'MKioskTy22': '[응급투석] HD',
    'MKioskTy23': '[응급투석] CRRT',
    'MKioskTy24': '[정신과적응급] 폐쇄병동입원',
    'MKioskTy25': '[안과적수술] 응급',
    'MKioskTy26': '[영상의학혈관중재] 성인',
    'MKioskTy27': '[영상의학혈관중재] 영유아',
    'MKioskTy28': '응급실 수용'
}

# ══════════════════════════════════════════════════════════════════
#  Y코드 → 한국어명 매핑 (API가 Y코드를 반환하는 경우 대비)
# ══════════════════════════════════════════════════════════════════
Y_CODE_MAP = {
    'Y000': '응급실',
    'Y0010': '[재관류중재술] 심근경색',
    'Y0020': '[재관류중재술] 뇌경색',
    'Y0031': '[뇌출혈수술] 거미막하출혈',
    'Y0032': '[뇌출혈수술] 거미막하출혈 외',
    'Y0041': '[대동맥응급] 흉부',
    'Y0042': '[대동맥응급] 복부',
    'Y0051': '[담낭담관질환] 담낭질환',
    'Y0052': '[담낭담관질환] 담도포함질환',
    'Y0060': '[복부응급수술] 비외상',
    'Y0070': '[장중첩/폐색] 영유아',
    'Y0081': '[응급내시경] 성인 위장관',
    'Y0082': '[응급내시경] 영유아 위장관',
    'Y0091': '[응급내시경] 성인 기관지',
    'Y0092': '[응급내시경] 영유아 기관지',
    'Y0100': '[저출생체중아] 집중치료',
    'Y0111': '[산부인과응급] 분만',
    'Y0112': '[산부인과응급] 산과수술',
    'Y0113': '[산부인과응급] 부인과수술',
    'Y0120': '[중증화상] 전문치료',
    'Y0131': '[사지접합] 수족지접합',
    'Y0132': '[사지접합] 수족지접합 외',
    'Y0141': '[응급투석] HD',
    'Y0142': '[응급투석] CRRT',
    'Y0150': '[정신과적응급] 폐쇄병동입원',
    'Y0160': '[안과적수술] 응급',
    'Y0171': '[영상의학혈관중재] 성인',
    'Y0172': '[영상의학혈관중재] 영유아',
}

# ══════════════════════════════════════════════════════════════════
#  예외상황 메시지 처리
# ══════════════════════════════════════════════════════════════════

# 예외상황 분류 → (표시라벨, 색상). 조회화면·자세히 팝업 공통 사용
EXC_STYLE = {
    '수용불가': ('[수용 불가능]', '#dc3545'),
    '수용가능': ('[ 수용 가능 ]', '#28a745'),
    '문의필요': ('[ 문의 필요 ]', '#e67e00'),
}


def _dept_color(text, color):
    """'[진료과목] 본문' 서식.

    [2026-H2] 색상 반전: [진료과목] 은 그룹 색(수용가능/수용불가/문의필요)과
    동일하게, 이후 세부내용은 검정으로 표시한다(기존과 정반대).
    → 과목이 먼저 눈에 들어오고 본문은 가독성 높은 검정으로 읽힌다.
    """
    m = _re_msg.match(text or '')
    if not m:
        return '<span style="color:#000;">%s</span>' % _html_escape(text or '')
    return ('<span style="color:%s;font-weight:700;">[%s]</span> '
            '<span style="color:#000;">%s</span>'
            % (color,
               _html_escape((m.group(1) or '').strip()),
               _html_escape((m.group(2) or '').strip())))


def _categorize_exception(label, msg):
    """메시지를 카테고리로 분류: 수용불가 / 수용가능 / 문의필요"""
    full = f"{label} {msg}".strip()
    if any(kw in msg for kw in ['문의', '확인 필요', '확인요', '연락', '전화']):
        return '문의필요'
    if ('가능' in full and
            not any(kw in full for kw in ['불가', '부족', '제한', '불능', '불가능'])):
        return '수용가능'
    return '수용불가'


def _resolve_type_label(sym_typ_cod_mag, sym_typ_cod):
    """symTypCodMag / symTypCod 로부터 표시용 라벨을 결정.
    우선순위:
      1) symTypCodMag 가 Y코드 → Y_CODE_MAP 변환
      2) symTypCodMag 가 D코드 → D_CODE_MAP 변환
      3) symTypCodMag 가 일반 문자열 → 그대로 사용
      4) symTypCodMag 가 비어있음 → symTypCod 로 Y/D 코드 조회
    """
    label = (sym_typ_cod_mag or '').strip()

    if label:
        # Y코드 형식: 'Y' + 최대 5자리 숫자
        if label.startswith('Y') and len(label) <= 6 and label[1:].isdigit():
            label = Y_CODE_MAP.get(label, label)
        # D코드 형식: 'D' + 정확히 3자리 숫자
        elif label.startswith('D') and len(label) == 4 and label[1:].isdigit():
            label = D_CODE_MAP.get(label, label)
        # 그 외 문자열은 그대로 사용 (이미 한국어 과목명 포함)
    else:
        # symTypCodMag 비어있음 → symTypCod 로 Y/D 코드 조회
        code = (sym_typ_cod or '').strip()
        label = Y_CODE_MAP.get(code, '') or D_CODE_MAP.get(code, '')

    return label


_re_msg = __import__('re').compile(r'^\[([^\]]*)\]\s*(.*)$', __import__('re').S)
_html_escape = __import__('html').escape


def _clean_msg(sym_blk_msg):
    """symBlkMsg에서 [응급] 접두사 제거 (이슈7)"""
    msg = (sym_blk_msg or '').strip()
    for prefix in ('[응급] ', '[응급]'):
        if msg.startswith(prefix):
            msg = msg[len(prefix):].strip()
            break
    return msg


def _split_msg_line(ln):
    """'[수용불가] [성형외과] 본문' → ('수용불가', '성형외과', '본문').
    형식이 다르면 가능한 만큼만 분해한다."""
    m1 = _re_msg.match(ln or '')
    if not m1:
        return '', '', (ln or '').strip()
    cat = (m1.group(1) or '').strip()
    rest = (m1.group(2) or '').strip()
    m2 = _re_msg.match(rest)
    if m2:
        return cat, (m2.group(1) or '').strip(), (m2.group(2) or '').strip()
    return cat, '', rest


def _fetch_one_hospital_msgs(hpid):
    """단일 병원 예외상황 메시지 조회 (페이지네이션 포함)"""
    try:
        exception_msgs = []
        page = 1
        while True:
            params = {
                'serviceKey': SERVICE_KEY,
                'HPID': hpid.strip(),
                'pageNo': str(page),
                'numOfRows': '100',
            }
            resp = _http_get(MSG_API_URL, params=params, timeout=8)
            if resp.status_code != 200:
                break
            root = ET.fromstring(resp.content)
            items = root.findall('.//item')
            if not items:
                break

            total_count_text = root.findtext('.//totalCount') or '0'
            try:
                total_count = int(total_count_text)
            except ValueError:
                total_count = 0

            for item in items:
                sym_blk_msg     = (item.findtext('symBlkMsg') or '').strip()
                sym_typ_cod_mag = (item.findtext('symTypCodMag') or '').strip()
                sym_typ_cod     = (item.findtext('symTypCod') or '').strip()
                sym_blk_msg_typ = (item.findtext('symBlkMsgTyp') or '').strip()
                sym_out_dsp_yon = (item.findtext('symOutDspYon') or '').strip()
                # 명세 미기재이나 실제 응답에 존재하는 진료과목 필드 (실측 확인)
                trt_prt_cod_mag = (item.findtext('trtPrtCodMag') or '').strip()

                # 모든 태그 덤프 (디버그)
                all_tags = {child.tag: (child.text or '') for child in item}
                print(f"[MSG_RAW] {hpid} p{page} | "
                      f"symTypCod={sym_typ_cod!r} symTypCodMag={sym_typ_cod_mag!r} "
                      f"symBlkMsg={sym_blk_msg!r} msgTyp={sym_blk_msg_typ!r} "
                      f"DspYon={sym_out_dsp_yon!r} trtPrt={trt_prt_cod_mag!r} "
                      f"| all_tags={all_tags}")

                # Y/D 코드 → 한국어명 변환
                label = _resolve_type_label(sym_typ_cod_mag, sym_typ_cod)
                # [응급] 접두사 제거 (이슈7)
                clean_msg = _clean_msg(sym_blk_msg)

                print(f"[MSG_PROC] label_raw={label!r} clean_msg={clean_msg!r}")

                # ── 표기 라벨 결정 ─────────────────────────────
                #  1순위 진료과목(trtPrtCodMag)  예: 성형외과
                #  2순위 중증질환명/응급실(symTypCodMag→label)
                #  둘 다 없으면 '응급실'
                #  (진료과목이 있으면 '응급실' 과 중복 표기하지 않는다)
                dept = trt_prt_cod_mag or label or '응급실'

                if not clean_msg and not dept:
                    print(f"[MSG_PROC] ->SKIPPED (both empty)")
                    continue

                # 최종 content 구성 — [진료과목] 접두.
                #  본문이 이미 '[..]' 로 시작하면 과목 표기가 중복되므로
                #  접두를 붙이지 않고 본문의 대괄호 하나만 남긴다.
                if not clean_msg:
                    content = f"[{dept}]"
                elif clean_msg.startswith('['):
                    content = clean_msg
                else:
                    content = f"[{dept}] {clean_msg}"

                cat = _categorize_exception(label, clean_msg)
                exception_msgs.append(f"[{cat}] {content}")

            # 페이지네이션: 100개 미만이면 마지막 페이지
            if len(items) < 100 or page * 100 >= total_count:
                break
            page += 1

        print(f"[메시지] {hpid}: {len(exception_msgs)}개 수집 (페이지 {page})")
        if exception_msgs:
            return hpid, '\n'.join(list(dict.fromkeys(exception_msgs)))
        else:
            return hpid, ' 정상'
    except Exception as e:
        print(f"예외상황 조회 오류 (hpid={hpid}): {e}")
        return hpid, ' 정상'


def _fetch_messages_direct(hpids):
    """
    병렬로 예외상황 메시지 조회 (HTTP 자기호출 없이 직접).
    반환값: {hpid: ' 정상' | '[수용불가] ...\n[문의필요] ...' }
    """
    messages = {}
    with ThreadPoolExecutor(max_workers=min(5, len(hpids))) as ex:
        futures = {ex.submit(_fetch_one_hospital_msgs, hpid): hpid for hpid in hpids}
        for future in as_completed(futures):
            try:
                hpid, result = future.result()
                messages[hpid] = result
            except Exception as e:
                hpid = futures[future]
                print(f"병렬 메시지 조회 실패 ({hpid}): {e}")
                messages[hpid] = ' 정상'
    return messages


@flask_app.route('/api/messages', methods=['POST'])
def get_messages():
    try:
        hpids = request.json.get('hpids', [])
        if not hpids:
            return jsonify({'success': False, 'error': '병원 ID가 필요합니다.'})
        messages = _fetch_messages_direct(hpids)
        return jsonify({'success': True, 'messages': messages})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})




def _fetch_basic_info(hpid):
    """단일 병원 기본정보(dutyInf) 조회 → (hpid, duty_inf) 반환"""
    try:
        params = {'serviceKey': SERVICE_KEY, 'HPID': hpid.strip(), 'numOfRows': '1'}
        resp = _http_get(
            'https://apis.data.go.kr/B552657/ErmctInfoInqireService/getEgytBassInfoInqire',
            params=params, timeout=8)
        if resp.status_code != 200:
            return hpid, ''
        root = ET.fromstring(resp.content)
        duty_inf = (root.findtext('.//dutyInf') or '').strip()
        return hpid, duty_inf
    except Exception:
        return hpid, ''


def _fetch_kiosk_info(sido, gugun):
    """지역 중증질환 수용가능정보 조회 → {hpid: {code: {val, msg}, ...}} 반환"""
    try:
        root = _region_api_root(
            'https://apis.data.go.kr/B552657/ErmctInfoInqireService/getSrsillDissAceptncPosblInfoInqire',
            sido, gugun, timeout=10, ctx='kiosk')
        if root is None:
            return {}
        result = {}
        for item in root.findall('.//item'):
            hpid = (item.findtext('hpid') or '').strip()
            if not hpid:
                continue
            data = {}
            for code in MKIOSK_MAP:
                val = (item.findtext(code) or '').strip()
                msg = (item.findtext(code + 'Msg') or '').strip()
                if val:
                    data[code] = {'val': val, 'msg': msg}
            result[hpid] = data
        return result
    except Exception:
        return {}

# ══════════════════════════════════════════════════════════════════
#  응급실 병상 상시 알림 모니터 (Pydroid 3 / Kivy APK 겸용)
#  - 비교화면에서 병원 1곳 선택() → Flask 백그라운드 스레드가
#    선택한 주기마다 응급실 일반(hvec) 병상을 조회해
#    안드로이드 상단 알림을 "병원명 x/y(z%)" 형식으로 무음 갱신한다.
#  - jnius로 NotificationManager를 직접 호출 → APK 빌드 불필요.
#    PC 실행 시에는 알림 대신 로그로만 기록된다.
# ══════════════════════════════════════════════════════════════════
_BED_NOTIFY_ID      = 5501
_BED_NOTIFY_CHANNEL = 'bed_monitor'
_bed_notify_lock  = threading.Lock()
_bed_notify_state = {
    'running': False,
    'hospitals': [], # [{'hpid','name','sido','gugun'}, ...] (복수 선택)
    'iv_sec': 180, 'h_param': '', 'thread': None,
    'stop_event': None, 'kick_event': None, # kick: '지금 갱신' 버튼용
    'line_map': {},           # hpid → 마지막 표시줄
    'last_line': '', 'last_ts': '', 'next_epoch': 0.0,
    'mode': 'notify', # 'notify'(알림) | 'overlay'(상단 오버레이)
}
# 상단 반투명 오버레이 뷰 참조 (UI 스레드에서만 조작)
_overlay_refs = {'view': None, 'wm': None, 'params': None}
_UI_RUNNABLE_REFS = []   # PythonJavaClass Runnable GC 방지용
_last_compare_h = ['']      # 최근 /compare 병원 구성 (PiP→브라우저 복귀용)
_wake_lock = [None]         # 모니터용 PARTIAL_WAKE_LOCK


def _get_android_context():
    """Android Context 획득 — p4a(Kivy APK)와 Pydroid 3 모두 지원."""
    try:
        from jnius import autoclass
    except Exception:
        return None
    try:  # ① Kivy APK (python-for-android)
        _PA = autoclass('org.kivy.android.PythonActivity')
        if _PA.mActivity:
            return _PA.mActivity
    except Exception:
        pass
    try:  # ② Pydroid 3 등: ActivityThread 경유로 애플리케이션 컨텍스트 획득
        _AT = autoclass('android.app.ActivityThread')
        app = _AT.currentApplication()
        if app is None:
            app = _AT.currentActivityThread().getApplication()
        return app
    except Exception:
        return None


def _bed_notifications_status():
    """(사용가능 여부, 안내문) — Android 13+ 알림 허용 여부 점검"""
    ctx = _get_android_context()
    if ctx is None:
        return False, 'Android 환경이 아니어서 알림 대신 로그로만 기록됩니다.'
    try:
        from jnius import autoclass
        Context = autoclass('android.content.Context')
        nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE)
        try:
            if not nm.areNotificationsEnabled():
                return False, ('알림이 꺼져 있습니다. 휴대폰 설정 → 앱 → Pydroid 3 → '
                               '알림 허용 후 을 다시 눌러주세요.')
        except Exception:
            pass  # API < 24
        return True, ''
    except Exception as e:
        return False, f'알림 시스템 접근 실패: {e}'


def _post_bed_notification(title, text, big_text=None, sub_text=None,
                           when_ms=None, count_down=False, kick_action=False,
                           timeout_ms=None):
    """상시(ongoing)·무음 알림 게시/갱신. 같은 ID로 게시해 제자리 갱신된다.
    big_text: 펼침 시 여러 줄(BigTextStyle), when_ms+count_down: 다음 갱신까지
    카운트다운 크로노미터, kick_action: '지금 갱신' 버튼(로컬 URL 경유)."""
    ctx = _get_android_context()
    if ctx is None:
        _dlog(f'[알림] (PC) {title} | {text}')
        return False
    try:
        from jnius import autoclass
        Context = autoclass('android.content.Context')
        VERSION = autoclass('android.os.Build$VERSION')
        nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE)
        if VERSION.SDK_INT >= 26:
            NotificationChannel = autoclass('android.app.NotificationChannel')
            NotificationManager = autoclass('android.app.NotificationManager')
            ch = NotificationChannel(_BED_NOTIFY_CHANNEL, '응급실 병상 모니터',
                                     NotificationManager.IMPORTANCE_LOW)  # 무음 채널
            ch.setShowBadge(False)
            nm.createNotificationChannel(ch)
            Builder = autoclass('android.app.Notification$Builder')
            b = Builder(ctx, _BED_NOTIFY_CHANNEL)
        else:
            Builder = autoclass('android.app.Notification$Builder')
            b = Builder(ctx)
            try:
                b.setPriority(-1)  # PRIORITY_LOW
            except Exception:
                pass
        b.setSmallIcon(ctx.getApplicationInfo().icon)
        b.setContentTitle(title)      # "병원명 x/y(z%)"
        b.setContentText(text)        # "응급실 일반 · HH:MM 갱신"
        b.setOngoing(True)            # 스와이프로 지워지지 않게 (항상 표시)
        b.setOnlyAlertOnce(True)      # 갱신 때마다 소리/진동 없음
        if sub_text:
            try:
                b.setSubText(sub_text)
            except Exception:
                pass
        if big_text:
            try:  # 펼치면 병원별 한 줄씩 (API 16+)
                BigTextStyle = autoclass('android.app.Notification$BigTextStyle')
                b.setStyle(BigTextStyle().bigText(big_text))
            except Exception as _bt:
                _dlog(f'[알림] BigText 실패(무시): {_bt}')
        if when_ms:
            try:  # 다음 갱신까지 카운트다운 (API 24+)
                b.setWhen(int(when_ms))
                b.setShowWhen(True)
                b.setUsesChronometer(True)
                if count_down and VERSION.SDK_INT >= 24:
                    b.setChronometerCountDown(True)
            except Exception as _cd:
                _dlog(f'[알림] 카운트다운 실패(무시): {_cd}')
        if timeout_ms:
            try:   #  프로세스가 강제종료돼도 OS가 만료 시 알림을 자동 제거 (API 26+)
                if VERSION.SDK_INT >= 26:
                    b.setTimeoutAfter(int(timeout_ms))
            except Exception as _to:
                _dlog(f'[알림] 자동만료 설정 실패(무시): {_to}')
        # 알림 탭 → 브라우저로 비교화면 열기
        try:
            Intent        = autoclass('android.content.Intent')
            Uri           = autoclass('android.net.Uri')
            PendingIntent = autoclass('android.app.PendingIntent')
            h = _bed_notify_state.get('h_param', '')
            url = ('http://127.0.0.1:5000/compare?h=' + h) if h else 'http://127.0.0.1:5000/'
            it = Intent(Intent.ACTION_VIEW, Uri.parse(url))
            it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            flags = PendingIntent.FLAG_UPDATE_CURRENT
            if VERSION.SDK_INT >= 23:
                flags |= PendingIntent.FLAG_IMMUTABLE
            b.setContentIntent(PendingIntent.getActivity(ctx, 0, it, flags))
            if kick_action:
                try:  # '지금 갱신' 버튼 → 로컬 킥 URL (브라우저 경유, 리시버 불필요)
                    ki = Intent(Intent.ACTION_VIEW,
                                Uri.parse('http://127.0.0.1:5000/api/bed_notify_kick'))
                    ki.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    kpi = PendingIntent.getActivity(ctx, 1, ki, flags)
                    b.addAction(ctx.getApplicationInfo().icon, '⟳ 지금 갱신', kpi)
                    ci = Intent(Intent.ACTION_VIEW,
                                Uri.parse('http://127.0.0.1:5000/api/bed_notify_close'))
                    ci.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    cpi = PendingIntent.getActivity(ctx, 2, ci, flags)
                    b.addAction(ctx.getApplicationInfo().icon, ' 닫기', cpi)
                    _dlog('[알림] 액션 부착: ⟳ 지금갱신 / 닫기')
                except Exception as _ka:
                    _dlog(f'[알림] 갱신 버튼 실패(무시): {_ka}')
        except Exception as _pe:
            _dlog(f'[알림] 탭 인텐트 설정 실패(무시): {_pe}')
        nm.notify(_BED_NOTIFY_ID, b.build())
        return True
    except Exception as e:
        _dlog(f'[알림] 게시 실패: {e}')
        return False


def _cancel_bed_notification():
    ctx = _get_android_context()
    if ctx is None:
        return
    try:
        from jnius import autoclass
        Context = autoclass('android.content.Context')
        ctx.getSystemService(Context.NOTIFICATION_SERVICE).cancel(_BED_NOTIFY_ID)
    except Exception as e:
        _dlog(f'[알림] 취소 실패(무시): {e}')


# ──────────────────────────────────────────────────────────────────
#  상단 반투명 오버레이 (다른 앱 위에 표시)
#  - WindowManager + TYPE_APPLICATION_OVERLAY 를 jnius로 직접 사용.
#  - 뷰 생성/갱신/제거는 반드시 안드로이드 메인(UI) 스레드에서 수행.
#  - 호스트 앱(Pydroid 3 등)이 SYSTEM_ALERT_WINDOW 권한을 가져야 하며,
#    가능 여부를 런타임에 자가진단해 사용자에게 정확히 안내한다.
# ──────────────────────────────────────────────────────────────────
def _run_on_ui(fn, wait=True, timeout=4.0):
    """안드로이드 메인(UI) 스레드에서 fn 실행 → (완료여부, 오류문자열|None)"""
    try:
        from jnius import autoclass, PythonJavaClass, java_method
    except Exception:
        try:
            fn()
            return True, None
        except Exception as e:
            return False, str(e)

    done = threading.Event()
    box = {'err': None}

    class _UIRun(PythonJavaClass):
        __javainterfaces__ = ['java/lang/Runnable']
        __javacontext__ = 'app'

        @java_method('()V')
        def run(self):
            try:
                fn()
            except Exception as e:
                box['err'] = str(e)
            finally:
                done.set()

    r = _UIRun()
    _UI_RUNNABLE_REFS.append(r)   # 실행 전 GC 방지
    try:
        Handler = autoclass('android.os.Handler')
        Looper  = autoclass('android.os.Looper')
        Handler(Looper.getMainLooper()).post(r)
    except Exception as e:
        try:
            _UI_RUNNABLE_REFS.remove(r)
        except ValueError:
            pass
        return False, str(e)
    if wait:
        done.wait(timeout)
        if done.is_set():
            try:
                _UI_RUNNABLE_REFS.remove(r)
            except ValueError:
                pass
    return (done.is_set() or not wait), box['err']


def _overlay_permission_state():
    """오버레이 가능 여부 자가진단.
    반환: ('ok'|'no_context'|'not_granted'|'not_declared'|'error', 안내문)"""
    ctx = _get_android_context()
    if ctx is None:
        return 'no_context', 'Android 환경이 아니어서 오버레이 대신 로그로만 기록됩니다.'
    try:
        from jnius import autoclass
        VERSION = autoclass('android.os.Build$VERSION')
        if VERSION.SDK_INT < 23:
            return 'ok', ''
        Settings = autoclass('android.provider.Settings')
        if Settings.canDrawOverlays(ctx):
            return 'ok', ''
        # 허용되지 않음 → 호스트 앱이 권한을 선언했는지로 원인 구분
        pkg = str(ctx.getPackageName())
        declared = False
        try:
            PackageManager = autoclass('android.content.pm.PackageManager')
            pi = ctx.getPackageManager().getPackageInfo(pkg, PackageManager.GET_PERMISSIONS)
            for p in (pi.requestedPermissions or []):
                if str(p) == 'android.permission.SYSTEM_ALERT_WINDOW':
                    declared = True
                    break
        except Exception:
            declared = True   # 판정 실패 시 설정 안내 쪽으로 진행
        if declared:
            return 'not_granted', ("'다른 앱 위에 표시' 권한이 필요합니다. "
                                   '방금 열린 설정에서 허용한 뒤 을 다시 눌러주세요.')
        return 'not_declared', (f'{pkg} 앱이 오버레이 권한(SYSTEM_ALERT_WINDOW)을 '
                                '선언하지 않아 일반 설정으로는 허용할 수 없습니다. '
                                '알림() 방식을 사용하거나 ADB로 권한을 부여해야 합니다. '
                                f'(PC: adb shell appops set {pkg} '
                                'SYSTEM_ALERT_WINDOW allow)')
    except Exception as e:
        return 'error', f'권한 확인 실패: {e}'


def _request_overlay_permission():
    """'다른 앱 위에 표시' 설정 화면 열기 (해당 앱 페이지로 직행)"""
    ctx = _get_android_context()
    if ctx is None:
        return
    try:
        from jnius import autoclass
        Settings = autoclass('android.provider.Settings')
        Intent   = autoclass('android.content.Intent')
        Uri      = autoclass('android.net.Uri')
        it = Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse('package:' + str(ctx.getPackageName())))
        it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        ctx.startActivity(it)
    except Exception as e:
        _dlog(f'[오버레이] 권한 설정 열기 실패: {e}')


def _overlay_show(text):
    """상태바 바로 아래 중앙에 반투명 스트립 생성/갱신. 성공 시 True."""
    ctx = _get_android_context()
    if ctx is None:
        _dlog(f'[오버레이] (PC) {text}')
        return True

    tv = _overlay_refs.get('view')
    if tv is not None:                       # 이미 떠 있음 → 텍스트만 갱신
        ok, err = _run_on_ui(lambda: tv.setText(text))
        if err:
            _dlog(f'[오버레이] 갱신 실패: {err}')
        return ok and not err

    def _create():
        from jnius import autoclass
        Context          = autoclass('android.content.Context')
        TextView         = autoclass('android.widget.TextView')
        Gravity          = autoclass('android.view.Gravity')
        Color            = autoclass('android.graphics.Color')
        PixelFormat      = autoclass('android.graphics.PixelFormat')
        LayoutParams     = autoclass('android.view.WindowManager$LayoutParams')
        VERSION          = autoclass('android.os.Build$VERSION')
        GradientDrawable = autoclass('android.graphics.drawable.GradientDrawable')

        density = ctx.getResources().getDisplayMetrics().density
        def dp(v):
            return int(v * density + 0.5)

        v = TextView(ctx)
        v.setText(text)
        v.setTextColor(Color.WHITE)
        v.setTextSize(12.5)
        v.setSingleLine(False)   # 복수 병원 = 여러 줄
        v.setPadding(dp(12), dp(4), dp(12), dp(5))
        bg = GradientDrawable()
        bg.setColor(Color.argb(150, 0, 0, 0))          # 반투명 검정
        bg.setCornerRadius(float(dp(14)))
        v.setBackground(bg)

        wtype = (LayoutParams.TYPE_APPLICATION_OVERLAY if VERSION.SDK_INT >= 26
                 else LayoutParams.TYPE_PHONE)
        flags = (LayoutParams.FLAG_NOT_FOCUSABLE
                 | LayoutParams.FLAG_NOT_TOUCHABLE)     # 터치 통과 → 조작 방해 없음
        p = LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT,
                         wtype, flags, PixelFormat.TRANSLUCENT)
        p.gravity = Gravity.TOP | Gravity.CENTER_HORIZONTAL
        p.x = 0
        p.y = dp(2)                                     # 상태바(시계) 바로 아래
        wm = ctx.getSystemService(Context.WINDOW_SERVICE)
        wm.addView(v, p)
        _overlay_refs.update({'view': v, 'wm': wm, 'params': p})

    ok, err = _run_on_ui(_create)
    if err or not ok:
        _dlog(f'[오버레이] 생성 실패: {err or "UI 스레드 응답 없음"}')
        _overlay_refs.update({'view': None, 'wm': None, 'params': None})
        return False
    return True


def _overlay_remove():
    """오버레이 제거 (없으면 무시) — WebView인 경우 destroy까지"""
    v  = _overlay_refs.get('view')
    wm = _overlay_refs.get('wm')
    was_web = _overlay_refs.get('web')
    _overlay_refs.update({'view': None, 'wm': None, 'params': None, 'web': False})
    if v is None or wm is None:
        return

    def _rm():
        wm.removeView(v)
        if was_web:
            try:
                v.destroy()
            except Exception:
                pass
    _ok, err = _run_on_ui(_rm)
    if err:
        _dlog(f'[오버레이] 제거 실패(무시): {err}')


def _fetch_bed_line(hpid, sido, gugun, name_hint=''):
    """응급실 일반(hvec) 가용/전체 조회 → '병원명 x/y(z%)' 문자열 (실패 시 None).
    전체(HVS01) 누락 시 마지막 양수값 폴백 — pip_data와 동일 정책."""
    try:
        root = _region_api_root(API_URL, sido, gugun, timeout=10, ctx='bedline')
        if root is None:
            return None
        for item in root.findall('.//item'):
            if (item.findtext('hpid') or '').strip() != hpid:
                continue
            name  = (item.findtext('dutyName') or '').strip() or name_hint or hpid
            _raw_avail = item.findtext('hvec')
            _has_avail = _raw_avail is not None and str(_raw_avail).strip() != ''
            avail = safe_int(_raw_avail)
            t_raw = get_hvs(item, 'HVS01')
            prev  = _pip_bed_total_cache.get(hpid, {})
            if t_raw >0:
                prev['hvec_t'] = t_raw
                total = t_raw
            else:
                total = prev.get('hvec_t', 0)
            _pip_bed_total_cache[hpid] = prev
            # 음수는 과밀(정원 초과)을 뜻하는 실제 값이므로 그대로 표기한다.
            # 태그 자체가 비었을 때만 '정보없음'.
            if not _has_avail:
                return f'{name} 정보없음'
            if total >0:
                pct = round(avail / total * 100)
                return f'{name} {avail}/{total}({pct}%)'
            return f'{name} {avail}/-'
        return None  # 응답에 해당 병원 없음
    except Exception as e:
        _dlog(f'[알림] 병상 조회 오류: {e}')
        return None


#  [일원화 2026-H1] bed_monitor.json 폐지 → ermon_state.json 'monitor' 섹션
def _save_monitor_cfg():
    try:
        st = _bed_notify_state
        _state_set('monitor', {
            'hospitals': st['hospitals'], 'iv': st['iv_sec'],
            'mode': st['mode'], 'h': st.get('h_param', ''),
            # 오버레이는 자동 복원 대상에서 제외한다.
            # (앱 재시작 시 사용자가 선택하지 않은 창이 뜨는 문제)
            'resume': st['mode'] != 'overlay',
            'ts': time.time()})
    except Exception as e:
        _dlog(f'[알림] 설정 저장 실패(무시): {e}')


def _clear_monitor_cfg():
    try:
        _state_set('monitor', None)
    except Exception:
        pass


def _resume_monitor_if_saved():
    """앱 재시작(강제종료 포함) 후 이전 모니터 자동 복원 — 6시간 이내 설정만."""
    try:
        cfg = _state_get('monitor')
        if not cfg:
            return
        if not cfg.get('resume') or cfg.get('mode') == 'overlay':
            _dlog('[알림] 오버레이/비복원 설정 — 자동 복원 생략')
            _clear_monitor_cfg()
            return
        if time.time() - float(cfg.get('ts', 0)) >21600:
            return
        hosp = cfg.get('hospitals') or []
        if not hosp:
            return
        with _bed_notify_lock:
            if _bed_notify_state['running']:
                return
            ev, kk = threading.Event(), threading.Event()
            _bed_notify_state.update({
                'running': True, 'hospitals': hosp,
                'iv_sec': int(cfg.get('iv', 180)),
                'mode': cfg.get('mode', 'notify'),
                'h_param': cfg.get('h', ''),
                'stop_event': ev, 'kick_event': kk,
                'line_map': {}, 'last_line': '', 'last_ts': '',
                'next_epoch': 0.0, '_fail_streak': 0,
            })
            th = threading.Thread(target=_bed_notify_worker, args=(ev,),
                                  daemon=True, name='BedNotify')
            _bed_notify_state['thread'] = th
            th.start()
            _acquire_wake_lock()
        _dlog(f"[알림] 이전 모니터 자동 복원: {len(hosp)}곳 · {cfg.get('mode')}")
    except Exception as e:
        _dlog(f'[알림] 자동 복원 실패(무시): {e}')


def _sync_kick_monitor(from_sync=True):
    """메인 조회(compare/pip)가 캐시를 갱신한 '그 시점'에 모니터를 즉시 갱신."""
    st = _bed_notify_state
    if st['running'] and st.get('kick_event'):
        if from_sync:
            st['_sync_kick'] = True
        st['kick_event'].set()


def _acquire_wake_lock():
    """모니터 동작 중 CPU 절전 방지 (Pydroid 3는 WAKE_LOCK 권한 보유)."""
    if _wake_lock[0] is not None:
        return
    ctx = _get_android_context()
    if ctx is None:
        return
    try:
        from jnius import autoclass
        Context = autoclass('android.content.Context')
        PowerManager = autoclass('android.os.PowerManager')
        pm = ctx.getSystemService(Context.POWER_SERVICE)
        wl = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, 'em:bedmonitor')
        wl.setReferenceCounted(False)
        wl.acquire()
        _wake_lock[0] = wl
        _dlog('[알림] WakeLock 획득 — 화면 꺼짐/백그라운드에서도 갱신 유지')
    except Exception as e:
        _dlog(f'[알림] WakeLock 실패(무시): {e}')


def _release_wake_lock():
    wl = _wake_lock[0]
    _wake_lock[0] = None
    if wl is not None:
        try:
            wl.release()
            _dlog('[알림] WakeLock 해제')
        except Exception:
            pass


def _post_error_and_close(reason):
    """갱신 이상(연속 실패/백그라운드 중단) → 오류 알림 게시 후 모니터 자동 종료.
    워커 스레드 내부에서 호출되므로 join 없이 상태만 정리한다."""
    try:
        _cancel_bed_notification()
        ctx = _get_android_context()
        if ctx is not None:
            from jnius import autoclass
            Context = autoclass('android.content.Context')
            VERSION = autoclass('android.os.Build$VERSION')
            nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE)
            if VERSION.SDK_INT >= 26:
                NotificationChannel = autoclass('android.app.NotificationChannel')
                NotificationManager = autoclass('android.app.NotificationManager')
                ch = NotificationChannel(_BED_NOTIFY_CHANNEL + '_err',
                                         '병상 모니터 오류',
                                         NotificationManager.IMPORTANCE_DEFAULT)
                nm.createNotificationChannel(ch)
                Builder = autoclass('android.app.Notification$Builder')
                b = Builder(ctx, _BED_NOTIFY_CHANNEL + '_err')
            else:
                Builder = autoclass('android.app.Notification$Builder')
                b = Builder(ctx)
            b.setSmallIcon(ctx.getApplicationInfo().icon)
            b.setContentTitle(' 병상 모니터 중단')
            b.setContentText(reason)
            b.setAutoCancel(True)
            nm.notify(_BED_NOTIFY_ID + 1, b.build())
        else:
            _dlog(f'[알림] (PC) 오류 종료: {reason}')
    except Exception as e:
        _dlog(f'[알림] 오류 알림 실패: {e}')
    _bed_notify_state.update({'running': False, 'stop_event': None,
                              'kick_event': None, 'thread': None})
    _overlay_remove()
    _release_wake_lock()


def _lines_from_compare_cache(hospitals, max_age):
    """메인 조회창(compare/pip)이 채운 _compare_bed_cache에서 알림 줄 구성.
    모든 병원이 max_age초 이내로 신선할 때만 (map, True) — 이때 API 추가 호출 0회."""
    now = datetime.now()
    out, all_ok = {}, True
    with _compare_bed_cache_lock:
        for h in hospitals:
            ce = _compare_bed_cache.get(h['hpid'])
            age = 99999.0
            if ce:
                try:
                    age = (now - datetime.strptime(
                        f"{now.strftime('%Y-%m-%d')} {ce['fetched_at']}",
                        '%Y-%m-%d %H:%M:%S')).total_seconds()
                    if age < 0:
                        age += 86400   # 자정 경계
                except Exception:
                    age = 99999.0
            if not ce or age >max_age:
                all_ok = False
                continue
            name = ce.get('name') or h.get('name') or h['hpid']
            avail = ce.get('hvec', None)
            total = ce.get('hvec_t', 0) or 0
            # 캐시에 값이 없을 때만 '정보없음' (음수는 과밀 실측값)
            if avail is None:
                out[h['hpid']] = f'{name} 정보없음'
                continue
            if total >0:
                pct = round(avail / total * 100)
                out[h['hpid']] = f'{name} {avail}/{total}({pct}%)'
            else:
                out[h['hpid']] = f'{name} {avail}/-'
    return out, all_ok


def _overlay_show_web(h_param):
    """PiP 그래프 페이지(/pip)를 WebView 오버레이로 표시 (성공 True).
    페이지 자체가 /pip_data로 갱신하므로(캐시 동기화 적용) 워커의 재게시 불필요."""
    ctx = _get_android_context()
    if ctx is None:
        _dlog(f'[오버레이] (PC) pip 웹뷰: h={h_param[:60]}')
        return True
    if _overlay_refs.get('view') is not None:
        return True   # 이미 표시 중
    from urllib.parse import quote
    url = 'http://127.0.0.1:5000/pip?ov=1&h=' + quote(h_param, safe='')
    n_hosp = max(1, len([x for x in (h_param or '').split(',') if x.strip()]))

    def _create():
        from jnius import autoclass
        Context      = autoclass('android.content.Context')
        WebView      = autoclass('android.webkit.WebView')
        Gravity      = autoclass('android.view.Gravity')
        PixelFormat  = autoclass('android.graphics.PixelFormat')
        LayoutParams = autoclass('android.view.WindowManager$LayoutParams')
        VERSION      = autoclass('android.os.Build$VERSION')
        density = ctx.getResources().getDisplayMetrics().density

        def dp(v):
            return int(v * density + 0.5)

        wv = WebView(ctx)
        s = wv.getSettings()
        s.setJavaScriptEnabled(True)
        try:
            s.setDomStorageEnabled(True)
        except Exception:
            pass
        wv.setBackgroundColor(0)
        wv.loadUrl(url)
        wtype = (LayoutParams.TYPE_APPLICATION_OVERLAY if VERSION.SDK_INT >= 26
                 else LayoutParams.TYPE_PHONE)
        # 터치 허용(FLAG_NOT_TOUCHABLE 제거) → 닫기/크기/이동 버튼 조작 가능.
        # FLAG_NOT_FOCUSABLE 은 유지 → 뒤로가기/IME 를 가로채지 않는다.
        flags = LayoutParams.FLAG_NOT_FOCUSABLE
        dm = ctx.getResources().getDisplayMetrics()
        scr_w, scr_h = dm.widthPixels, dm.heightPixels
        w_px = min(scr_w - dp(12), dp(360))
        # 헤더+진행바+컨트롤 ≈ 96dp, 행당 ≈ 44dp
        h_px = min(int(scr_h * 0.62), dp(96 + 44 * n_hosp))
        p = LayoutParams(w_px, h_px, wtype, flags, PixelFormat.TRANSLUCENT)
        p.gravity = Gravity.TOP | Gravity.CENTER_HORIZONTAL
        p.x = 0
        p.y = dp(2)
        wm = ctx.getSystemService(Context.WINDOW_SERVICE)
        wm.addView(wv, p)
        _overlay_refs.update({'view': wv, 'wm': wm, 'params': p, 'web': True,
                              'w': w_px, 'h': h_px, 'dens': density,
                              'scr_w': scr_w, 'scr_h': scr_h})
        _dlog(f'[오버레이] 생성 {n_hosp}곳 크기={w_px}x{h_px}px 화면={scr_w}x{scr_h}')

    ok, err = _run_on_ui(_create)
    if err or not ok:
        _dlog(f'[오버레이] pip 웹뷰 실패: {err or "UI 응답 없음"} → 텍스트 방식 폴백')
        _overlay_refs.update({'view': None, 'wm': None, 'params': None, 'web': False})
        return False
    return True


def _overlay_adjust(action, value=0):
    """오버레이 크기(scale)·수직위치(move) 조정. 성공 시 True."""
    v  = _overlay_refs.get('view')
    wm = _overlay_refs.get('wm')
    p  = _overlay_refs.get('params')
    if v is None or wm is None or p is None:
        return False
    dens   = _overlay_refs.get('dens') or 2.0
    scr_w  = _overlay_refs.get('scr_w') or 1080
    scr_h  = _overlay_refs.get('scr_h') or 1920
    cur_w  = _overlay_refs.get('w') or p.width
    cur_h  = _overlay_refs.get('h') or p.height

    if action == 'scale':
        f = 1.18 if value >0 else (1 / 1.18)
        new_w = int(max(dens * 180, min(scr_w - dens * 8, cur_w * f)))
        new_h = int(max(dens * 110, min(scr_h * 0.92, cur_h * f)))
    else:
        new_w, new_h = cur_w, cur_h

    def _apply():
        if action == 'scale':
            p.width, p.height = new_w, new_h
        elif action == 'move':
            p.y = int(max(0, min(scr_h - dens * 60,
                                 p.y + value * dens)))
        wm.updateViewLayout(v, p)

    ok, err = _run_on_ui(_apply)
    if err or not ok:
        _dlog(f'[오버레이] 조정 실패: {err}')
        return False
    if action == 'scale':
        _overlay_refs.update({'w': new_w, 'h': new_h})
    _dlog(f'[오버레이] 조정 {action}={value} → {p.width}x{p.height} y={p.y}')
    return True


def _fetch_bed_lines(hospitals):
    """복수 병원 응급실 일반 병상 조회 → {hpid: '이름 x/y(z%)'} (지역당 API 1회).
    응답에 없는 병원은 키 자체를 생략(직전값 유지용). 폴백 정책은 단일판과 동일."""
    region_map = {}
    for h in hospitals:
        region_map.setdefault((h['sido'], h['gugun']), []).append(h)
    out = {}
    for (sido, gugun), hs in region_map.items():
        try:
            root = _region_api_root(API_URL, sido, gugun, timeout=10, ctx='bedlines')
            if root is None:
                continue
            feed = {}
            for item in root.findall('.//item'):
                hp = (item.findtext('hpid') or '').strip()
                if hp:
                    feed[hp] = item
        except Exception as e:
            _dlog(f'[알림] 병상 조회 오류 ({sido} {gugun}): {e}')
            continue
        for h in hs:
            item = feed.get(h['hpid'])
            if item is None:
                continue
            name  = (item.findtext('dutyName') or '').strip() or h.get('name') or h['hpid']
            _raw_a = item.findtext('hvec')
            _has_a = _raw_a is not None and str(_raw_a).strip() != ''
            avail = safe_int(_raw_a)
            t_raw = get_hvs(item, 'HVS01')
            prev  = _pip_bed_total_cache.get(h['hpid'], {})
            if t_raw >0:
                prev['hvec_t'] = t_raw
                total = t_raw
            else:
                total = prev.get('hvec_t', 0)
            _pip_bed_total_cache[h['hpid']] = prev
            # 음수 = 과밀(정원 초과) 실제 값. 결측일 때만 '정보없음'.
            if not _has_a:
                out[h['hpid']] = f'{name} 정보없음'
            elif total >0:
                pct = round(avail / total * 100)
                out[h['hpid']] = f'{name} {avail}/{total}({pct}%)'
            else:
                out[h['hpid']] = f'{name} {avail}/-'
    return out


def _bed_notify_worker(stop_event):
    st = _bed_notify_state
    kick = st.get('kick_event') or threading.Event()
    names = ', '.join(h.get('name') or h['hpid'] for h in st['hospitals'])
    _dlog(f"[알림] 모니터 시작: {names} · {st['iv_sec']}초 주기 · {st['mode']}")
    try:
        while not stop_event.is_set():
            # [동기화] 메인 조회창(compare/pip)이 채운 캐시가 신선하면 재사용
            #  → 추가 API 호출 0회. 캐시가 없거나 오래됐을 때만 직접 조회(폴백).
            synced = bool(st.pop('_sync_kick', False))
            if synced:
                # 메인 조회가 방금 캐시를 갱신 → 그 데이터만 사용 (완전 동기, API 0회)
                fresh, _all = _lines_from_compare_cache(st['hospitals'], max_age=86400)
            else:
                cache_lines, cache_ok = _lines_from_compare_cache(
                    st['hospitals'], max_age=max(45, st['iv_sec'] + 30))
                fresh = cache_lines if cache_ok else _fetch_bed_lines(st['hospitals'])
            if stop_event.is_set():   # 조회 중 중지된 경우 마지막 게시 방지
                break
            if fresh:
                st['_fail_streak'] = 0
            else:
                st['_fail_streak'] = st.get('_fail_streak', 0) + 1
                if st['_fail_streak'] >= 3:
                    _post_error_and_close(
                        f"갱신 3회 연속 실패 ({datetime.now().strftime('%H:%M')}) — "
                        '네트워크/서버 상태를 확인하세요.')
                    break
            st['line_map'].update(fresh)
            lines = []
            for h in st['hospitals']:
                lines.append(st['line_map'].get(
                    h['hpid'], f"{h.get('name') or h['hpid']} 조회 중..."))
            ts = datetime.now().strftime('%H:%M')
            st['last_ts'] = ts
            st['last_line'] = ' | '.join(lines)
            wait = max(30, st['iv_sec'])
            st['next_epoch'] = time.time() + wait
            if synced:
                _sub, _when, _cd = '메인 조회와 동기화', time.time() * 1000, False
            else:
                _sub, _when, _cd = f"폴백 {st['iv_sec']}초 주기", st['next_epoch'] * 1000, True
            n = len(lines)
            if st.get('mode') == 'overlay':
                _h_now = ','.join(f"{h['hpid']}|{h['sido']}|{h['gugun']}"
                                  for h in st['hospitals'])
                _ov_ok = _overlay_show_web(_h_now)   # PiP 그래프 웹뷰 (우선)
                if not _ov_ok:                       # 실패 → 텍스트 오버레이
                    _ov_ok = _overlay_show('\n'.join(lines) + f'\n{ts} 갱신')
                if not _ov_ok:                       # 최종 폴백 → 알림
                    _post_bed_notification(
                        lines[0] if n == 1 else f'응급실 병상 {n}곳 모니터',
                        f'{ts} 갱신', big_text='\n'.join(lines),
                        sub_text=_sub,
                        when_ms=_when, count_down=_cd,
                        kick_action=True, timeout_ms=(wait + 90) * 1000)
            else:
                _post_bed_notification(
                    lines[0] if n == 1 else f'응급실 병상 {n}곳 모니터',
                    (f'{ts} 갱신' if n == 1 else lines[0] + f' · {ts} 갱신'),
                    big_text=('\n'.join(lines) + f'\n {ts} 갱신') if n >1 else None,
                    sub_text=_sub,
                    when_ms=_when, count_down=_cd,
                    kick_action=True, timeout_ms=(wait + 90) * 1000)
            _dlog(f"[알림] {st['last_line']} | {ts}")
            kick.clear()
            _t0 = time.time()
            kick.wait(wait)   # 주기 대기 (킥/중지 시 즉시 해제)
            if stop_event.is_set():
                break
            # [중단 감지] 카운트다운 0 이후에도 깨어나지 못했던 경우
            if (not kick.is_set()) and (time.time() - _t0) >wait + 90:
                _post_error_and_close(
                    '백그라운드에서 갱신이 중단되었습니다. '
                    'Pydroid 3 배터리 최적화 해제 여부를 확인하세요.')
                break
    finally:
        _dlog('[알림] 모니터 종료')
        try:
            from jnius import detach
            detach()   # jnius 사용 스레드 종료 시 JVM detach (안전조치)
        except Exception:
            pass


def _stop_bed_notify(cancel_notification=True):
    """실행 중인 모니터 중지 (_bed_notify_lock 보유 상태에서 호출)"""
    ev = _bed_notify_state.get('stop_event')
    kk = _bed_notify_state.get('kick_event')
    th = _bed_notify_state.get('thread')
    if ev:
        ev.set()
    if kk:
        kk.set()
    if th and th.is_alive():
        th.join(timeout=2)
    _bed_notify_state.update({'running': False, 'thread': None, 'stop_event': None})
    _release_wake_lock()
    if cancel_notification:
        _cancel_bed_notification()
        _overlay_remove()


# 앱 재시작 시: 이전 세션(프로세스 강제종료)이 남긴 상시 알림 정리
try:
    _cancel_bed_notification()
except Exception:
    pass

# 강제종료로 끊긴 모니터 자동 복원 (Android에서만 — PC 개발 환경 제외)
if hasattr(sys, 'getandroidapilevel'):
    _resume_monitor_if_saved()


@flask_app.route('/last')
def last_compare():
    """PiP·알림에서 브라우저 복귀 — 가장 최근에 본 비교화면으로"""
    h = _last_compare_h[0]
    return redirect('/compare?h=' + h) if h else redirect('/')


@flask_app.route('/api/bed_notify', methods=['POST'])
def api_bed_notify():
    """비교화면 모니터 패널 → 병상 모니터 시작/전환/중지.
    본문: {hospitals:[{hpid,name,sido,gugun},...], iv, mode, h}
    (구버전 {hpid,name,sido,gugun} 단일 형식도 수용)
    같은 구성(병원 집합+방식) 재요청 = 중지 토글."""
    data = request.get_json(silent=True) or {}
    hosps = data.get('hospitals')
    if not hosps and data.get('hpid'):
        hosps = [{'hpid': data.get('hpid'), 'name': data.get('name'),
                  'sido': data.get('sido'), 'gugun': data.get('gugun')}]
    clean = []
    for h in (hosps or []):
        hpid  = (h.get('hpid') or '').strip()
        sido  = (h.get('sido') or '').strip()
        gugun = (h.get('gugun') or '').strip()
        if hpid and sido and gugun:
            clean.append({'hpid': hpid, 'name': (h.get('name') or hpid).strip(),
                          'sido': sido, 'gugun': gugun})
    mode  = 'overlay' if (data.get('mode') == 'overlay') else 'notify'
    label = '오버레이' if mode == 'overlay' else '알림'
    try:
        iv = max(30, int(data.get('iv', 180)))
    except (ValueError, TypeError):
        iv = 180
    action = data.get('action', '')
    with _bed_notify_lock:
        cur_key = (tuple(sorted(h['hpid'] for h in _bed_notify_state['hospitals'])),
                   _bed_notify_state.get('mode'))
        new_key = (tuple(sorted(h['hpid'] for h in clean)), mode)
        if action == 'stop' or (_bed_notify_state['running'] and clean
                                and cur_key == new_key and action != 'start'):
            was = _bed_notify_state['running']
            _stop_bed_notify()
            _clear_monitor_cfg()
            return jsonify({'ok': True, 'running': False,
                            'msg': f'{label} 중지' if was else '실행 중인 모니터가 없습니다'})
        if not clean:
            return jsonify({'ok': False, 'running': _bed_notify_state['running'],
                            'msg': '병원을 1곳 이상 선택해주세요.'})
        warn = ''
        if mode == 'overlay':
            _st, _st_msg = _overlay_permission_state()
            if _st == 'not_granted':
                _request_overlay_permission()
                return jsonify({'ok': False,
                                'running': _bed_notify_state['running'],
                                'msg': _st_msg})
            if _st in ('not_declared', 'error'):
                return jsonify({'ok': False,
                                'running': _bed_notify_state['running'],
                                'msg': _st_msg})
            if _st == 'no_context':
                warn = _st_msg
        _stop_bed_notify(cancel_notification=False)
        if mode == 'overlay':
            _cancel_bed_notification()
        else:
            _overlay_remove()
        ev, kk = threading.Event(), threading.Event()
        _bed_notify_state.update({
            'running': True, 'hospitals': clean, 'iv_sec': iv, 'mode': mode,
            'h_param': (data.get('h') or '').strip(),
            'stop_event': ev, 'kick_event': kk,
            'line_map': {}, 'last_line': '', 'last_ts': '', 'next_epoch': 0.0,
        })
        th = threading.Thread(target=_bed_notify_worker, args=(ev,),
                              daemon=True, name='BedNotify')
        _bed_notify_state['thread'] = th
        _bed_notify_state['_fail_streak'] = 0
        th.start()
        _acquire_wake_lock()   # 백그라운드에서도 주기 유지
        _save_monitor_cfg()    # 강제종료 후 자동 복원용
    if mode == 'notify' and not warn:
        _usable, warn = _bed_notifications_status()
    nm = ', '.join(h['name'] for h in clean[:2]) + (' 외' if len(clean) >2 else '')
    return jsonify({'ok': True, 'running': True,
                    'msg': f'{nm} {label} 시작 ({iv}초 주기)',
                    'warn': warn})


@flask_app.route('/api/bed_notify_close')
def api_bed_notify_close():
    """알림의 ' 닫기' 버튼 → 모니터 종료 + 알림 제거"""
    with _bed_notify_lock:
        was = _bed_notify_state['running']
        _stop_bed_notify()
        _clear_monitor_cfg()
    body = ' 병상 모니터를 종료했습니다.' if was else '실행 중인 병상 모니터가 없습니다.'
    return (f'<html><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width, initial-scale=1"></head>'
            f'<body style="font-family:sans-serif;padding:40px 16px;'
            f'text-align:center;font-size:1.05rem;">{body}</body></html>')


@flask_app.route('/api/bed_notify_kick')
def api_bed_notify_kick():
    """알림의 '지금 갱신' 버튼 → 워커 즉시 1회 갱신"""
    st = _bed_notify_state
    if st['running'] and st.get('kick_event'):
        st['kick_event'].set()
        body = ' 지금 갱신합니다. 이 창은 닫으셔도 됩니다.'
    else:
        body = '실행 중인 병상 모니터가 없습니다.'
    return (f'<html><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width, initial-scale=1"></head>'
            f'<body style="font-family:sans-serif;padding:40px 16px;'
            f'text-align:center;font-size:1.05rem;">{body}</body></html>')


_bed_line_cache = {'key': '', 'line': '', 'ts': 0.0}


@flask_app.route('/api/bed_line')
def api_bed_line():
    """외부 앱(MacroDroid/Tasker 등) 폴링용 순수 텍스트 1줄.
    ① 모니터 실행 중 → 워커 최신 값 (복수 병원이면 ' | ' 연결)
    ② 미실행 + hpid/sido/gugun 쿼리 → 직접 조회 (25초 캐시)"""
    _hdr = {'Content-Type': 'text/plain; charset=utf-8'}
    st = _bed_notify_state
    if st['running']:
        line = st.get('last_line') or '조회 중...'
        ts = st.get('last_ts', '')
        return (f'{line} · {ts}' if ts else line), 200, _hdr
    hpid  = (request.args.get('hpid') or '').strip()
    sido  = (request.args.get('sido') or '').strip()
    gugun = (request.args.get('gugun') or '').strip()
    if hpid and sido and gugun:
        key = f'{hpid}|{sido}|{gugun}'
        now = time.time()
        cch = _bed_line_cache
        if cch['key'] == key and cch['line'] and (now - cch['ts']) < 25:
            return cch['line'], 200, _hdr
        line = _fetch_bed_line(hpid, sido, gugun,
                               (request.args.get('name') or '').strip())
        if line:
            body = f"{line} · {datetime.now().strftime('%H:%M')}"
            _bed_line_cache.update({'key': key, 'line': body, 'ts': now})
            return body, 200, _hdr
        if cch['key'] == key and cch['line']:
            return f"{cch['line']} (직전값)", 200, _hdr
        return '조회 실패', 200, _hdr
    return '', 200, _hdr


@flask_app.route('/api/bed_notify_status')
def api_bed_notify_status():
    """현재 모니터 상태 (패널 동기화·카운트다운용)"""
    st = _bed_notify_state
    return jsonify({
        'running': st['running'], 'mode': st.get('mode', 'notify'),
        'iv': st.get('iv_sec', 180),
        'hospitals': [{'hpid': h['hpid'], 'name': h['name']}
                      for h in st.get('hospitals', [])],
        'line': st.get('last_line', ''), 'ts': st.get('last_ts', ''),
        'next_epoch': st.get('next_epoch', 0.0),
    })


# ══════════════════════════════════════════════════════════════════
#  [ROOT-FIX 2026-D3] 오류화면 공통 [뒤로] 버튼
#  근본원인: /compare 는 window.open(url,'_blank') 로 새 탭에서 열리므로
#  그 탭의 history.length === 1 → history.back() 이 아무 동작도 하지 않았다.
#  새 탭 / iframe(저장 HTML) / 히스토리 없음 4가지 경우를 모두 처리한다.
#  (정상화면 ermonBackToSelect() 와 동일 정책 — 국소최적화 금지)
# ══════════════════════════════════════════════════════════════════
_ERR_BACK_BTN = """<button onclick="erGoBack()"
  style="padding:10px 20px;background:#888;color:white;border:none;
         border-radius:6px;cursor:pointer;font-size:1rem;margin-left:10px;">◀ 뒤로</button>
<script>
function erGoBack() {
  /* 1) 새 탭(window.open)으로 열린 경우 -> 탭 닫기 */
  try { if (window.opener && !window.opener.closed) { window.close(); return; } } catch (e) {}
  /* 2) iframe(저장 HTML) 내부 -> 상위 오버레이 닫기 */
  try {
    if (window.parent && window.parent !== window
        && window.parent.EXAPP && window.parent.EXAPP.closeCompare) {
      window.parent.EXAPP.closeCompare(); return;
    }
  } catch (e) {}
  /* 3) 히스토리가 있으면 back — 500ms 안에 이탈이 없으면 홈으로 */
  try {
    if (history.length > 1) {
      var t = setTimeout(function () { location.replace('/'); }, 500);
      window.addEventListener('pagehide', function () { clearTimeout(t); });
      history.back(); return;
    }
  } catch (e) {}
  /* 4) 최후: 메인으로 */
  try { location.replace('/'); } catch (e) { try { window.close(); } catch (e2) {} }
}
</script>"""


@flask_app.route('/compare', methods=['GET', 'POST'])
def compare():
    # ─── POST 레거시 호환: GET 으로 리다이렉트 ───────────────────────────
    if request.method == 'POST':
        try:
            hospitals_data = json.loads(request.form.get('hospitals', '[]'))
            form_sido  = request.form.get('sido', '')
            form_gugun = request.form.get('gugun', '')
            h_parts = []
            for h in hospitals_data:
                hpid  = h.get('hpid', '')
                sido  = h.get('sido',  form_sido)
                gugun = h.get('gugun', form_gugun)
                if hpid and sido and gugun:
                    h_parts.append(f'{hpid}|{sido}|{gugun}')
            h_param = ','.join(h_parts)
            _log(f'[compare] POST→GET redirect h={h_param[:80]}...')
            return redirect('/compare?h=' + h_param)
        except Exception as e:
            _log(f'[compare] POST redirect 오류: {e}', 'ERROR')
            return ('<div style="padding:20px;color:red;">POST 처리 오류: 병원 목록을 다시 선택해주세요.</div>'
                    '<div style="padding:0 20px;">' + _ERR_BACK_BTN + '</div>'), 400

    # ─── GET ──────────────────────────────────────────────────────
    try:
        h_param = request.args.get('h', '').strip()

        if not h_param:
            old_hpids = request.args.get('hpids', '').strip()
            old_sido  = request.args.get('sido', '').strip()
            old_gugun = request.args.get('gugun', '').strip()
            if old_hpids and old_sido and old_gugun:
                h_param = ','.join(
                    f'{hpid.strip()}|{old_sido}|{old_gugun}'
                    for hpid in old_hpids.split(',') if hpid.strip()
                )
                _log(f'[compare] 구버전 URL 변환: {h_param[:80]}')
            else:
                return ('<div style="padding:20px;color:#d32f2f;">오류: 병원 정보가 없습니다.'
                        ' 메인 페이지에서 병원을 선택해주세요.</div>'
                        '<div style="padding:0 20px;">' + _ERR_BACK_BTN + '</div>'), 400

        _log(f'[compare] GET 요청: h={h_param[:120]}')

        entries = []
        parse_errors = []
        for raw in h_param.split(','):
            raw = raw.strip()
            if not raw: continue
            parts = raw.split('|')
            if len(parts) == 3 and all(p.strip() for p in parts):
                entries.append({'hpid': parts[0].strip(),
                                'sido': parts[1].strip(),
                                'gugun': parts[2].strip()})
            else:
                parse_errors.append(raw)

        if parse_errors:
            _log(f'[compare] 파싱 실패 항목: {parse_errors}', 'ERROR')
        if not entries:
            return ('<div style="padding:20px;color:#d32f2f;">오류: 파싱 가능한 병원 정보가 없습니다.'
                    ' 메인 페이지에서 다시 선택해주세요.</div>'
                    '<div style="padding:0 20px;">' + _ERR_BACK_BTN + '</div>'), 400
        if len(entries) >5:
            entries = entries[:5]

        _log(f'[compare] 파싱 완료: {len(entries)}개 병원')

        from collections import OrderedDict
        if entries:
            _last_compare_h[0] = ','.join(
                f"{e['hpid']}|{e['sido']}|{e['gugun']}" for e in entries)

        region_to_hpids = OrderedDict()
        for e in entries:
            key = (e['sido'], e['gugun'])
            region_to_hpids.setdefault(key, []).append(e['hpid'])

        hpid_to_hospital = {}
        fetch_errors = []

        # ══════════════════════════════════════════════════════════
        # [최적화] 기존에는 ①지역별(병상→목록) ②메시지 ③dutyInf ④kiosk 가
        # 순차 실행되어 총 지연 = 각 구간의 "합" 이었다.
        # 네 구간은 서로 독립적( disjoint 키에 기록 )이므로 동시에 시작해
        # 총 지연 = 가장 느린 구간 하나로 줄인다.
        # 각 구간의 내부 로직·오류 처리·기록 순서는 기존과 동일하다.
        # ══════════════════════════════════════════════════════════
        _msg_hpids = list(dict.fromkeys(e['hpid'] for e in entries))
        _msg_futures   = {hp: _NET_POOL.submit(_fetch_one_hospital_msgs, hp)
                          for hp in _msg_hpids}
        _basic_futures = {hp: _NET_POOL.submit(_fetch_basic_info, hp)
                          for hp in _msg_hpids}
        _kiosk_futures = [((sido, gugun),
                           _NET_POOL.submit(_fetch_kiosk_info, sido, gugun))
                          for (sido, gugun) in region_to_hpids]

        def _region_task(sido, gugun, target_hpids):
            """지역 1곳의 병상 API + 목록 API 조회 (기존 루프 본문과 동일 로직).
            반환: (local_map, err_str|None)  — local_map: {hpid: hospital}"""
            target_set = set(target_hpids)
            local_map = {}
            try:
                #  ROOT-FIX 2026-D1: STAGE1 정식표기가 0건을 주는 시/도
                #   (광주광역시·전라남도) 대응 — 별칭 → STAGE2생략 순 폴백.
                root = _region_api_root(API_URL, sido, gugun, timeout=15,
                                        ctx='compare')
                if root is None:
                    return local_map, f'{sido} {gugun}: 병상 API 응답 없음(전 후보 실패)'
                result_code = root.findtext('.//resultCode')
                result_msg  = root.findtext('.//resultMsg', '')
                _log(f'[compare] [{sido} {gugun}] API: code={result_code}')

                if result_code not in (None, '', '00'):
                    return local_map, f'{sido} {gugun}: API 오류 ({result_code}) {result_msg}'

                for item in root.findall('.//item'):
                    hpid = (item.findtext('hpid') or '').strip()
                    if hpid in target_set:
                        h = parse_hospital_data(item)
                        h['sido']  = sido
                        h['gugun'] = gugun
                        local_map[hpid] = h

                        # ── 비교화면 데이터를 PiP 공유 캐시에 저장 ────────────────
                        # /pip_data가 이 캐시를 우선 사용하면 API 이중 호출을 방지하고
                        # 두 화면 간 데이터 일관성이 보장된다.
                        _icu_ks = ['hvicc','hv2','hv3','hvncc','hv32','hvcc',
                                   'hv6','hv34','hvccc','hv35','hv31','hv33']
                        _ca = sum(h['icu'][k]['avail'] for k in _icu_ks
                                  if h['icu'][k]['avail'] >= 0)
                        _ct = sum(h['icu'][k]['total'] for k in _icu_ks
                                  if h['icu'][k]['total'] >0)
                        _ce = any(h['icu'][k]['avail'] >= 0 for k in _icu_ks)
                        _now_str = datetime.now().strftime('%H:%M:%S')
                        with _compare_bed_cache_lock:
                            _compare_bed_cache[hpid] = {
                                #  FIX(2025-B1): name 추가 → pip_data 캐시 히트 시
                                #   HPID 코드 대신 한글 병원명 표시
                                'name': h['name'],
                                'hvec': h['emergency']['hvec']['avail'],
                                #  FIX(2025-B2): get_hvs가 태그 부재 시 -1 반환.
                                #   total 필드에 -1이 저장되면 pip_data의
                                #   양쪽합산 분기에서 hv36_t=-1이 0으로 취급되어
                                #   합계 총량이 실제보다 낮게(hvgc_t만) 표시됨.
                                #   max(0, v)로 "데이터 없음"을 0으로 정규화.
                                'hvec_t': max(0, h['emergency']['hvec']['total']),
                                'hvgc': h['general']['hvgc']['avail'],
                                'hvgc_t': max(0, h['general']['hvgc']['total']),
                                'hv36': h['general']['hv36']['avail'],
                                'hv36_t': max(0, h['general']['hv36']['total']),
                                'hicu':   _ca if _ce else -1,
                                'hicu_t': _ct,
                                'fetched_at': _now_str,
                            }
                        # ─────────────────────────────────────────────────────────

                # 목록 API로 분류 보완
                try:
                    lr_root = _region_api_root(LIST_API_URL, sido, gugun, timeout=8,
                                               ctx='compare-list', key1='Q0', key2='Q1')
                    if lr_root is not None:
                        for li in lr_root.findall('.//item'):
                            lhpid = (li.findtext('hpid') or '').strip()
                            if lhpid in local_map:
                                emcls = (li.findtext('dutyEmcls') or '').strip()
                                name  = local_map[lhpid]['name']
                                local_map[lhpid]['emcls'] = emcls
                                local_map[lhpid]['level'] = _get_hospital_level(emcls, name)
                except Exception as le:
                    _log(f'[compare] 목록 API 오류 (무시): {le}')

            except Exception as api_err:
                _log(f'[compare] API 오류 [{sido} {gugun}]: {traceback.format_exc()}', 'ERROR')
                return local_map, f'{sido} {gugun}: {api_err}'
            return local_map, None

        # 지역 태스크 병렬 제출 → 제출 순서(기존 순회 순서)대로 병합
        # → hpid_to_hospital / fetch_errors 의 내용·순서가 기존과 동일하게 유지된다.
        _region_futures = [_NET_POOL.submit(_region_task, sido, gugun, hp_list)
                           for (sido, gugun), hp_list in region_to_hpids.items()]
        for _rf in _region_futures:
            _local_map, _region_err = _rf.result()
            hpid_to_hospital.update(_local_map)
            if _region_err:
                fetch_errors.append(_region_err)

        hospitals_data = [hpid_to_hospital[e['hpid']]
                          for e in entries if e['hpid'] in hpid_to_hospital]

        if hospitals_data:
            _sync_kick_monitor()   # 모니터 = 메인 조회와 동일 시점·동일 데이터

        if not hospitals_data:
            err_detail = '<br>'.join(fetch_errors) if fetch_errors else '해당 지역에 병원을 찾을 수 없습니다.'
            return (f'''<html><body style="font-family:sans-serif;padding:20px;">
                <div style="color:#d32f2f;background:#ffe0e0;padding:15px;border-radius:8px;margin-bottom:15px;">데이터 로드 실패:<br>{err_detail}</div>
                <button onclick="location.reload()"
                  style="padding:10px 20px;background:#667eea;color:white;border:none;
                         border-radius:6px;cursor:pointer;font-size:1rem;">다시 시도</button>
                ''' + _ERR_BACK_BTN + '''
                </body></html>'''), 502

        # ── 예외상황 메시지 직접 조회 (수정1: HTTP 자기호출 제거) ──
        # [최적화] 지역 조회와 동시에 시작해 둔 future 결과를 수집한다.
        # 개별 실패 → ' 정상' 폴백은 기존 _fetch_messages_direct와 동일.
        try:
            msgs = {}
            for _hp, _fu in _msg_futures.items():
                try:
                    _hpid_res, _msg_res = _fu.result()
                    msgs[_hpid_res] = _msg_res
                except Exception as _fe:
                    print(f"병렬 메시지 조회 실패 ({_hp}): {_fe}")
                    msgs[_hp] = ' 정상'
            for h in hospitals_data:
                h['exception'] = msgs.get(h.get('hpid'), '정상')
            _log(f'[compare] 예외상황 메시지 수신 완료')
        except Exception as msg_err:
            _log(f'[compare] 예외상황 조회 실패 (무시): {msg_err}', 'ERROR')
            for h in hospitals_data:
                h.setdefault('exception', ' 정상')

        # ── 기본정보(dutyInf) 병렬 조회 ──────────────────────────────
        # [최적화] 지역 조회와 동시에 시작해 둔 future 결과를 수집한다.
        try:
            for _fu in as_completed(list(_basic_futures.values())):
                hpid_res, duty_inf = _fu.result()
                for h in hospitals_data:
                    if h['hpid'] == hpid_res:
                        h['duty_inf'] = duty_inf
                        break
            _log(f'[compare] dutyInf 수신 완료')
        except Exception as bi_err:
            _log(f'[compare] dutyInf 조회 실패 (무시): {bi_err}', 'ERROR')
            for h in hospitals_data:
                h.setdefault('duty_inf', '')

        # ── 중증질환 수용가능정보(kiosk) 지역별 조회 ─────────────────
        # [최적화] 지역 조회와 동시에 시작해 둔 future를 기존 순서대로 병합.
        try:
            kiosk_all = {}
            for _rk, _fu in _kiosk_futures:
                kiosk_all.update(_fu.result())
            for h in hospitals_data:
                h['kiosk'] = kiosk_all.get(h['hpid'], {})
            _log(f'[compare] kiosk 수용가능 수신 완료')
        except Exception as ki_err:
            _log(f'[compare] kiosk 조회 실패 (무시): {ki_err}', 'ERROR')
            for h in hospitals_data:
                h.setdefault('kiosk', {})

        # ── 폰트 자동 조절 (n=1~4 최적화) ───────────────────────────
        def lerp(v1, v4, n):
            """n=1 최대, n>=4 최소 선형 보간"""
            if n <= 1: return v1
            if n >= 4: return v4
            return v1 + (v4 - v1) * (n - 1) / 3.0

        n = len(hospitals_data)
        sizes = {
            # 표 위 영역(제목·툴바)은 병원 수와 무관하게 5개 기준값으로 고정
            'title_font_size': '1.11rem',
            'base_font_size': '0.58rem',
            'table_font_size': f'{lerp(0.92, 0.52, n):.2f}rem',
            'category_font_size': f'{lerp(0.88, 0.52, n):.2f}rem',
            'label_font_size': f'{lerp(0.88, 0.52, n):.2f}rem',
            'bed_number_font_size': f'{lerp(0.92, 0.56, n):.2f}em',
            'pct_font_size_large': f'{lerp(0.88, 0.58, n):.2f}em',
            'exception_font_size': f'{lerp(0.78, 0.48, n):.2f}em',
            'cell_padding': '4px 2px',
            'bed_cell_padding': '3px 2px',
            'bar_height': '5px',
        }

        content = generate_comparison_html(hospitals_data)
        _log(f'[compare] 렌더링 완료: {n}개 병원')
        _page = _render_cached(
            COMPARE_WINDOW_HTML,
            num_hospitals=n,
            current_time=datetime.now().strftime("%H:%M:%S"),
            content=content,
            **sizes
        )
        #  서버 사망 대비: 직접조회 폴백 엔진을 페이지에 내장
        return _page.replace('</body>', _live_engine_script(entries) + '</body>', 1)
    except Exception as e:
        _log(f'[compare] 처리 오류: {e}\n{traceback.format_exc()}', 'ERROR')
        return f'<div style="padding:20px;color:red;">오류 발생: {str(e)}</div>', 500


def safe_int(value):
    if value is None or value == '': return -1
    try: return int(value)
    except (ValueError, TypeError): return -1

def get_hvs(item, tag_name):
    val = item.findtext(tag_name.upper())
    if val is not None: return safe_int(val)
    return safe_int(item.findtext(tag_name.lower()))

def parse_hospital_data(item):
    def eq(ayn, cnt):
        avail = (item.findtext(ayn) or 'N').upper().startswith('Y')
        return {'available': avail, 'count': safe_int(item.findtext(cnt)) if avail else 0}

    equipment = {
        'ct': eq('hvctayn', 'hvs27'),
        'mri': eq('hvmriayn', 'hvs28'),
        'angio': eq('hvangioayn', 'hvs29'),
        'ventilator': eq('hvventiayn', 'hvs30'),
        'ventilator_preemie':eq('hvventisoayn','hvs31'),
        'incubator': eq('hvincuayn', 'hvs32'),
        'crrt': eq('hvcrrtayn', 'hvs33'),
        'ecmo': eq('hvecmoayn', 'hvs34'),
        'hypothermia': eq('hvhypoayn', 'hvs35'),
        'hyperbaric': eq('hvoxyayn', 'hvs37'),
    }
    # hv42(분만실)은 Y/N 또는 숫자/분수 형태 → raw 저장 (이슈4)
    hv42_raw = (item.findtext('hv42') or '').strip()
    return {
        'hpid': item.findtext('hpid') or '',
        'name': item.findtext('dutyName') or '알 수 없음',
        'dutyAddr': item.findtext('dutyAddr') or '',
        'dutyTel1': item.findtext('dutyTel1') or '',
        'dutyTel3': item.findtext('dutyTel3') or '',
        'update_time':item.findtext('hvidate') or '',
        'emcls': '',       # /api/hospitals 에서 채워짐
        'emclsName': '',
        'level': '기관',  # 기본값
        'emergency': {
            'hvec': {'avail': safe_int(item.findtext('hvec')), 'total': get_hvs(item, 'HVS01')},
            'hv28': {'avail': safe_int(item.findtext('hv28')), 'total': get_hvs(item, 'HVS02')},
            'hv29': {'avail': safe_int(item.findtext('hv29')), 'total': get_hvs(item, 'HVS03')},
            'hv30': {'avail': safe_int(item.findtext('hv30')), 'total': get_hvs(item, 'HVS04')},
        },
        'icu': {
            'hvicc': {'avail': safe_int(item.findtext('hvicc')), 'total': get_hvs(item, 'HVS17')},
            'hv2': {'avail': safe_int(item.findtext('hv2')), 'total': get_hvs(item, 'HVS06')},
            'hv3': {'avail': safe_int(item.findtext('hv3')), 'total': get_hvs(item, 'HVS07')},
            'hvncc': {'avail': safe_int(item.findtext('hvncc')), 'total': get_hvs(item, 'HVS08')},
            'hv32': {'avail': safe_int(item.findtext('hv32')), 'total': get_hvs(item, 'HVS09')},
            'hvcc': {'avail': safe_int(item.findtext('hvcc')), 'total': get_hvs(item, 'HVS11')},
            'hv6': {'avail': safe_int(item.findtext('hv6')), 'total': get_hvs(item, 'HVS12')},
            'hv34': {'avail': safe_int(item.findtext('hv34')), 'total': get_hvs(item, 'HVS15')},
            'hvccc': {'avail': safe_int(item.findtext('hvccc')), 'total': get_hvs(item, 'HVS16')},
            'hv35': {'avail': safe_int(item.findtext('hv35')), 'total': get_hvs(item, 'HVS18')},
            'hv31': {'avail': safe_int(item.findtext('hv31')), 'total': get_hvs(item, 'HVS05')},
            'hv33': {'avail': safe_int(item.findtext('hv33')), 'total': get_hvs(item, 'HVS10')},
        },
        'isolation': {
            'hv13': {'avail': safe_int(item.findtext('hv13')), 'total': get_hvs(item, 'HVS46')},
            'hv14': {'avail': safe_int(item.findtext('hv14')), 'total': get_hvs(item, 'HVS47')},
            'hv15': {'avail': safe_int(item.findtext('hv15')), 'total': get_hvs(item, 'HVS48')},
            'hv16': {'avail': safe_int(item.findtext('hv16')), 'total': get_hvs(item, 'HVS49')},
            'hv22': {'avail': safe_int(item.findtext('hv22')), 'total': get_hvs(item, 'HVS54')},
            'hv23': {'avail': safe_int(item.findtext('hv23')), 'total': get_hvs(item, 'HVS55')},
            'hv24': {'avail': safe_int(item.findtext('hv24')), 'total': get_hvs(item, 'HVS56')},
            'hv25': {'avail': safe_int(item.findtext('hv25')), 'total': get_hvs(item, 'HVS57')},
            'hv26': {'avail': safe_int(item.findtext('hv26')), 'total': get_hvs(item, 'HVS58')},
            'hv27': {'avail': safe_int(item.findtext('hv27')), 'total': get_hvs(item, 'HVS59')},
        },
        # 수술실 + 분만실(hv42 raw 저장)
        'other': {
            'hvoc': {'avail': safe_int(item.findtext('hvoc')), 'total': get_hvs(item, 'HVS22')},
            'hv42': {'raw': hv42_raw, 'total': get_hvs(item, 'HVS26')},
        },
        'general': {
            'hvgc': {'avail': safe_int(item.findtext('hvgc')), 'total': get_hvs(item, 'HVS38')},
            'hv36': {'avail': safe_int(item.findtext('hv36')), 'total': get_hvs(item, 'HVS19')},
            'hv37': {'avail': safe_int(item.findtext('hv37')), 'total': get_hvs(item, 'HVS20')},
            'hv41': {'avail': safe_int(item.findtext('hv41')), 'total': get_hvs(item, 'HVS25')},
        },
        'equipment': equipment,
        'exception': '정상',
    }

def should_show_row(hospitals_data, category, key):
    for h in hospitals_data:
        bd = h.get(category, {}).get(key, {})
        if bd.get('avail', -1) != -1: return True
        if bd.get('total', -1) >= 0:  return True
    return False

def should_show_equipment(hospitals_data, eq_key):
    statuses = []
    for h in hospitals_data:
        ed = h.get('equipment', {}).get(eq_key, {})
        statuses.append(ed.get('available', False) if isinstance(ed, dict) else ed)
    if len(set(statuses)) == 1 and not statuses[0]: return False
    return True


def _break_label(text):
    """항목명에서 '(' 앞에서 강제 줄바꿈 — 조회화면 셀 항목명 처리"""
    import re as _re
    return _re.sub(r'\s*\(', '<br>(', text)

def generate_comparison_html(hospitals_data):
    num_hospitals = len(hospitals_data)
    html = ('<div class="comparison-wrapper"><table class="comparison-table"><thead><tr>'
            '<th style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);">항목</th>')

    # 레벨별 헤더 배경색 — 권역 명도+10%(채도유지) / 센터 청록 / 기관 짙은슬레이트
    level_bg = {
        '권역': 'linear-gradient(135deg, #192799 0%, #4217d1 50%, #7955e6 100%)',  # 명도+10% 채도유지
        '센터': 'linear-gradient(135deg, #013a3a 0%, #0d6655 50%, #0f8068 100%)',  # 청록
        '기관': 'linear-gradient(135deg, #1a2840 0%, #2c4570 50%, #3a5a90 100%)',  # 짙은슬레이트
    }

    for h in hospitals_data:
        name = h["name"]; name_length = len(h["name"])
        has_lb = False
        if '대학교의과대학' in name:
            parts = name.split('의과대학', 1)
            if len(parts) == 2: name = f"{parts[0]}의과대학<br>{parts[1]}"; has_lb = True
        if not has_lb:
            for kw in ['재단법인','재단','대학교','기념','남도','북도']:
                if kw in name:
                    p = name.split(kw, 1)
                    if len(p) == 2: name = f"{p[0]}{kw}<br>{p[1]}"; has_lb = True; break
        nc = 'hospital-name very-long-name' if name_length >20 else ('hospital-name long-name' if name_length >12 else 'hospital-name')
        u = h.get('update_time', '')
        us = f"<br><span style='font-size:0.7em;font-weight:normal;'>{u[:4]}-{u[4:6]}-{u[6:8]} {u[8:10]}:{u[10:12]}</span>" if u and len(u) >= 12 else ''
        level = h.get('level', '기관')
        bg = level_bg.get(level, level_bg['기관'])
        html += f'<th class="{nc}" style="background:{bg};">{name}{us}</th>'
    html += '</tr></thead><tbody>'

    for category, label, items in [
        ('응급실', '응급실', [('일반','hvec'),('소아','hv28'),('음압격리','hv29'),('일반격리','hv30')]),
        ('중환자실', '중환자실', [('일반','hvicc'),('내과','hv2'),('외과','hv3'),('신생아','hvncc'),('소아','hv32'),('신경과','hvcc'),('신경외과','hv6'),('심장내과','hv34'),('흉부외과','hvccc'),('음압격리','hv35'),('응급전용','hv31'),('응급전용(소아)','hv33')]),
        ('격리진료구역', '격리진료구역', [('음압격리','hv13'),('일반격리','hv14'),('소아음압','hv15'),('소아일반','hv16'),('감염병전담(중환자실)','hv22'),('감염병전담(중환자실 음압)','hv23'),('감염[중증]','hv24'),('감염[준-중증]','hv25'),('감염[중등증]','hv26'),('코호트격리','hv27')]),
        ('입원실', '입원실', [('일반','hvgc'),('응급전용','hv36'),('응급전용(소아)','hv37'),('음압격리','hv41')]),
    ]:
        cat_key = {'응급실':'emergency','중환자실':'icu','격리진료구역':'isolation','입원실':'general'}[category]
        visible = [(lbl, key) for lbl, key in items if should_show_row(hospitals_data, cat_key, key)]
        if visible:
            html += f'<tr><td colspan="{num_hospitals+1}" class="category-header">{category}</td></tr>'
            for lbl, key in visible:
                html += f'<tr><td class="item-label">{_break_label(lbl)}</td>'
                for h in hospitals_data: html += format_bed_cell(h[cat_key][key])
                html += '</tr>'

    # 기타: 수술실 + 분만실 (이슈4)
    other_items_def = [
        ('수술실', 'hvoc', False),
        ('분만실', 'hv42', True),   # True = 분만실 전용 렌더러
    ]
    other_visible = []
    for lbl, key, is_birth in other_items_def:
        visible = False
        for h in hospitals_data:
            bd = h.get('other', {}).get(key, {})
            if is_birth:
                if bd.get('raw', ''):
                    visible = True; break
            else:
                if bd.get('avail', -1) != -1 or bd.get('total', -1) >= 0:
                    visible = True; break
        if visible:
            other_visible.append((lbl, key, is_birth))

    if other_visible:
        html += f'<tr><td colspan="{num_hospitals+1}" class="category-header">기타</td></tr>'
        for lbl, key, is_birth in other_visible:
            html += f'<tr><td class="item-label">{_break_label(lbl)}</td>'
            for h in hospitals_data:
                if is_birth:
                    html += format_birth_room_cell(h['other'][key])
                else:
                    html += format_bed_cell(h['other'][key])
            html += '</tr>'

    # 의료장비
    eq_list = [('CT','ct'),('MRI','mri'),('혈관촬영기','angio'),('인공호흡기','ventilator'),
               ('인공호흡기(조산아)','ventilator_preemie'),('인큐베이터','incubator'),
               ('CRRT','crrt'),('ECMO','ecmo'),('고압산소치료기','hyperbaric'),('중심체온조절유도기','hypothermia')]
    vis_eq = [(n,k) for n,k in eq_list if should_show_equipment(hospitals_data, k)]
    if vis_eq:
        html += f'<tr><td colspan="{num_hospitals+1}" class="category-header">의료장비</td></tr>'
        for en, ek in vis_eq:
            html += f'<tr><td class="item-label">{_break_label(en)}</td>'
            for h in hospitals_data:
                ed = h.get('equipment',{}).get(ek,{})
                ea = ed.get('available', False) if isinstance(ed, dict) else ed
                ec = ed.get('count', 1)          if isinstance(ed, dict) else 1
                if ea:
                    html += f'<td class="equipment-cell equipment-available">{ec if ec >0 else 1}</td>'
                else:
                    html += '<td class="equipment-cell equipment-unavailable" style="font-size:1.03em;">X</td>'
            html += '</tr>'

    # ── 중증질환 수용가능 (MKioskTy) ─────────────────────────────
    # 하나라도 데이터가 있는 항목만 행 표시; Y/정보미제공/불가능 + Msg 툴팁
    def _kiosk_cell_val(val_str):
        v = (val_str or '').strip()
        # '0' 포함: API가 0을 수용가능으로 반환하는 경우 → 굵은 O 표시
        if v in ('Y', 'Y ', '0'):
            return 'ok', '<span style="font-weight:900;font-size:1.34em;color:#4CAF50;line-height:1;display:inline-block;">O</span>'
        if '불가' in v:
            # X: -20% (1.29em → 1.03em)
            return 'ng', '<span style="font-size:1.03em;color:#C62828;font-weight:700;line-height:1;">X</span>'
        if v == '정보미제공':
            return 'na', '<span style="color:#888;font-size:1.0em;">–</span>'
        if v:
            return 'partial', '<span style="color:#e65100;font-size:1.1em;"></span>'
        return 'none', ''

    kiosk_visible = []
    for code, name in MKIOSK_MAP.items():
        has_data = any(code in h.get('kiosk', {}) for h in hospitals_data)
        if has_data:
            kiosk_visible.append((code, name))

    if kiosk_visible:
        html += f'<tr><td colspan="{num_hospitals+1}" class="category-header">중증질환 수용가능 <a href="https://www.e-gen.or.kr" target="_blank" style="font-size:0.72em;color:#c5d8ff;text-decoration:underline;">E-Gen 공식확인</a></td></tr>'
        for code, name in kiosk_visible:
            html += f'<tr><td class="item-label">{_break_label(name)}</td>'
            for h in hospitals_data:
                kd = h.get('kiosk', {}).get(code, {})
                val_raw = kd.get('val', '')
                msg     = kd.get('msg', '')
                status, icon = _kiosk_cell_val(val_raw)
                if not icon:
                    html += '<td class="kiosk-cell" style="color:#888;">–</td>'
                else:
                    # 이모지/아이콘과 동일 색상으로 msg 텍스트 표시, 잘림 없이 전체 출력
                    msg_color = {'ok':'#388e3c','ng':'#c62828','partial':'#e65100','na':'#888'}.get(status,'#777')
                    html += (f'<td class="kiosk-cell">'
                             + icon
                             + (f'<div class="kiosk-msg" style="color:{msg_color};">'
                                + msg + '</div>' if msg else '')
                             + '</td>')
            html += '</tr>'

    # ── 예외상황 (이슈5, 이슈6, 이슈7 반영) ─────────────────────────
    html += (f'<tr><td colspan="{num_hospitals+1}" '
             f'class="category-header cat-exception">예외상황</td></tr>')
    html += '<tr><td class="item-label">예외상황</td>'
    for h in hospitals_data:
        exc = (h.get('exception') or '정보 없음')
        # 센티넬(구 '✅ 정상')이 이모지 제거로 소실 → 의미 기반 판정으로 교체
        if exc.strip() in ('', '정상', '정보 없음'):
            duty_inf_raw = (h.get('duty_inf') or '').strip()
            if duty_inf_raw:
                duty_lines = [s.strip() for s in duty_inf_raw.replace('，',',').split(',') if s.strip()]
                duty_html = '<div style="color:#5a6a7e;font-weight:700;margin-left:5px;">상시 운영 제한:</div>'
                for di in duty_lines:
                    duty_html += f'<div style="margin-left:10px;color:#5a6a7e;line-height:1.3;">{di}</div>'
                html += (f'<td class="exception-cell exception-warning" '
                         f'style="text-align:left;padding:6px;vertical-align:top;">{duty_html}</td>')
            else:
                html += ('<td class="exception-cell exception-ok">'
                         ' <span style="color:#000;font-weight:normal;">없음</span></td>')
        else:
            un_lines  = []
            av_lines  = []
            inq_lines = []
            for ln in exc.split('\n'):
                ln = ln.strip()
                if not ln: continue
                if ln.startswith('[수용불가] '):
                    un_lines.append(ln[7:])
                elif ln.startswith('[수용가능] '):
                    av_lines.append(ln[7:])
                elif ln.startswith('[문의필요] '):
                    inq_lines.append(ln[7:])
                else:
                    un_lines.append(ln)

            # dutyInf (상시 운영 제한) 수집
            duty_inf_raw = (h.get('duty_inf') or '').strip()
            duty_inf_lines = [s.strip() for s in duty_inf_raw.replace('，',',').split(',') if s.strip()] if duty_inf_raw else []

            # 그룹 제목: 같은 색 밑줄 / 그룹 사이 1줄 공백 / [진료과목]만 검정
            result = []

            def _grp(title, color, lines, dept_color=True):
                if not lines:
                    return
                if result:                       # 첫 그룹 앞에는 공백 없음
                    result.append('<div style="height:1.3em;"></div>')
                result.append(
                    f'<div style="color:{color};font-weight:700;margin-left:5px;'
                    f'text-decoration:underline;text-decoration-color:{color};">{title}</div>')
                for item in lines:
                    #  [2026-H2] 과목=그룹색 / 세부내용=검정 (기존과 반전)
                    if dept_color:
                        body, line_col = _dept_color(item, color), '#000'
                    else:
                        body, line_col = _html_escape(item), color
                    result.append(
                        f'<div style="margin-left:10px;color:{line_col};line-height:1.3;">{body}</div>')

            _grp('수용불가:', '#dc3545', un_lines)
            _grp('수용가능:', '#28a745', av_lines)
            _grp('※ 문의 필요:', '#e67e00', inq_lines)
            _grp('상시 운영 제한:', '#5a6a7e', duty_inf_lines, dept_color=False)

            exc_fmt = ''.join(result) if result else exc
            html += (f'<td class="exception-cell exception-warning" '
                     f'style="text-align:left;padding:6px;vertical-align:top;">{exc_fmt}</td>')
    html += '</tr></tbody></table></div>'
    return html

def format_birth_room_cell(bed_data):
    """분만실(hv42) 전용 셀 - raw Y/N/숫자/분수 처리 (이슈4)"""
    raw   = bed_data.get('raw', '')
    total = bed_data.get('total', -1)
    if not raw:
        return '<td class="bed-cell"><div class="bed-info"><div class="bed-numbers" style="color:#000;">-</div></div></td>'

    raw_up = raw.upper()

    # 숫자인 경우 → 기존 format_bed_cell 로직 재사용
    try:
        num = int(raw)
        return format_bed_cell({'avail': num, 'total': total})
    except ValueError:
        pass

    # "가능/숫자" 형태 (예: 가능/2)
    if '/' in raw and not raw_up.startswith('N'):
        parts = raw.split('/', 1)
        display = raw  # 그대로 표시
        bar_class, text_class = 'bar-green', 'green-text'
        return (f'<td class="bed-cell"><div class="bed-info"><div class="bar-container">\n'
                f' <div class="bar {bar_class}" style="width:100%"></div>\n'
                f' <div class="bed-text-overlay {text_class}">{display}</div>\n'
                f' </div></div></td>')

    # Y 계열 → 가능 (100%)
    if raw_up.startswith('Y'):
        total_str = f'/{total}' if total >0 else ''
        display = f'가능{total_str}'
        return (f'<td class="bed-cell"><div class="bed-info"><div class="bar-container">\n'
                f' <div class="bar bar-green" style="width:100%"></div>\n'
                f' <div class="bed-text-overlay green-text">{display}</div>\n'
                f' </div></div></td>')

    # N 계열 → 불가 (0%)
    if raw_up.startswith('N'):
        return (f'<td class="bed-cell"><div class="bed-info"><div class="bar-container">\n'
                f' <div class="bar bar-red" style="width:0%"></div>\n'
                f' <div class="bed-text-overlay red-text">불가</div>\n'
                f' </div></div></td>')

    # 그 외 문자열 그대로 표시 (예: "가능")
    return (f'<td class="bed-cell"><div class="bed-info"><div class="bar-container">\n'
            f' <div class="bar bar-green" style="width:100%"></div>\n'
            f' <div class="bed-text-overlay green-text">{raw}</div>\n'
            f' </div></div></td>')


def format_bed_cell(bed_data):
    avail = bed_data['avail']; total = bed_data['total']
    if avail == -1 and total <= 0:
        return '<td class="bed-cell"><div class="bed-info"><div class="bed-numbers" style="color:#000;">-</div></div></td>'
    if avail < 0:
        display_text = f"{avail}/{total} ({round(avail/total*100)}%)" if total >0 else str(avail)
        return (f'<td class="bed-cell"><div class="bed-info"><div class="bar-container">'
                f'<div class="bar" style="width:0%;background:#cccccc;"></div>'
                f'<div class="bed-text-overlay" style="color:#d32f2f;font-weight:900;">{display_text}</div>'
                f'</div></div></td>')
    if total <= 0:
        if avail == 0:
            # 0병상 가용 (기준 데이터 없음): "-"가 아닌 빨간 "0"으로 표시하여 데이터 없음과 구분
            return ('<td class="bed-cell"><div class="bed-info"><div class="bar-container">'
                    '<div class="bar bar-red" style="width:0%"></div>'
                    '<div class="bed-text-overlay red-text">0</div>'
                    '</div></div></td>')
        return (f'<td class="bed-cell"><div class="bed-info">'
                f'<div class="bed-numbers"><span class="available">{avail}</span></div>'
                f'</div></td>')
    pct = round(avail / total * 100)
    bar_width = min(100, pct)
    if pct >= 50: bar_class, text_class = 'bar-green', 'green-text'
    elif pct >= 20: bar_class, text_class = 'bar-yellow', 'yellow-text'
    else:           bar_class, text_class = 'bar-red', 'red-text'
    display_text = f"{avail}/{total} ({pct}%)"
    return (f'<td class="bed-cell"><div class="bed-info"><div class="bar-container">\n'
            f' <div class="bar {bar_class}" style="width:{bar_width}%"></div>\n'
            f' <div class="bed-text-overlay {text_class}">{display_text}</div>\n'
            f' </div></div></td>')




# ══════════════════════════════════════════════════════════════════
#  /pip  — 백그라운드 팝업용 미니 대시보드 (서버 렌더링 방식)
#  canvas/video PIP API 불필요; window.open() 팝업으로 표시됨.
# ══════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════
#  /pip  — 백그라운드 팝업용 미니 대시보드 (개선판)
#  변경: 열 정렬, 숫자/분모 표시, 가는 색상 바, 카운트다운 프로그레스
# ══════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════
#   HTML 저장 (내보내기) — 서버(파이썬) 없이 단독 동작하는 조회화면
#  - 병원 구성(h 파라미터)과 서비스키, CSS(폰트 크기 확정)를 굽고,
#    데이터 조회/파싱/렌더링 로직 전체를 JS로 이식해 내장한다.
#  - 갱신 시 브라우저가 apis.data.go.kr 를 직접 호출하며,
#    CORS 차단 시 공개 프록시(allorigins/corsproxy)로 자동 우회한다.
#  - 렌더 결과는 generate_comparison_html 과 바이트 단위 동일하도록
#    이식되었다 (Node 교차검증으로 확인).
# ══════════════════════════════════════════════════════════════════
EXPORT_HTML_SHELL = r'''<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>응급의료상황판 (저장본)</title>
    <style>__CSS__
        .ex-status { font-size: 0.72rem; color: #667eea; margin-left: 6px; white-space: nowrap; }
        .ex-err { max-width: 640px; margin: 30px auto; background: #fff; border-radius: 12px;
                  padding: 22px; box-shadow: 0 4px 14px rgba(0,0,0,0.12); color: #c62828;
                  font-size: 0.9rem; line-height: 1.5; }
        .ex-note { color: #888; font-size: 0.68rem; text-align: center; margin: 8px 4px 14px; }
    </style>
</head>
<body>
    <div class="header">
        <button class="back-sel" id="backSelBtn" title="병원 선택 화면으로"
                onclick="ermonBackToSelect()">← 병원 선택</button>
        <div class="hdr-row">
            <h1><span class="h1-main">응급의료상황판</span></h1>
            <div class="hdr-right">
                <span class="h1-sub">(<span id="queryTime">--:--:--</span>기준)</span>
            </div>
        </div>
        <div class="refresh-controls">
            <label for="refreshInterval">갱신주기:</label>
            <select id="refreshInterval">
                <option value="0">수동</option>
                <option value="60000">1분</option>
                <option value="180000" selected>3분</option>
                <option value="300000">5분</option>
                <option value="600000">10분</option>
                <option value="1800000">30분</option>
                <option value="custom">직접입력</option>
            </select>
            <button id="refreshNow">즉시 갱신</button>
            <label style="font-weight:normal;"><input type="checkbox" id="allowProxy" checked>프록시 허용</label>
            <button id="miniBtn" title="항상 위 미니창">미니창</button>
            <button id="miniStyleBtn" title="미니창 스타일">스타일</button>
            <button id="secBtn" title="표시 항목 설정">표시 항목</button>
            <span class="ex-status" id="exStatus">대기</span>
        </div>
        <script>
        // 병원 선택 화면 복귀 + 안드로이드 백버튼 가로채기(앱 종료 방지)
        function ermonBackToSelect() {
            // 저장본에서는 이 화면이 상위 문서의 iframe(srcdoc) 안에 있다.
            // history.back() 을 쓰면 최상위 문서가 원본 content:// 로 되돌아가
            // 권한 만료로 ERR_FILE_NOT_FOUND 가 난다. 오버레이만 닫는다.
            var inFrame = false;
            try { inFrame = !!(window.parent && window.parent !== window); }
            catch (e) { inFrame = true; }
            // ① 동일 출처(앱 내부 iframe): 부모 API 직접 호출
            try {
                if (inFrame && window.parent.EXAPP && window.parent.EXAPP.closeCompare) {
                    window.parent.EXAPP.closeCompare();
                    return;
                }
            } catch (e) {}
            // ② [ROOT-FIX 2026-G2] 저장본의 비교화면은 about:srcdoc 프레임이고,
            //    부모 문서가 file:// · content:// 이면 opaque origin 이 되어
            //    window.parent.EXAPP 접근이 SecurityError 로 차단된다.
            //    → ①이 조용히 실패하고 ③④도 조건 불일치라 [뒤로가기]·[← 병원 선택]
            //      양쪽이 완전 무반응이 되었다. postMessage 는 교차출처에서도
            //      동작하므로 프레임 안에서는 이 경로를 정규 경로로 사용한다.
            if (inFrame) {
                try {
                    window.parent.postMessage({ ermonClose: 1 }, '*');
                    return;
                } catch (e) {}
            }
            try {
                if (location.protocol === 'http:' || location.protocol === 'https:') {
                    location.href = '/';
                    return;
                }
            } catch (e) {}
            try {
                if (!inFrame && history.length > 1) { history.back(); return; }
            } catch (e) {}
            try { window.close(); } catch (e) {}
        }
        (function () {
            // 백버튼 가로채기는 최상위 문서에서만. iframe 에서 걸면 상위가 이탈한다.
            //  (프레임일 때는 부모 글루가 popstate 를 잡아 오버레이를 닫는다)
            try {
                if (window.top !== window) return;
                history.pushState({ ermon: 1 }, '', location.href);
                window.addEventListener('popstate', function () {
                    ermonBackToSelect();
                });
            } catch (e) {}
        })();
        </script>
        <div style="margin: 0 0 0 0; padding-top: 0;">
            <div class="bed-cell" style="max-width: 400px; margin: 0 auto; padding: 0;">
                <div class="bed-info">
                    <div class="bar-container" style="height: 10px; overflow: visible;">
                        <div class="bar bar-green" id="globalRefreshBar" style="width: 100%"></div>
                        <div class="bed-text-overlay green-text" id="globalRefreshOverlay"
                             style="font-size:0.60em;white-space:nowrap;overflow:visible;
                                    top:50%;transform:translate(-50%,-50%);line-height:1;">
                              <span id="globalRefreshText">--:--</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <div id="exBody"><div class="ex-err" style="color:#667eea;">병상 정보를 불러오는 중...</div></div>
    <div class="ex-note">저장본 생성: __GENERATED__ · 병원 구성 고정 · 데이터는 국립중앙의료원 API에서 갱신됩니다</div>
    <script>
/*EX-ENGINE-START*/
var EX = (function () {
    'use strict';
    var cfg = __CONFIG__;

    // ── XML/공용 유틸 (파이썬 원본과 동일 동작) ──────────────────
    var parseXml = function (text) {
        return new DOMParser().parseFromString(text, 'text/xml');
    };
    function txt(el, tag) {                       // findtext(tag) or ''
        if (!el) return '';
        var list = el.getElementsByTagName(tag);
        if (!list || list.length === 0) return '';
        return list[0].textContent || '';
    }
    function items(doc) {
        var out = [], list = doc.getElementsByTagName('item');
        for (var i = 0; i < list.length; i++) out.push(list[i]);
        return out;
    }
    function safeInt(v) {                          // int() 실패 → -1
        if (v === null || v === undefined || v === '') return -1;
        var s = String(v).trim();
        if (!/^[+-]?\d+$/.test(s)) return -1;
        return parseInt(s, 10);
    }
    function getHvs(item, tagName) {               // 대문자 우선, 없으면 소문자
        var list = item.getElementsByTagName(tagName.toUpperCase());
        if (list && list.length >0) return safeInt(list[0].textContent || '');
        return safeInt(txt(item, tagName.toLowerCase()) || nullIfMissing(item, tagName.toLowerCase()));
    }
    function nullIfMissing(item, tag) {            // 소문자 태그 부재 시 null → safeInt(-1)
        var l = item.getElementsByTagName(tag);
        return (l && l.length >0) ? (l[0].textContent || '') : null;
    }
    function pyRound(x) {                          // 파이썬 round (은행가 반올림)
        var f = Math.floor(x), d = x - f;
        if (d < 0.5) return f;
        if (d >0.5) return f + 1;
        return (f % 2 === 0) ? f : f + 1;
    }

    // ── 코드맵 (원본과 동일) ─────────────────────────────────────
    var D_CODE_MAP = {
        'D001': '내과', 'D002': '소아청소년과', 'D003': '신경과',
        'D004': '정신건강의학과', 'D005': '피부과', 'D006': '외과',
        'D007': '흉부외과', 'D008': '정형외과', 'D009': '신경외과',
        'D010': '성형외과', 'D011': '산부인과', 'D012': '안과',
        'D013': '이비인후과', 'D014': '비뇨기과', 'D016': '재활의학과',
        'D017': '마취통증의학과','D018': '영상의학과', 'D019': '치료방사선과',
        'D020': '임상병리과', 'D021': '해부병리과', 'D022': '가정의학과',
        'D023': '핵의학과', 'D024': '응급의학과', 'D026': '치과',
        'D034': '구강악안면외과'
    };
    var MKIOSK_MAP = {
        // 공식 매핑 V4 / 실측 확인: Ty1~27 = 중증질환, Ty28 = 응급실
        'MKioskTy1': '[재관류중재술] 심근경색',
        'MKioskTy2': '[재관류중재술] 뇌경색',
        'MKioskTy3': '[뇌출혈수술] 거미막하출혈',
        'MKioskTy4': '[뇌출혈수술] 거미막하출혈 외',
        'MKioskTy5': '[대동맥응급] 흉부',
        'MKioskTy6': '[대동맥응급] 복부',
        'MKioskTy7': '[담낭담관질환] 담낭질환',
        'MKioskTy8': '[담낭담관질환] 담도포함질환',
        'MKioskTy9': '[복부응급수술] 비외상',
        'MKioskTy10': '[장중첩/폐색] 영유아',
        'MKioskTy11': '[응급내시경] 성인 위장관',
        'MKioskTy12': '[응급내시경] 영유아 위장관',
        'MKioskTy13': '[응급내시경] 성인 기관지',
        'MKioskTy14': '[응급내시경] 영유아 기관지',
        'MKioskTy15': '[저체중출생아] 집중치료',
        'MKioskTy16': '[산부인과응급] 분만',
        'MKioskTy17': '[산부인과응급] 산과수술',
        'MKioskTy18': '[산부인과응급] 부인과수술',
        'MKioskTy19': '[중증화상] 전문치료',
        'MKioskTy20': '[사지접합] 수족지접합',
        'MKioskTy21': '[사지접합] 수족지접합 외',
        'MKioskTy22': '[응급투석] HD',
        'MKioskTy23': '[응급투석] CRRT',
        'MKioskTy24': '[정신과적응급] 폐쇄병동입원',
        'MKioskTy25': '[안과적수술] 응급',
        'MKioskTy26': '[영상의학혈관중재] 성인',
        'MKioskTy27': '[영상의학혈관중재] 영유아',
        'MKioskTy28': '응급실 수용'
    };
    var Y_CODE_MAP = {
        'Y000': '응급실',
        'Y0010': '[재관류중재술] 심근경색',
        'Y0020': '[재관류중재술] 뇌경색',
        'Y0031': '[뇌출혈수술] 거미막하출혈',
        'Y0032': '[뇌출혈수술] 거미막하출혈 외',
        'Y0041': '[대동맥응급] 흉부',
        'Y0042': '[대동맥응급] 복부',
        'Y0051': '[담낭담관질환] 담낭질환',
        'Y0052': '[담낭담관질환] 담도포함질환',
        'Y0060': '[복부응급수술] 비외상',
        'Y0070': '[장중첩/폐색] 영유아',
        'Y0081': '[응급내시경] 성인 위장관',
        'Y0082': '[응급내시경] 영유아 위장관',
        'Y0091': '[응급내시경] 성인 기관지',
        'Y0092': '[응급내시경] 영유아 기관지',
        'Y0100': '[저출생체중아] 집중치료',
        'Y0111': '[산부인과응급] 분만',
        'Y0112': '[산부인과응급] 산과수술',
        'Y0113': '[산부인과응급] 부인과수술',
        'Y0120': '[중증화상] 전문치료',
        'Y0131': '[사지접합] 수족지접합',
        'Y0132': '[사지접합] 수족지접합 외',
        'Y0141': '[응급투석] HD',
        'Y0142': '[응급투석] CRRT',
        'Y0150': '[정신과적응급] 폐쇄병동입원',
        'Y0160': '[안과적수술] 응급',
        'Y0171': '[영상의학혈관중재] 성인',
        'Y0172': '[영상의학혈관중재] 영유아'
    };

    // ── 네트워크: 직접 → 공개 프록시 자동 폴백 ──────────────────
    var API_BASE = 'https://apis.data.go.kr/B552657/ErmctInfoInqireService';
    var PROXIES = [
        { name: '직접',                wrap: function (u) { return u; } },
        { name: '프록시(allorigins)', wrap: function (u) { return 'https://api.allorigins.win/raw?url=' + encodeURIComponent(u); } },
        { name: '프록시(corsproxy)', wrap: function (u) { return 'https://corsproxy.io/?url=' + encodeURIComponent(u); } }
    ];
    var netMode = 0;   // 최근 성공한 경로를 기억

    function buildUrl(endpoint, params) {
        var qs = new URLSearchParams();
        qs.set('serviceKey', cfg.serviceKey);
        Object.keys(params).forEach(function (k) { qs.set(k, params[k]); });
        return (cfg.apiBase || API_BASE) + '/' + endpoint + '?' + qs.toString();
    }
    async function fetchWith(url, wrapIdx) {
        var ctrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
        var timer = ctrl ? setTimeout(function () { ctrl.abort(); }, 15000) : null;
        try {
            var r = await fetch(PROXIES[wrapIdx].wrap(url),
                                ctrl ? { cache: 'no-store', signal: ctrl.signal }
                                     : { cache: 'no-store' });
            if (!r.ok) throw new Error('HTTP ' + r.status);
            var text = await r.text();
            if (text.indexOf('<') === -1) throw new Error('XML 아님');
            var doc = parseXml(text);
            if (doc.getElementsByTagName('parsererror').length >0) throw new Error('XML 파싱 실패');
            return doc;
        } finally { if (timer) clearTimeout(timer); }
    }
    async function apiGet(endpoint, params) {
        var url = buildUrl(endpoint, params);
        if (cfg.apiBase) return fetchWith(url, 0);       // 테스트 모드: 직접 고정
        var allowProxy = true;
        try { allowProxy = document.getElementById('allowProxy').checked; } catch (e) {}
        var maxIdx = allowProxy ? PROXIES.length - 1 : 0;
        var lastErr = null;
        for (var i = netMode; i <= maxIdx; i++) {
            try {
                var doc = await fetchWith(url, i);
                netMode = i;
                setStatus('연결: ' + PROXIES[i].name);
                return doc;
            } catch (e) { lastErr = e; }
        }
        netMode = 0;
        throw lastErr || new Error('연결 실패');
    }
    /* [ROOT-FIX 2026-D1] 시/도 파라미터 공용 폴백 (앱 _region_api_root 와 동일 정책)
       별칭 × (시군구 사용 → 생략) 순으로 시도해 item 이 있는 문서를 반환. */
    async function regionDoc(endpoint, sido, gugun, extra, k1, k2) {
        k1 = k1 || 'STAGE1'; k2 = k2 || 'STAGE2';
        var names = sido ? [sido].concat(SIDO_ALIAS[sido] || []) : [''];
        var modes = gugun ? [true, false] : [false];
        var last = null, i, j;
        for (i = 0; i < names.length; i++) {
            for (j = 0; j < modes.length; j++) {
                var p = { pageNo: '1', numOfRows: (modes[j] ? '100' : '400') };
                if (extra) Object.keys(extra).forEach(function (k) { p[k] = extra[k]; });
                p[k1] = names[i];
                if (modes[j]) p[k2] = gugun;
                try {
                    var doc = await apiGet(endpoint, p);
                    var rc = txt(doc, 'resultCode');
                    if (rc && rc !== '00') continue;
                    last = doc;
                    if (items(doc).length) return doc;
                } catch (e) { /* 다음 후보 */ }
            }
        }
        return last;
    }
    function setStatus(t) {
        try { document.getElementById('exStatus').textContent = t; } catch (e) {}
    }

    // ── 병원 데이터 파싱 (parse_hospital_data 이식) ──────────────
    function bedPair(item, availTag, totalTag) {
        return { avail: safeInt(txt2(item, availTag)), total: getHvs(item, totalTag) };
    }
    function txt2(item, tag) {                     // findtext: 부재 시 null 취급
        var l = item.getElementsByTagName(tag);
        return (l && l.length >0) ? (l[0].textContent || '') : null;
    }
    function parseHospitalData(item) {
        function eq(ayn, cnt) {
            var avail = ((txt2(item, ayn) || 'N').toUpperCase()).indexOf('Y') === 0;
            return { available: avail, count: avail ? safeInt(txt2(item, cnt)) : 0 };
        }
        var equipment = {
            'ct': eq('hvctayn', 'hvs27'),
            'mri': eq('hvmriayn', 'hvs28'),
            'angio': eq('hvangioayn', 'hvs29'),
            'ventilator': eq('hvventiayn', 'hvs30'),
            'ventilator_preemie': eq('hvventisoayn','hvs31'),
            'incubator': eq('hvincuayn', 'hvs32'),
            'crrt': eq('hvcrrtayn', 'hvs33'),
            'ecmo': eq('hvecmoayn', 'hvs34'),
            'hypothermia': eq('hvhypoayn', 'hvs35'),
            'hyperbaric': eq('hvoxyayn', 'hvs37')
        };
        var hv42raw = (txt2(item, 'hv42') || '').trim();
        return {
            'hpid': txt2(item, 'hpid') || '',
            'name': txt2(item, 'dutyName') || '알 수 없음',
            'dutyAddr': txt2(item, 'dutyAddr') || '',
            'dutyTel1': txt2(item, 'dutyTel1') || '',
            'dutyTel3': txt2(item, 'dutyTel3') || '',
            'update_time': txt2(item, 'hvidate') || '',
            'emcls': '',
            'emclsName': '',
            'level': '기관',
            'emergency': {
                'hvec': bedPair(item, 'hvec', 'HVS01'),
                'hv28': bedPair(item, 'hv28', 'HVS02'),
                'hv29': bedPair(item, 'hv29', 'HVS03'),
                'hv30': bedPair(item, 'hv30', 'HVS04')
            },
            'icu': {
                'hvicc': bedPair(item, 'hvicc', 'HVS17'),
                'hv2': bedPair(item, 'hv2', 'HVS06'),
                'hv3': bedPair(item, 'hv3', 'HVS07'),
                'hvncc': bedPair(item, 'hvncc', 'HVS08'),
                'hv32': bedPair(item, 'hv32', 'HVS09'),
                'hvcc': bedPair(item, 'hvcc', 'HVS11'),
                'hv6': bedPair(item, 'hv6', 'HVS12'),
                'hv34': bedPair(item, 'hv34', 'HVS15'),
                'hvccc': bedPair(item, 'hvccc', 'HVS16'),
                'hv35': bedPair(item, 'hv35', 'HVS18'),
                'hv31': bedPair(item, 'hv31', 'HVS05'),
                'hv33': bedPair(item, 'hv33', 'HVS10')
            },
            'isolation': {
                'hv13': bedPair(item, 'hv13', 'HVS46'),
                'hv14': bedPair(item, 'hv14', 'HVS47'),
                'hv15': bedPair(item, 'hv15', 'HVS48'),
                'hv16': bedPair(item, 'hv16', 'HVS49'),
                'hv22': bedPair(item, 'hv22', 'HVS54'),
                'hv23': bedPair(item, 'hv23', 'HVS55'),
                'hv24': bedPair(item, 'hv24', 'HVS56'),
                'hv25': bedPair(item, 'hv25', 'HVS57'),
                'hv26': bedPair(item, 'hv26', 'HVS58'),
                'hv27': bedPair(item, 'hv27', 'HVS59')
            },
            'other': {
                'hvoc': bedPair(item, 'hvoc', 'HVS22'),
                'hv42': { raw: hv42raw, total: getHvs(item, 'HVS26') }
            },
            'general': {
                'hvgc': bedPair(item, 'hvgc', 'HVS38'),
                'hv36': bedPair(item, 'hv36', 'HVS19'),
                'hv37': bedPair(item, 'hv37', 'HVS20'),
                'hv41': bedPair(item, 'hv41', 'HVS25')
            },
            'equipment': equipment,
            'exception': '정상'
        };
    }
    function hospitalLevel(emcls, name) {
        name = name || '';
        if (emcls === 'G001' || emcls === 'G002' || name.indexOf('권역') !== -1) return '권역';
        if (emcls === 'G003' || emcls === 'G004' || emcls === 'G006' || name.indexOf('센터') !== -1) return '센터';
        return '기관';
    }

    // ── 예외상황 메시지 처리 (이식) ──────────────────────────────
    function categorizeException(label, msg) {
        var full = (label + ' ' + msg).trim();
        var inq = ['문의', '확인 필요', '확인요', '연락', '전화'];
        for (var i = 0; i < inq.length; i++) if (msg.indexOf(inq[i]) !== -1) return '문의필요';
        if (full.indexOf('가능') !== -1) {
            var neg = ['불가', '부족', '제한', '불능', '불가능'];
            var hasNeg = false;
            for (var j = 0; j < neg.length; j++) if (full.indexOf(neg[j]) !== -1) { hasNeg = true; break; }
            if (!hasNeg) return '수용가능';
        }
        return '수용불가';
    }
    function resolveTypeLabel(mag, cod) {
        var label = (mag || '').trim();
        if (label) {
            if (label.charAt(0) === 'Y' && label.length <= 6 && /^\d+$/.test(label.slice(1)))
                label = Y_CODE_MAP.hasOwnProperty(label) ? Y_CODE_MAP[label] : label;
            else if (label.charAt(0) === 'D' && label.length === 4 && /^\d+$/.test(label.slice(1)))
                label = D_CODE_MAP.hasOwnProperty(label) ? D_CODE_MAP[label] : label;
        } else {
            var code = (cod || '').trim();
            label = Y_CODE_MAP[code] || D_CODE_MAP[code] || '';
        }
        return label;
    }
    function cleanMsg(m) {
        var msg = (m || '').trim();
        var pres = ['[응급] ', '[응급]'];
        for (var i = 0; i < pres.length; i++) {
            if (msg.indexOf(pres[i]) === 0) { msg = msg.slice(pres[i].length).trim(); break; }
        }
        return msg;
    }
    function _exSplitMsg(ln) {
        // '[수용불가] [성형외과] 본문' → {label:'성형외과', msg:'[수용불가] 본문'}
        var m1 = /^\[([^\]]*)\]\s*([\s\S]*)$/.exec(ln || '');
        if (!m1) return { label: '-', msg: String(ln || '') };
        var cat = (m1[1] || '').trim(), rest = (m1[2] || '').trim();
        var ST = { '수용불가': ['[수용 불가능]', '#dc3545'],
                   '수용가능': ['[ 수용 가능 ]', '#28a745'],
                   '문의필요': ['[ 문의 필요 ]', '#e67e00'] };
        var st = ST[cat] || [cat || '-', '#333'];
        var m2 = /^\[([^\]]*)\]\s*([\s\S]*)$/.exec(rest);
        if (m2) {
            var dept = (m2[1] || '').trim(), body = (m2[2] || '').trim();
            return { label: st[0], color: st[1], cat: cat, dept: dept,
                     msg: dept ? ('[' + dept + '] ' + body) : body };
        }
        return { label: st[0], color: st[1], cat: cat, dept: '', msg: rest };
    }
    async function fetchHospitalMsgs(hpid) {
        try {
            var msgs = [], page = 1;
            while (true) {
                var doc = await apiGet('getEmrrmSrsillDissMsgInqire',
                                       { HPID: hpid.trim(), pageNo: String(page), numOfRows: '100' });
                var its = items(doc);
                if (its.length === 0) break;
                var tc = parseInt(txt(doc, 'totalCount') || '0', 10); if (isNaN(tc)) tc = 0;
                for (var i = 0; i < its.length; i++) {
                    var it = its[i];
                    var blk = (txt(it, 'symBlkMsg') || '').trim();
                    var mag = (txt(it, 'symTypCodMag') || '').trim();
                    var cod = (txt(it, 'symTypCod') || '').trim();
                    var trt = (txt(it, 'trtPrtCodMag') || '').trim();
                    var label = resolveTypeLabel(mag, cod);
                    var cm = cleanMsg(blk);
                    // 진료과목 1순위, 없으면 질환명/응급실 (파이썬판과 동일 규칙)
                    var dept = trt || label || '응급실';
                    if (!cm && !dept) continue;
                    // 본문이 이미 '[..]' 로 시작하면 과목 중복이므로 접두 생략
                    var content = !cm ? ('[' + dept + ']')
                                : (cm.charAt(0) === '[' ? cm : ('[' + dept + '] ' + cm));
                    var cat = categorizeException(label, cm);
                    msgs.push('[' + cat + '] ' + content);
                }
                if (its.length < 100 || page * 100 >= tc) break;
                page += 1;
            }
            if (msgs.length) {
                var seen = {}, uniq = [];
                for (var k = 0; k < msgs.length; k++)
                    if (!seen[msgs[k]]) { seen[msgs[k]] = 1; uniq.push(msgs[k]); }
                return uniq.join('\n');
            }
            return ' 정상';
        } catch (e) { return ' 정상'; }
    }
    async function fetchBasicInfo(hpid) {
        try {
            var doc = await apiGet('getEgytBassInfoInqire', { HPID: hpid.trim(), numOfRows: '1' });
            return (txt(doc, 'dutyInf') || '').trim();
        } catch (e) { return ''; }
    }
    async function fetchKiosk(sido, gugun) {
        try {
            var doc = await regionDoc('getSrsillDissAceptncPosblInfoInqire', sido, gugun);
            if (!doc) return {};
            var res = {};
            var its = items(doc);
            for (var i = 0; i < its.length; i++) {
                var it = its[i];
                var hp = (txt(it, 'hpid') || '').trim();
                if (!hp) continue;
                var data = {};
                Object.keys(MKIOSK_MAP).forEach(function (code) {
                    var val = (txt2b(it, code) || '').trim();
                    var msg = (txt2b(it, code + 'Msg') || '').trim();
                    if (val) data[code] = { val: val, msg: msg };
                });
                res[hp] = data;
            }
            return res;
        } catch (e) { return {}; }
    }
    function txt2b(item, tag) {
        var l = item.getElementsByTagName(tag);
        return (l && l.length >0) ? (l[0].textContent || '') : '';
    }

    // ── 렌더링 (generate_comparison_html 바이트 동일 이식) ────────
    function breakLabel(t) { return t.replace(/\s*\(/g, '<br>('); }
    function shouldShowRow(hd, catKey, key) {
        for (var i = 0; i < hd.length; i++) {
            var bd = (hd[i][catKey] || {})[key] || {};
            if ((bd.avail !== undefined ? bd.avail : -1) !== -1) return true;
            if ((bd.total !== undefined ? bd.total : -1) >= 0) return true;
        }
        return false;
    }
    function shouldShowEquipment(hd, ek) {
        var st = [];
        for (var i = 0; i < hd.length; i++) {
            var ed = (hd[i].equipment || {})[ek] || {};
            st.push(ed.available === undefined ? false : ed.available);
        }
        var allSame = st.every(function (s) { return s === st[0]; });
        if (allSame && !st[0]) return false;
        return true;
    }
    function formatBedCell(bd) {
        var avail = bd.avail, total = bd.total;
        if (avail === -1 && total <= 0)
            return '<td class="bed-cell"><div class="bed-info"><div class="bed-numbers" style="color:#000;">-</div></div></td>';
        if (avail < 0) {
            var dt = total >0 ? (avail + '/' + total + ' (' + pyRound(avail / total * 100) + '%)') : String(avail);
            return '<td class="bed-cell"><div class="bed-info"><div class="bar-container">'
                 + '<div class="bar" style="width:0%;background:#cccccc;"></div>'
                 + '<div class="bed-text-overlay" style="color:#d32f2f;font-weight:900;">' + dt + '</div>'
                 + '</div></div></td>';
        }
        if (total <= 0) {
            if (avail === 0)
                return '<td class="bed-cell"><div class="bed-info"><div class="bar-container">'
                     + '<div class="bar bar-red" style="width:0%"></div>'
                     + '<div class="bed-text-overlay red-text">0</div>'
                     + '</div></div></td>';
            return '<td class="bed-cell"><div class="bed-info">'
                 + '<div class="bed-numbers"><span class="available">' + avail + '</span></div>'
                 + '</div></td>';
        }
        var pct = pyRound(avail / total * 100);
        var bw = Math.min(100, pct);
        var bc, tc;
        if (pct >= 50)      { bc = 'bar-green'; tc = 'green-text'; }
        else if (pct >= 20) { bc = 'bar-yellow'; tc = 'yellow-text'; }
        else                { bc = 'bar-red'; tc = 'red-text'; }
        var dtext = avail + '/' + total + ' (' + pct + '%)';
        return '<td class="bed-cell"><div class="bed-info"><div class="bar-container">\n'
             + ' <div class="bar ' + bc + '" style="width:' + bw + '%"></div>\n'
             + ' <div class="bed-text-overlay ' + tc + '">' + dtext + '</div>\n'
             + ' </div></div></td>';
    }
    function formatBirthRoomCell(bd) {
        var raw = bd.raw || '', total = (bd.total === undefined ? -1 : bd.total);
        if (!raw)
            return '<td class="bed-cell"><div class="bed-info"><div class="bed-numbers" style="color:#000;">-</div></div></td>';
        var rawUp = raw.toUpperCase();
        if (/^[+-]?\d+$/.test(raw.trim()))
            return formatBedCell({ avail: parseInt(raw.trim(), 10), total: total });
        if (raw.indexOf('/') !== -1 && rawUp.indexOf('N') !== 0) {
            return '<td class="bed-cell"><div class="bed-info"><div class="bar-container">\n'
                 + ' <div class="bar bar-green" style="width:100%"></div>\n'
                 + ' <div class="bed-text-overlay green-text">' + raw + '</div>\n'
                 + ' </div></div></td>';
        }
        if (rawUp.indexOf('Y') === 0) {
            var ts = total >0 ? ('/' + total) : '';
            return '<td class="bed-cell"><div class="bed-info"><div class="bar-container">\n'
                 + ' <div class="bar bar-green" style="width:100%"></div>\n'
                 + ' <div class="bed-text-overlay green-text">가능' + ts + '</div>\n'
                 + ' </div></div></td>';
        }
        if (rawUp.indexOf('N') === 0) {
            return '<td class="bed-cell"><div class="bed-info"><div class="bar-container">\n'
                 + ' <div class="bar bar-red" style="width:0%"></div>\n'
                 + ' <div class="bed-text-overlay red-text">불가</div>\n'
                 + ' </div></div></td>';
        }
        return '<td class="bed-cell"><div class="bed-info"><div class="bar-container">\n'
             + ' <div class="bar bar-green" style="width:100%"></div>\n'
             + ' <div class="bed-text-overlay green-text">' + raw + '</div>\n'
             + ' </div></div></td>';
    }
    function kioskCellVal(valStr) {
        var v = (valStr || '').trim();
        if (v === 'Y' || v === 'Y ' || v === '0')
            return ['ok', '<span style="font-weight:900;font-size:1.34em;color:#4CAF50;line-height:1;display:inline-block;">O</span>'];
        if (v.indexOf('불가') !== -1)
            return ['ng', '<span style="font-size:1.03em;color:#C62828;font-weight:700;line-height:1;">X</span>'];
        if (v === '정보미제공')
            return ['na', '<span style="color:#888;font-size:1.0em;">–</span>'];
        if (v) return ['partial', '<span style="color:#e65100;font-size:1.1em;"></span>'];
        return ['none', ''];
    }
    function renderComparison(hd, withBells) {
        var n = hd.length;
        var html = '<div class="comparison-wrapper"><table class="comparison-table"><thead><tr>'
                 + '<th style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);">항목</th>';
        var levelBg = {
            '권역': 'linear-gradient(135deg, #192799 0%, #4217d1 50%, #7955e6 100%)',
            '센터': 'linear-gradient(135deg, #013a3a 0%, #0d6655 50%, #0f8068 100%)',
            '기관': 'linear-gradient(135deg, #1a2840 0%, #2c4570 50%, #3a5a90 100%)'
        };
        hd.forEach(function (h) {
            var name = h.name, nameLength = h.name.length, hasLb = false;
            if (name.indexOf('대학교의과대학') !== -1) {
                var ix = name.indexOf('의과대학');
                name = name.slice(0, ix) + '의과대학<br>' + name.slice(ix + 4);
                hasLb = true;
            }
            if (!hasLb) {
                var kws = ['재단법인', '재단', '대학교', '기념', '남도', '북도'];
                for (var i = 0; i < kws.length; i++) {
                    var kw = kws[i], j = name.indexOf(kw);
                    if (j !== -1) {
                        name = name.slice(0, j) + kw + '<br>' + name.slice(j + kw.length);
                        hasLb = true; break;
                    }
                }
            }
            var nc = nameLength >20 ? 'hospital-name very-long-name'
                   : (nameLength >12 ? 'hospital-name long-name' : 'hospital-name');
            var u = h.update_time || '';
            var us = (u && u.length >= 12)
                ? "<br><span style='font-size:0.7em;font-weight:normal;'>" + u.slice(0, 4) + '-' + u.slice(4, 6) + '-' + u.slice(6, 8) + ' ' + u.slice(8, 10) + ':' + u.slice(10, 12) + '</span>'
                : '';
            var bg = levelBg[h.level || '기관'] || levelBg['기관'];
            html += '<th class="' + nc + '" style="background:' + bg + ';">' + name + us + '</th>';
        });
        html += '</tr></thead><tbody>';

        var cats = [
            ['응급실', [['일반','hvec'],['소아','hv28'],['음압격리','hv29'],['일반격리','hv30']]],
            ['중환자실', [['일반','hvicc'],['내과','hv2'],['외과','hv3'],['신생아','hvncc'],['소아','hv32'],['신경과','hvcc'],['신경외과','hv6'],['심장내과','hv34'],['흉부외과','hvccc'],['음압격리','hv35'],['응급전용','hv31'],['응급전용(소아)','hv33']]],
            ['격리진료구역', [['음압격리','hv13'],['일반격리','hv14'],['소아음압','hv15'],['소아일반','hv16'],['감염병전담(중환자실)','hv22'],['감염병전담(중환자실 음압)','hv23'],['감염[중증]','hv24'],['감염[준-중증]','hv25'],['감염[중등증]','hv26'],['코호트격리','hv27']]],
            ['입원실', [['일반','hvgc'],['응급전용','hv36'],['응급전용(소아)','hv37'],['음압격리','hv41']]]
        ];
        var catKeyMap = { '응급실': 'emergency', '중환자실': 'icu', '격리진료구역': 'isolation', '입원실': 'general' };
        cats.forEach(function (c) {
            var category = c[0], its = c[1];
            var catKey = catKeyMap[category];
            var visible = its.filter(function (p) { return shouldShowRow(hd, catKey, p[1]); });
            if (visible.length) {
                html += '<tr><td colspan="' + (n + 1) + '" class="category-header">' + category + '</td></tr>';
                visible.forEach(function (p) {
                    html += '<tr><td class="item-label">' + breakLabel(p[0]) + '</td>';
                    hd.forEach(function (h) { html += formatBedCell(h[catKey][p[1]]); });
                    html += '</tr>';
                });
            }
        });

        var otherDefs = [['수술실', 'hvoc', false], ['분만실', 'hv42', true]];
        var otherVisible = [];
        otherDefs.forEach(function (d) {
            var key = d[1], isBirth = d[2], visible = false;
            for (var i = 0; i < hd.length; i++) {
                var bd = (hd[i].other || {})[key] || {};
                if (isBirth) { if (bd.raw) { visible = true; break; } }
                else if ((bd.avail !== undefined ? bd.avail : -1) !== -1 || (bd.total !== undefined ? bd.total : -1) >= 0) { visible = true; break; }
            }
            if (visible) otherVisible.push(d);
        });
        if (otherVisible.length) {
            html += '<tr><td colspan="' + (n + 1) + '" class="category-header">기타</td></tr>';
            otherVisible.forEach(function (d) {
                html += '<tr><td class="item-label">' + breakLabel(d[0]) + '</td>';
                hd.forEach(function (h) {
                    html += d[2] ? formatBirthRoomCell(h.other[d[1]]) : formatBedCell(h.other[d[1]]);
                });
                html += '</tr>';
            });
        }

        var eqList = [['CT','ct'],['MRI','mri'],['혈관촬영기','angio'],['인공호흡기','ventilator'],
                      ['인공호흡기(조산아)','ventilator_preemie'],['인큐베이터','incubator'],
                      ['CRRT','crrt'],['ECMO','ecmo'],['고압산소치료기','hyperbaric'],['중심체온조절유도기','hypothermia']];
        var visEq = eqList.filter(function (p) { return shouldShowEquipment(hd, p[1]); });
        if (visEq.length) {
            html += '<tr><td colspan="' + (n + 1) + '" class="category-header">의료장비</td></tr>';
            visEq.forEach(function (p) {
                html += '<tr><td class="item-label">' + breakLabel(p[0]) + '</td>';
                hd.forEach(function (h) {
                    var ed = (h.equipment || {})[p[1]] || {};
                    var ea = ed.available === undefined ? false : ed.available;
                    var ec = ed.count === undefined ? 1 : ed.count;
                    if (ea) html += '<td class="equipment-cell equipment-available">' + (ec >0 ? ec : 1) + '</td>';
                    else    html += '<td class="equipment-cell equipment-unavailable" style="font-size:1.03em;">X</td>';
                });
                html += '</tr>';
            });
        }

        var kioskVisible = [];
        Object.keys(MKIOSK_MAP).forEach(function (code) {
            var has = hd.some(function (h) { return (h.kiosk || {}).hasOwnProperty(code); });
            if (has) kioskVisible.push([code, MKIOSK_MAP[code]]);
        });
        if (kioskVisible.length) {
            html += '<tr><td colspan="' + (n + 1) + '" class="category-header">중증질환 수용가능 <a href="https://www.e-gen.or.kr" target="_blank" style="font-size:0.72em;color:#c5d8ff;text-decoration:underline;">E-Gen 공식확인</a></td></tr>';
            kioskVisible.forEach(function (p) {
                html += '<tr><td class="item-label">' + breakLabel(p[1]) + '</td>';
                hd.forEach(function (h) {
                    var kd = (h.kiosk || {})[p[0]] || {};
                    var sv = kioskCellVal(kd.val || '');
                    var status = sv[0], icon = sv[1];
                    if (!icon) html += '<td class="kiosk-cell" style="color:#888;">–</td>';
                    else {
                        var mc = { ok: '#388e3c', ng: '#c62828', partial: '#e65100', na: '#888' }[status] || '#777';
                        html += '<td class="kiosk-cell">' + icon
                              + ((kd.msg || '') ? ('<div class="kiosk-msg" style="color:' + mc + ';">' + kd.msg + '</div>') : '')
                              + '</td>';
                    }
                });
                html += '</tr>';
            });
        }

        html += '<tr><td colspan="' + (n + 1) + '" class="category-header">예외상황</td></tr>';
        html += '<tr><td class="item-label">예외상황</td>';
        hd.forEach(function (h) {
            var exc = (h.exception === undefined || h.exception === null)
                ? '정보 없음' : String(h.exception);
            var excT = exc.trim();
            if (excT === '' || excT === '정상' || excT === '정보 없음') {
                var dutyRaw = (h.duty_inf || '').trim();
                if (dutyRaw) {
                    var dl = dutyRaw.replace(/，/g, ',').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
                    var dh = '<div style="color:#5a6a7e;font-weight:700;margin-left:5px;">상시 운영 제한:</div>';
                    dl.forEach(function (di) { dh += '<div style="margin-left:10px;color:#5a6a7e;line-height:1.3;">' + di + '</div>'; });
                    html += '<td class="exception-cell exception-warning" style="text-align:left;padding:6px;vertical-align:top;">' + dh + '</td>';
                } else {
                    html += '<td class="exception-cell exception-ok"> <span style="color:#000;font-weight:normal;">없음</span></td>';
                }
            } else {
                var un = [], av = [], inq = [];
                exc.split('\n').forEach(function (ln) {
                    ln = ln.trim();
                    if (!ln) return;
                    if (ln.indexOf('[수용불가] ') === 0) un.push(ln.slice(7));
                    else if (ln.indexOf('[수용가능] ') === 0) av.push(ln.slice(7));
                    else if (ln.indexOf('[문의필요] ') === 0) inq.push(ln.slice(7));
                    else un.push(ln);
                });
                var dutyRaw2 = (h.duty_inf || '').trim();
                var dl2 = dutyRaw2 ? dutyRaw2.replace(/，/g, ',').split(',').map(function (s) { return s.trim(); }).filter(Boolean) : [];
                var res = [];
                // 앱(generate_comparison_html)과 동일 서식:
                //  그룹 제목 같은 색 밑줄 / 그룹 사이 1줄 공백 / [진료과목]만 검정
                var _grp = function (title, color, lines, deptBlack) {
                    if (!lines.length) return;
                    if (res.length) res.push('<div style="height:1.3em;"></div>');
                    res.push('<div style="color:' + color + ';font-weight:700;margin-left:5px;'
                           + 'text-decoration:underline;text-decoration-color:' + color + ';">'
                           + title + '</div>');
                    lines.forEach(function (it) {
                        /* [2026-H2] 과목=그룹색 / 세부내용=검정 (앱과 동일) */
                        var body = it, lineCol = color;
                        if (deptBlack !== false) {
                            lineCol = '#000';
                            var mm = /^\[([^\]]*)\]\s*([\s\S]*)$/.exec(it || '');
                            body = mm
                                ? '<span style="color:' + color + ';font-weight:700;">['
                                  + mm[1] + ']</span> <span style="color:#000;">' + mm[2] + '</span>'
                                : '<span style="color:#000;">' + it + '</span>';
                        }
                        res.push('<div style="margin-left:10px;color:' + lineCol
                               + ';line-height:1.3;">' + body + '</div>');
                    });
                };
                _grp('수용불가:', '#dc3545', un, true);
                _grp('수용가능:', '#28a745', av, true);
                _grp('※ 문의 필요:', '#e67e00', inq, true);
                _grp('상시 운영 제한:', '#5a6a7e', dl2, false);
                var fmt = res.length ? res.join('') : exc;
                html += '<td class="exception-cell exception-warning" style="text-align:left;padding:6px;vertical-align:top;">' + fmt + '</td>';
            }
        });
        html += '</tr></tbody></table></div>';
        return html;
    }

    // ── 파이프라인: 지역/메시지/기본정보/kiosk 병렬 조회 → 조립 ──
    async function loadAll() {
        var regions = [], seenR = {};
        cfg.entries.forEach(function (e) {
            var k = e.sido + '|' + e.gugun;
            if (!seenR[k]) { seenR[k] = { sido: e.sido, gugun: e.gugun }; regions.push(seenR[k]); }
        });
        var hpids = [], seenH = {};
        cfg.entries.forEach(function (e) { if (!seenH[e.hpid]) { seenH[e.hpid] = 1; hpids.push(e.hpid); } });

        async function regionTask(r) {
            var localMap = {}, err = null;
            try {
                var doc = await regionDoc('getEmrrmRltmUsefulSckbdInfoInqire', r.sido, r.gugun);
                if (!doc)
                    return { map: localMap, err: r.sido + ' ' + r.gugun + ': 병상 API 응답 없음(전 후보 실패)' };
                var rc = txt(doc, 'resultCode');
                if (rc && rc !== '00')
                    return { map: localMap, err: r.sido + ' ' + r.gugun + ': API 오류 (' + rc + ') ' + (txt(doc, 'resultMsg') || '') };
                var target = {};
                cfg.entries.forEach(function (e) { if (e.sido === r.sido && e.gugun === r.gugun) target[e.hpid] = 1; });
                items(doc).forEach(function (it) {
                    var hp = (txt(it, 'hpid') || '').trim();
                    if (target[hp]) {
                        var h = parseHospitalData(it);
                        h.sido = r.sido; h.gugun = r.gugun;
                        localMap[hp] = h;
                    }
                });
                try {
                    var ld = await regionDoc('getEgytListInfoInqire', r.sido, r.gugun,
                                             null, 'Q0', 'Q1');
                    items(ld || doc).forEach(function (li) {
                        var lh = (txt(li, 'hpid') || '').trim();
                        if (localMap[lh]) {
                            var emcls = (txt(li, 'dutyEmcls') || '').trim();
                            localMap[lh].emcls = emcls;
                            localMap[lh].level = hospitalLevel(emcls, localMap[lh].name);
                        }
                    });
                } catch (le) { /* 목록 API 실패는 무시 (원본 동일) */ }
            } catch (e) { err = r.sido + ' ' + r.gugun + ': ' + (e && e.message ? e.message : e); }
            return { map: localMap, err: err };
        }

        var regionP = regions.map(regionTask);
        var msgP = {}, basicP = {};
        hpids.forEach(function (hp) { msgP[hp] = fetchHospitalMsgs(hp); basicP[hp] = fetchBasicInfo(hp); });
        var kioskP = regions.map(function (r) { return fetchKiosk(r.sido, r.gugun); });

        var hpidMap = {}, errors = [];
        var regionRes = await Promise.all(regionP);
        regionRes.forEach(function (rr) {
            Object.keys(rr.map).forEach(function (k) { hpidMap[k] = rr.map[k]; });
            if (rr.err) errors.push(rr.err);
        });

        var hd = [];
        cfg.entries.forEach(function (e) { if (hpidMap[e.hpid]) hd.push(hpidMap[e.hpid]); });

        var msgs = {};
        for (var i = 0; i < hpids.length; i++) {
            try { msgs[hpids[i]] = await msgP[hpids[i]]; } catch (e) { msgs[hpids[i]] = ' 정상'; }
        }
        hd.forEach(function (h) { h.exception = msgs.hasOwnProperty(h.hpid) ? msgs[h.hpid] : ' 정상'; });
        for (var j = 0; j < hpids.length; j++) {
            var duty = '';
            try { duty = await basicP[hpids[j]]; } catch (e) { duty = ''; }
            for (var m = 0; m < hd.length; m++) if (hd[m].hpid === hpids[j]) { hd[m].duty_inf = duty; break; }
        }
        var kioskAll = {};
        var kioskRes = await Promise.all(kioskP.map(function (p) { return p.catch(function () { return {}; }); }));
        kioskRes.forEach(function (kr) { Object.keys(kr).forEach(function (k) { kioskAll[k] = kr[k]; }); });
        hd.forEach(function (h) { h.kiosk = kioskAll.hasOwnProperty(h.hpid) ? kioskAll[h.hpid] : {}; });

        return { hd: hd, errors: errors };
    }


    // ── 전국 로스터 / 병상 / 포화도 (서버판과 동일 규격) ──────────
    /* [일원화 2026-H1] 행정구역 표는 파이썬 DISTRICTS/_SIDO_ALIAS 에서 주입한다.
       JS 쪽에 별도 하드코딩을 두면 개편 때마다 두 곳이 어긋난다
       (전남광주통합특별시 누락의 재발 방지). */
    var _ADMIN = __ADMIN__;
    var SIDO_LIST = _ADMIN.list, SIDO_ALIAS = _ADMIN.alias,
        GU_MAP = _ADMIN.gu, SIDO_LEGACY = _ADMIN.legacy,
        UNION_SIDO = _ADMIN.union;
    var ER_TAGS   = [['hvec','HVS01']];
    var WARD_TAGS = [['hvgc','HVS38'],['hv36','HVS19'],['hv37','HVS20'],['hv41','HVS25']];
    var ICU_TAGS  = [['hvicc','HVS17'],['hv2','HVS06'],['hv3','HVS07'],['hvncc','HVS08'],
                     ['hv32','HVS09'],['hvcc','HVS11'],['hv6','HVS12'],['hv34','HVS15'],
                     ['hvccc','HVS16'],['hv35','HVS18'],['hv31','HVS05'],['hv33','HVS10']];
    // ※ 설계 상수 (검증된 문헌값 아님)
    var ADM_RATE = { '권역':0.35, '센터':0.28, '기관':0.20 };
    var WI_W = 0.74, WI_I = 0.26, THETA = 2.0, BACI_CAP = 4.0;
    var _rosterCache = null, _satCache = null, _bedCache = {};

    /* [ROOT-FIX 2026-H1] 접두 매칭은 2자 약칭('전남')이 신설 시/도
       ('전남광주통합특별시')를 삼켜 전량 오분류를 일으켰다.
       → 주소 첫 토큰의 '정확일치'를 1순위로, 접두 폴백은 3자 이상만 허용.
       (앱 _sido_of 와 동일 규칙) */
    var _sidoExact = null, _sidoPre = null;
    function sidoOf(addr) {
        if (!_sidoExact) {
            _sidoExact = {};
            SIDO_LIST.forEach(function (k) { _sidoExact[k] = k; });
            SIDO_LIST.forEach(function (k) {
                (SIDO_ALIAS[k] || []).forEach(function (a) {
                    if (!_sidoExact[a]) _sidoExact[a] = k;
                });
            });
            Object.keys(SIDO_LEGACY || {}).forEach(function (o) {
                if (SIDO_LIST.indexOf(SIDO_LEGACY[o]) !== -1) _sidoExact[o] = SIDO_LEGACY[o];
            });
            _sidoPre = Object.keys(_sidoExact).filter(function (k) { return k.length >= 3; })
                             .sort(function (a, b) { return b.length - a.length; });
        }
        var h = String(addr || '').trim();
        if (!h) return '';
        var tok = h.split(/\s+/)[0];
        if (_sidoExact[tok]) return _sidoExact[tok];
        for (var i = 0; i < _sidoPre.length; i++)
            if (tok.indexOf(_sidoPre[i]) === 0) return _sidoExact[_sidoPre[i]];
        return '';
    }
    function splitGugun(sido, addr) {
        var toks = String(addr || '').split(/\s+/), cand = [], i;
        try { if (typeof districts !== 'undefined' && districts[sido]) cand = districts[sido]; } catch (e) {}
        for (i = 0; i < toks.length && i < 4; i++) if (cand.indexOf(toks[i]) !== -1) return toks[i];
        for (i = 0; i < toks.length && i < 4; i++)
            if (toks[i] !== sido && /[시군구]$/.test(toks[i])) return toks[i];
        if (sido === '세종특별자치시') return '세종특별자치시';
        return cand.length === 1 ? cand[0] : '';
    }
    function listRows(doc, hint) {
        var out = [];
        items(doc).forEach(function (it) {
            var hpid = (txt(it, 'hpid') || '').trim();
            if (!hpid) return;
            var nm = (txt(it, 'dutyName') || '').trim() || '알 수 없음';
            var ad = (txt(it, 'dutyAddr') || '').trim();
            var em = (txt(it, 'dutyEmcls') || '').trim();
            var sd = sidoOf(ad) || hint || '';
            out.push({ hpid: hpid, name: nm, dutyAddr: ad,
                dutyTel1: (txt(it, 'dutyTel1') || '').trim(),
                dutyTel3: (txt(it, 'dutyTel3') || '').trim(),
                emcls: em, emclsName: (txt(it, 'dutyEmclsName') || '').trim(),
                level: hospitalLevel(em, nm), sido: sd, gugun: splitGugun(sd, ad) });
        });
        return out;
    }
    /* [ROOT-FIX 2026-G1] 시/도 파라미터를 쓰지 않는 전국 스냅샷.
       Q0/STAGE1 필터가 광주광역시 등에서 0건만 주는 API 결함의 최종 폴백.
       (앱 _nat_list_cached / _nat_beds_cached 와 동일 정책) */
    var _natList = null, _natListT = 0, _natBeds = null, _natBedsT = 0;
    async function natListCached(force) {
        if (!force && _natList && Date.now() - _natListT < 300000) return _natList;
        var rows = [], page = 1, total = -1;
        try {
            while (page <= 4) {
                var doc = await apiGet('getEgytListInfoInqire',
                    { pageNo: String(page), numOfRows: '500' });
                var tc = parseInt(txt(doc, 'totalCount') || '-1', 10);
                if (!isNaN(tc)) total = tc;
                var got = listRows(doc, '');
                rows = rows.concat(got);
                if (!got.length || (total >= 0 && page * 500 >= total)) break;
                page += 1;
            }
        } catch (e) { /* 부분 성공분만 사용 */ }
        if (rows.length) { _natList = rows; _natListT = Date.now(); }
        return _natList || [];
    }
    async function natBedsCached(force) {
        if (!force && _natBeds && Date.now() - _natBedsT < 90000) return _natBeds;
        var m = {}, page = 1, total = -1;
        try {
            while (page <= 4) {
                var d = await apiGet('getEmrrmRltmUsefulSckbdInfoInqire',
                    { pageNo: String(page), numOfRows: '500' });
                var tc2 = parseInt(txt(d, 'totalCount') || '-1', 10);
                if (!isNaN(tc2)) total = tc2;
                var n0 = Object.keys(m).length;
                items(d).forEach(function (it) {
                    var hp = (txt(it, 'hpid') || '').trim();
                    if (!hp || m[hp]) return;
                    var yn = function (t) {
                        return ((txt(it, t) || 'N') + '').trim().toUpperCase().charAt(0) === 'Y';
                    };
                    m[hp] = { er: bedObj(sumBeds(it, ER_TAGS)),
                              ward: bedObj(sumBeds(it, WARD_TAGS)),
                              icu: bedObj(sumBeds(it, ICU_TAGS)),
                              eq: { crrt: yn('hvcrrtayn'), ecmo: yn('hvecmoayn'),
                                    ttm: yn('hvhypoayn'), hbo: yn('hvoxyayn') },
                              tel3: (txt(it, 'dutyTel3') || '').trim(),
                              upd: (txt(it, 'hvidate') || '').trim() };
                });
                if (Object.keys(m).length === n0
                    || (total >= 0 && page * 500 >= total)) break;
                page += 1;
            }
        } catch (e) { /* 부분 성공분만 사용 */ }
        if (Object.keys(m).length) { _natBeds = m; _natBedsT = Date.now(); }
        return _natBeds || {};
    }
    async function listBySido(sd) {
        var names = [sd].concat(SIDO_ALIAS[sd] || []);
        var uni = (UNION_SIDO || []).indexOf(sd) !== -1, acc = [], accSeen = {};
        for (var i = 0; i < names.length; i++) {
            try {
                var doc = await apiGet('getEgytListInfoInqire',
                    { Q0: names[i], pageNo: '1', numOfRows: '500' });
                var r = listRows(doc, sd);
                if (r.length) {
                    if (!uni) return r;
                    r.forEach(function (x) {
                        if (!accSeen[x.hpid]) { accSeen[x.hpid] = 1; acc.push(x); }
                    });
                }
            } catch (e) { /* 다음 별칭 */ }
        }
        if (uni && acc.length) return acc;
        // ── Q0 가 0건만 주는 시/도(광주광역시 등) → 구/군 단위로 나눠 조회 ──
        //    앱(_fetch_sido_list) 과 동일한 폴백
        var gus = GU_MAP[sd] || [];
        for (var n = 0; n < names.length && gus.length; n++) {
            var merged = [], seen = {};
            for (var g = 0; g < gus.length; g++) {
                try {
                    var d2 = await apiGet('getEgytListInfoInqire',
                        { Q0: names[n], Q1: gus[g], pageNo: '1', numOfRows: '200' });
                    listRows(d2, sd).forEach(function (r2) {
                        if (!seen[r2.hpid]) { seen[r2.hpid] = 1; merged.push(r2); }
                    });
                } catch (e) { /* 개별 구 실패는 무시 */ }
            }
            if (merged.length) return merged;
        }
        /* ③ Q0/Q1 필터 자체가 고장난 경우 → 전국 스냅샷에서 주소로 추출 */
        var nat = await natListCached(false);
        var pick = nat.filter(function (h) { return h.sido === sd; });
        if (pick.length) return pick;
        return [];
    }
    async function fetchAllHospitals(force) {
        if (!force && _rosterCache)
            return { success: true, cached: true, count: _rosterCache.length,
                     failed: [], hospitals: _rosterCache };
        var rows = [], fails = [], b;
        for (b = 0; b < SIDO_LIST.length; b += 6) {
            setStatus('전국 목록 ' + Math.min(b + 6, SIDO_LIST.length) + '/' + SIDO_LIST.length);
            var res = await Promise.all(SIDO_LIST.slice(b, b + 6).map(function (sd) {
                return listBySido(sd).then(function (r) { return { s: sd, r: r }; })
                    .catch(function (e) { return { s: sd, err: e }; });
            }));
            res.forEach(function (x) {
                if (x.err) { fails.push(x.s + ': ' + (x.err && x.err.message ? x.err.message : x.err)); return; }
                rows = rows.concat(x.r);
            });
        }
        if (!rows.length)
            return { success: false, error: '전국 목록 조회 실패: ' + fails.slice(0, 3).join('; ') };
        var seen = {}, uniq = [];
        rows.forEach(function (h) { if (!seen[h.hpid]) { seen[h.hpid] = 1; uniq.push(h); } });
        uniq.sort(function (a, c) {
            var ka = a.sido + a.gugun + a.name, kc = c.sido + c.gugun + c.name;
            return ka < kc ? -1 : (ka >kc ? 1 : 0);
        });
        _rosterCache = uniq;
        setStatus('전국 ' + uniq.length + '개');
        return { success: true, cached: false, count: uniq.length, failed: fails, hospitals: uniq };
    }
    async function fetchRegionHospitals(sido, gugun) {
        if (!sido) return { success: false, error: '시/도를 선택해주세요.' };
        var all = await fetchAllHospitals(false);
        var rows = [];
        if (all.success) {
            rows = all.hospitals.filter(function (h) {
                if (h.sido !== sido) return false;
                if (!gugun) return true;
                return h.gugun === gugun || String(h.dutyAddr || '').indexOf(gugun) !== -1;
            });
        }
        if (!rows.length) {
            try {
                var p = { Q0: sido, pageNo: '1', numOfRows: '300' };
                if (gugun) p.Q1 = gugun;
                rows = listRows(await apiGet('getEgytListInfoInqire', p), sido);
                if (!rows.length) rows = await listBySido(sido);   // 별칭+구단위 폴백
                if (gugun) rows = rows.filter(function (h) {
                    return h.gugun === gugun || String(h.dutyAddr || '').indexOf(gugun) !== -1; });
            } catch (e) { return { success: false, error: '서버 오류: ' + (e && e.message ? e.message : e) }; }
        }
        rows.sort(function (a, c) { return a.name < c.name ? -1 : 1; });
        return { success: true, hospitals: rows };
    }

    function sumBeds(item, tags) {
        var a = 0, t = 0;
        tags.forEach(function (p) {
            var tt = getHvs(item, p[1]);
            if (tt === null || tt === undefined || tt <= 0) return;
            var rawv = String(txt2(item, p[0]) || '').trim();
            var av = /^[+-]?\d+$/.test(rawv) ? parseInt(rawv, 10) : 0;
            t += tt; a += av;
        });
        return [a, t];
    }
    function bedObj(p) {
        var a = p[0], t = p[1];
        return { a: a, t: t, r: t >0 ? Math.round(Math.max(0, (t - a) / t) * 10000) / 10000 : null };
    }
    function baci(er, ward, icu, adm) {
        var E = er.r; if (E === null) return null;
        var W = ward.r, I = icu.r, D;
        if (W !== null && I !== null) D = WI_W * W + WI_I * I;
        else if (W !== null) D = W; else if (I !== null) D = I; else D = 0;
        D = Math.min(1, Math.max(0, D));
        var L = E / (1 - adm * Math.pow(D, THETA));
        return Math.round(Math.min(BACI_CAP, L) * 10000) / 10000;
    }
    async function bedsBySido(sd, force) {
        var c = _bedCache[sd];
        if (!force && c && Date.now() - c.t < 90000) return c.m;
        var names = [sd].concat(SIDO_ALIAS[sd] || []);
        var uni = (UNION_SIDO || []).indexOf(sd) !== -1, accM = {}, accN = 0;
        for (var i = 0; i < names.length; i++) {
            try {
                var doc = await apiGet('getEmrrmRltmUsefulSckbdInfoInqire',
                    { STAGE1: names[i], pageNo: '1', numOfRows: '400' });
                var m = {};
                items(doc).forEach(function (it) {
                    var hp = (txt(it, 'hpid') || '').trim();
                    if (!hp) return;
                    var yn = function (t) {
                        return ((txt(it, t) || 'N') + '').trim().toUpperCase().charAt(0) === 'Y';
                    };
                    m[hp] = { er: bedObj(sumBeds(it, ER_TAGS)), ward: bedObj(sumBeds(it, WARD_TAGS)),
                              icu: bedObj(sumBeds(it, ICU_TAGS)),
                              // 자원검색용 장비 보유 플래그 (서버판 /api/beds 와 동일 규격)
                              eq: { crrt: yn('hvcrrtayn'), ecmo: yn('hvecmoayn'),
                                    ttm: yn('hvhypoayn'),  hbo: yn('hvoxyayn') },
                              tel3: (txt(it, 'dutyTel3') || '').trim(),
                              upd: (txt(it, 'hvidate') || '').trim() };
                });
                if (Object.keys(m).length) {
                    if (!uni) { _bedCache[sd] = { t: Date.now(), m: m }; return m; }
                    Object.keys(m).forEach(function (k) {
                        if (!accM[k]) { accM[k] = m[k]; accN++; }
                    });
                }
            } catch (e) { /* 다음 별칭 */ }
        }
        if (uni && accN) { _bedCache[sd] = { t: Date.now(), m: accM }; return accM; }
        /* [ROOT-FIX 2026-D1] STAGE1 이 0건만 주는 시/도 → STAGE2 단위 합집합
           (앱 _fetch_sido_beds 와 동일) */
        var gus = GU_MAP[sd] || [];
        for (var n = 0; n < names.length && gus.length; n++) {
            var mm = {};
            for (var g = 0; g < gus.length; g++) {
                try {
                    var d3 = await apiGet('getEmrrmRltmUsefulSckbdInfoInqire',
                        { STAGE1: names[n], STAGE2: gus[g], pageNo: '1', numOfRows: '200' });
                    items(d3).forEach(function (it3) {
                        var hp3 = (txt(it3, 'hpid') || '').trim();
                        if (!hp3 || mm[hp3]) return;
                        var yn3 = function (t) {
                            return ((txt(it3, t) || 'N') + '').trim().toUpperCase().charAt(0) === 'Y';
                        };
                        mm[hp3] = { er: bedObj(sumBeds(it3, ER_TAGS)),
                                    ward: bedObj(sumBeds(it3, WARD_TAGS)),
                                    icu: bedObj(sumBeds(it3, ICU_TAGS)),
                                    eq: { crrt: yn3('hvcrrtayn'), ecmo: yn3('hvecmoayn'),
                                          ttm: yn3('hvhypoayn'), hbo: yn3('hvoxyayn') },
                                    tel3: (txt(it3, 'dutyTel3') || '').trim(),
                                    upd: (txt(it3, 'hvidate') || '').trim() };
                    });
                } catch (e) { /* 개별 구 실패 무시 */ }
            }
            if (Object.keys(mm).length) { _bedCache[sd] = { t: Date.now(), m: mm }; return mm; }
        }
        /* ③ STAGE1/STAGE2 가 고장난 시/도 → 전국 병상 스냅샷에서 hpid 로 추출 */
        try {
            var ids = {}, k;
            (await natListCached(false)).forEach(function (h) {
                if (h.sido === sd) ids[h.hpid] = 1; });
            if (!Object.keys(ids).length && _rosterCache)
                _rosterCache.forEach(function (h) { if (h.sido === sd) ids[h.hpid] = 1; });
            if (Object.keys(ids).length) {
                var nb = await natBedsCached(false), mp = {};
                for (k in nb) if (ids[k]) mp[k] = nb[k];
                if (Object.keys(mp).length) {
                    _bedCache[sd] = { t: Date.now(), m: mp };
                    return mp;
                }
            }
        } catch (e) { /* 폴백 실패 */ }
        _bedCache[sd] = { t: Date.now(), m: {} };
        return {};
    }
    async function fetchBeds(sido) {
        try { return { success: true, cached: false, beds: await bedsBySido(sido, false) }; }
        catch (e) { return { success: false, error: (e && e.message) || String(e) }; }
    }
    async function fetchBedSaturation(force) {
        if (!force && _satCache && (Date.now() - _satCache.t < 60000))
            return { success: true, cached: true, count: _satCache.rows.length,
                     failed: _satCache.fails, rows: _satCache.rows, queried_at: _satCache.at };
        var all = await fetchAllHospitals(false);
        if (!all.success) return all;
        /* [ROOT-FIX 2026-G1] 로스터에 0건인 시/도(광주광역시 등)를 라이브 복구.
           포화도 화면은 로스터만 보므로 이 단계가 없으면 지역이 통째로 사라진다. */
        try {
            var cnt = {};
            all.hospitals.forEach(function (h) { cnt[h.sido] = (cnt[h.sido] || 0) + 1; });
            var missSd = SIDO_LIST.filter(function (k) { return !cnt[k]; });
            if (missSd.length) {
                setStatus('누락 지역 복구 ' + missSd.join(','));
                var seenH = {};
                all.hospitals.forEach(function (h) { seenH[h.hpid] = 1; });
                for (var mi = 0; mi < missSd.length; mi++) {
                    var rec = await listBySido(missSd[mi]).catch(function () { return []; });
                    rec.forEach(function (h) {
                        if (!seenH[h.hpid]) { seenH[h.hpid] = 1; all.hospitals.push(h); }
                    });
                }
                _rosterCache = all.hospitals;
            }
        } catch (e) { /* 복구 실패 시 원본 유지 */ }
        var beds = {}, fails = [], b;
        for (b = 0; b < SIDO_LIST.length; b += 6) {
            setStatus('병상 포화도 ' + Math.min(b + 6, SIDO_LIST.length) + '/' + SIDO_LIST.length);
            var res = await Promise.all(SIDO_LIST.slice(b, b + 6).map(function (sd) {
                return bedsBySido(sd, force).then(function (m) { return { s: sd, m: m }; })
                    .catch(function (e) { return { s: sd, err: e }; });
            }));
            res.forEach(function (x) {
                if (x.err) { fails.push(x.s + ': ' + (x.err && x.err.message ? x.err.message : x.err)); return; }
                Object.keys(x.m).forEach(function (k) { beds[k] = x.m[k]; });
            });
        }
        /* 시/도 조회에서 누락된 hpid 를 전국 병상 스냅샷으로 보충 */
        try {
            var lack = all.hospitals.filter(function (h) { return !beds[h.hpid]; });
            if (lack.length) {
                var nbAll = await natBedsCached(force);
                lack.forEach(function (h) { if (nbAll[h.hpid]) beds[h.hpid] = nbAll[h.hpid]; });
            }
        } catch (e) { /* 보충 실패 무시 */ }
        if (!Object.keys(beds).length)
            return { success: false, error: '실시간 병상 조회 실패: ' + fails.slice(0, 3).join('; ') };
        var Z = { a: 0, t: 0, r: null };
        var out = all.hospitals.map(function (h) {
            var bd = beds[h.hpid];
            var er = bd ? bd.er : Z, wd = bd ? bd.ward : Z, ic = bd ? bd.icu : Z;
            var adm = ADM_RATE.hasOwnProperty(h.level) ? ADM_RATE[h.level] : 0.25;
            return { hpid: h.hpid, name: h.name, sido: h.sido, gugun: h.gugun,
                     level: h.level, dutyAddr: h.dutyAddr, dutyTel1: h.dutyTel1 || '',
                     dutyTel3: (bd && bd.tel3) ? bd.tel3 : (h.dutyTel3 || ''),
                     emclsName: h.emclsName || '',
                     er: er, ward: wd, icu: ic,
                     load: baci(er, wd, ic, adm), adm: adm,
                     upd: bd ? bd.upd : '' };
        });
        out.sort(function (x, y) {
            var nx = (x.er.r === null) ? 1 : 0, ny = (y.er.r === null) ? 1 : 0;
            if (nx !== ny) return nx - ny;
            var rx = x.er.r || 0, ry = y.er.r || 0;
            if (rx !== ry) return ry - rx;
            return x.name < y.name ? -1 : 1;
        });
        var dt = new Date(), pad = function (n) { return (n < 10 ? '0' : '') + n; };
        var at = pad(dt.getMonth() + 1) + '/' + pad(dt.getDate()) + ' '
               + pad(dt.getHours()) + ':' + pad(dt.getMinutes()) + ':' + pad(dt.getSeconds());
        _satCache = { t: Date.now(), rows: out, fails: fails, at: at };
        setStatus('포화도 ' + out.length + '개');
        return { success: true, cached: false, count: out.length, failed: fails, rows: out, queried_at: at };
    }
    async function fetchDetail(hpid, sido, gugun) {
        var detail = null;
        try {
            var doc = await regionDoc('getEmrrmRltmUsefulSckbdInfoInqire', sido, gugun);
            if (doc) items(doc).forEach(function (it) {
                if (!detail && (txt(it, 'hpid') || '').trim() === hpid) detail = parseHospitalData(it);
            });
        } catch (e) { /* 폴백 전부 실패 */ }
        if (!detail) detail = { emergency: {}, icu: {}, general: {}, isolation: {},
                                other: {}, equipment: {}, update_time: '' };
        // fetchHospitalMsgs 는 문자열을 반환한다 → [{label,msg}] 로 정규화
        var msgs = [];
        try {
            var r = await fetchHospitalMsgs(hpid);
            if (typeof r === 'string') {
                String(r).split('\n').forEach(function (ln) {
                    ln = ln.trim();
                    if (!ln || ln === '정상' || ln === '정보 없음') return;
                    msgs.push(_exSplitMsg(ln));
                });
            } else if (Array.isArray(r)) {
                r.forEach(function (x) {
                    msgs.push(typeof x === 'object'
                        ? { label: x.label || x.type || '-', msg: x.msg || x.message || String(x) }
                        : _exSplitMsg(String(x)));
                });
            }
        } catch (e) { msgs = []; }
        return { success: true, detail: detail, messages: msgs };
    }

    return {
        cfg: cfg, parseXml: parseXml, setParseXml: function (f) { parseXml = f; },
        safeInt: safeInt, pyRound: pyRound,
        parseHospitalData: parseHospitalData, hospitalLevel: hospitalLevel,
        fetchHospitalMsgs: fetchHospitalMsgs, fetchBasicInfo: fetchBasicInfo, fetchKiosk: fetchKiosk,
        fetchRegionHospitals: fetchRegionHospitals,
        fetchAllHospitals: fetchAllHospitals, splitGugun: splitGugun,
        fetchBedSaturation: fetchBedSaturation, fetchBeds: fetchBeds,
        fetchDetail: fetchDetail, baci: baci, regionDoc: regionDoc,
        formatBedCell: formatBedCell, formatBirthRoomCell: formatBirthRoomCell,
        renderComparison: renderComparison, loadAll: loadAll, setStatus: setStatus
    };
})();
/*EX-ENGINE-END*/

    // ── 화면 구동부 (브라우저 전용) ─────────────────────────────
    (function () {
        if (typeof document === 'undefined' || !document.getElementById('exBody')) return;
        var refreshTimer = null, countdownTimer = null;
        var currentInterval = EX.cfg.iv || 180000;
        var nextRefreshTime = null, isRefreshing = false;

        var sel = document.getElementById('refreshInterval');
        var hasOpt = false;
        for (var i = 0; i < sel.options.length; i++)
            if (parseInt(sel.options[i].value) === currentInterval) { sel.selectedIndex = i; hasOpt = true; break; }
        if (!hasOpt) sel.value = '180000';
        currentInterval = parseInt(sel.value);

        function fmtRemain(ms) {
            var s = Math.max(0, Math.ceil(ms / 1000));
            return Math.floor(s / 60) + ':' + ('0' + (s % 60)).slice(-2);
        }
        function updateBar() {
            var bar = document.getElementById('globalRefreshBar');
            var txtEl = document.getElementById('globalRefreshText');
            if (currentInterval <= 0 || !nextRefreshTime) {
                bar.style.width = '100%'; txtEl.textContent = '수동'; return;
            }
            var remain = nextRefreshTime - Date.now();
            if (remain <= 0) {
                bar.style.width = '0%'; txtEl.textContent = '0:00';
                // 백그라운드 스로틀로 예약이 밀린 경우 즉시 만회 갱신
                if (remain < -3000 && !isRefreshing) doRefresh();
                return;
            }
            bar.style.width = Math.max(0, Math.min(100, remain / currentInterval * 100)) + '%';
            txtEl.textContent = fmtRemain(remain);
        }
        function schedule() {
            if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null; }
            if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
            if (currentInterval >0) {
                nextRefreshTime = Date.now() + currentInterval;
                refreshTimer = setTimeout(doRefresh, currentInterval);
                countdownTimer = setInterval(updateBar, 1000);
            } else { nextRefreshTime = null; }
            updateBar();
        }
        async function doRefresh() {
            if (isRefreshing) return;
            isRefreshing = true;
            EX.setStatus('갱신 중...');
            try {
                var r = await EX.loadAll();
                if (r.hd.length === 0) {
                    var msg = '병상 데이터를 가져오지 못했습니다.';
                    if (r.errors.length) msg += '<br><br>' + r.errors.join('<br>');
                    msg += '<br><br>· 인터넷 연결을 확인하세요.<br>· 직접 호출이 차단된 환경이면 상단의 "프록시 허용"을 켜고 다시 시도하세요.';
                    document.getElementById('exBody').innerHTML = '<div class="ex-err"> ' + msg + '</div>';
                } else {
                    document.getElementById('exBody').innerHTML = EX.renderComparison(r.hd, false);
                    try { EXSEC.apply(); } catch (e) {}
                    var now = new Date();
                    document.getElementById('queryTime').textContent =
                        ('0' + now.getHours()).slice(-2) + ':' + ('0' + now.getMinutes()).slice(-2) + ':' + ('0' + now.getSeconds()).slice(-2);
                    try { fitBedTexts(); setTimeout(fitBedTexts, 300); } catch (e) {}
                    // 탭 제목/작업전환 화면에 복수병원 응급실 x/y + 갱신시각 표시
                    try {
                        var tline = r.hd.map(function (h) {
                            var b = (h.emergency || {}).hvec || {};
                            var a = (b.avail === undefined || b.avail < 0) ? '?' : b.avail;
                            var t = (b.total >0) ? b.total : '?';
                            var nm = h.name.length >5 ? h.name.slice(0, 5) : h.name;
                            return nm + ' ' + a + '/' + t;
                        }).join(' | ');
                        var tfull = tline + ' (' + document.getElementById('queryTime').textContent.slice(0, 5) + ')';
                        if (window.parent && window.parent !== window)
                            window.parent.postMessage({ exTitle: tfull }, '*');
                        else document.title = tfull;
                    } catch (e) {}
                    try { MINI.update(r.hd); } catch (e) {}
                }
            } catch (e) {
                document.getElementById('exBody').innerHTML =
                    '<div class="ex-err">갱신 실패: ' + (e && e.message ? e.message : e) + '</div>';
            }
            isRefreshing = false;
            schedule();
        }
        sel.addEventListener('change', function () {
            const _sel = sel;
            if (_sel.value === 'custom') {
                const _s = parseInt(prompt('갱신 주기(초, 최소 20초)', '90'));
                const _ms = (_s && _s >= 20) ? _s * 1000 : 180000;
                const _o = _sel.options[_sel.selectedIndex];
                _o.value = String(_ms);
                _o.textContent = '직접(' + Math.round(_ms / 1000) + '초)';
                const _c = document.createElement('option');
                _c.value = 'custom'; _c.textContent = '직접입력';
                _sel.appendChild(_c);
            }
            currentInterval = parseInt(_sel.value);
            schedule();
        });
        document.getElementById('refreshNow').addEventListener('click', doRefresh);
        document.addEventListener('visibilitychange', function () {
            if (!document.hidden && nextRefreshTime && Date.now() >= nextRefreshTime) doRefresh();
        });

        // ── 병상 텍스트 폰트 자동 조절 (원본 fitBedTexts 이식) ──────
        function fitBedTexts() {
            document.querySelectorAll('.bed-text-overlay').forEach(function (overlay) {
                if (overlay.id === 'globalRefreshOverlay') return;
                var container = overlay.closest('.bar-container');
                if (!container) return;
                overlay.style.fontSize = '';
                var containerW = container.clientWidth;
                if (!containerW) return;
                var span = document.createElement('span');
                var curFontSize = parseFloat(window.getComputedStyle(overlay).fontSize);
                span.style.cssText = ['position:fixed', 'visibility:hidden', 'white-space:nowrap',
                    'font-size:' + curFontSize + 'px', 'font-weight:700', 'pointer-events:none', 'top:-9999px'].join(';');
                span.textContent = overlay.textContent;
                document.body.appendChild(span);
                var textW = span.getBoundingClientRect().width;
                document.body.removeChild(span);
                if (textW >containerW - 2) {
                    var ratio = (containerW - 2) / textW;
                    overlay.style.fontSize = Math.max(curFontSize * ratio * 0.97, 5) + 'px';
                }
            });
            var numCols = document.querySelectorAll('.comparison-table thead th').length - 1;
            if (numCols >= 2) {
                var tbl = document.querySelector('.comparison-table');
                var tableW = tbl ? tbl.clientWidth : 0;
                var fth = document.querySelector('.comparison-table thead th');
                var firstColW = fth ? fth.clientWidth : 0;
                var cellW = tableW >0 ? (tableW - firstColW) / numCols : 0;
                if (cellW >0) {
                    document.querySelectorAll('.comparison-table td:not(.item-label):not(.category-header)').forEach(function (td) {
                        var inner = td.querySelector('.bed-numbers, .equipment-cell, .bed-cell');
                        var el = inner || td;
                        el.style.fontSize = '';
                        var curSz = parseFloat(window.getComputedStyle(el).fontSize);
                        if (el.scrollWidth >cellW + 4) {
                            var ratio = cellW / el.scrollWidth;
                            el.style.fontSize = Math.max(curSz * ratio * 0.95, 6) + 'px';
                        }
                    });
                }
            }
        }
        window.addEventListener('resize', function () { try { fitBedTexts(); } catch (e) {} });

        // ──  표시 항목 · 순서 설정 (py/저장본 공용, localStorage 영구 기억) ──
        var EXSEC = (function () {
            var CATS = ['응급실', '중환자실', '격리진료구역', '입원실', '기타', '의료장비',
                        '중증질환 수용가능', '예외상황'];
            var MINSET = { '응급실': 1, '중환자실': 1, '입원실': 1, '예외상황': 1 };
            function load() {
                var c = null;
                try { c = JSON.parse(localStorage.getItem('exSections') || 'null'); } catch (e) {}
                if (!c || !c.order || !c.order.length) c = { order: CATS.slice(), hidden: {} };
                if (!c.hidden) c.hidden = {};
                CATS.forEach(function (nm) { if (c.order.indexOf(nm) === -1) c.order.push(nm); });
                c.order = c.order.filter(function (nm) { return CATS.indexOf(nm) !== -1; });
                return c;
            }
            function save(c) { try { localStorage.setItem('exSections', JSON.stringify(c)); } catch (e) {} }
            function groups(tb) {
                var out = [], cur = null;
                Array.prototype.forEach.call(tb.rows, function (tr) {
                    var td = tr.querySelector('td.category-header');
                    if (td) {
                        var nm = td.textContent.trim();
                        var name = CATS.filter(function (c2) { return nm.indexOf(c2) === 0; })[0] || nm;
                        cur = { name: name, rows: [tr] };
                        out.push(cur);
                    } else if (cur) {
                        cur.rows.push(tr);
                    }
                });
                return out;
            }
            function apply() {
                var tb = document.querySelector('.comparison-table tbody');
                if (!tb) return;
                var c = load(), by = {};
                groups(tb).forEach(function (g) { by[g.name] = g; });
                c.order.forEach(function (nm) {
                    var g = by[nm];
                    if (!g) return;
                    g.rows.forEach(function (r) {
                        tb.appendChild(r);
                        r.style.display = c.hidden[nm] ? 'none' : '';
                    });
                    delete by[nm];
                });
                Object.keys(by).forEach(function (nm) {
                    by[nm].rows.forEach(function (r) { tb.appendChild(r); });
                });
            }
            function panel() {
                var old = document.getElementById('secPanel');
                if (old) { old.remove(); return; }
                var c = load();
                var wrap = document.createElement('div');
                wrap.id = 'secPanel';
                wrap.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.45);'
                    + 'display:flex;align-items:center;justify-content:center;';
                function build() {
                    var rows = c.order.map(function (nm, i) {
                        return '<div style="display:flex;align-items:center;gap:8px;padding:6px 4px;'
                             + 'border-bottom:1px solid #eee;font-size:0.9rem;">'
                             + '<input type="checkbox" data-sc="' + i + '"'
                             + (c.hidden[nm] ? '' : ' checked') + '>'
                             + '<span style="flex:1;">' + nm + '</span>'
                             + '<button data-up="' + i + '" style="border:none;background:#eee;'
                             + 'border-radius:6px;padding:3px 9px;">▲</button>'
                             + '<button data-dn="' + i + '" style="border:none;background:#eee;'
                             + 'border-radius:6px;padding:3px 9px;">▼</button></div>';
                    }).join('');
                    wrap.innerHTML = '<div style="background:#fff;border-radius:14px;max-width:340px;'
                        + 'width:88vw;padding:16px;box-shadow:0 8px 30px rgba(0,0,0,0.3);">'
                        + '<div style="font-weight:700;margin-bottom:6px;">표시 항목 · 순서</div>'
                        + '<div style="max-height:52vh;overflow:auto;">' + rows + '</div>'
                        + '<div style="display:flex;gap:8px;margin-top:10px;">'
                        + '<button id="secMin" style="flex:1;padding:8px;border:none;border-radius:10px;'
                        + 'background:#667eea;color:#fff;font-weight:700;">최소</button>'
                        + '<button id="secAll" style="flex:1;padding:8px;border:none;border-radius:10px;'
                        + 'background:#e0e0e0;font-weight:700;">전체</button>'
                        + '<button id="secClose" style="padding:8px 12px;border:none;border-radius:10px;'
                        + 'background:#f5f5f5;">닫기</button></div></div>';
                }
                build();
                document.body.appendChild(wrap);
                wrap.addEventListener('click', function (e) {
                    var t = e.target;
                    if (t === wrap || t.id === 'secClose') { wrap.remove(); return; }
                    if (t.id === 'secMin') {
                        c.hidden = {};
                        c.order.forEach(function (nm) { if (!MINSET[nm]) c.hidden[nm] = true; });
                        save(c); apply(); build(); return;
                    }
                    if (t.id === 'secAll') { c.hidden = {}; save(c); apply(); build(); return; }
                    var up = t.getAttribute ? t.getAttribute('data-up') : null;
                    var dn = t.getAttribute ? t.getAttribute('data-dn') : null;
                    if (up !== null) {
                        var i = parseInt(up);
                        if (i >0) { var x = c.order[i]; c.order[i] = c.order[i - 1]; c.order[i - 1] = x; }
                        save(c); apply(); build(); return;
                    }
                    if (dn !== null) {
                        var j = parseInt(dn);
                        if (j < c.order.length - 1) {
                            var y = c.order[j]; c.order[j] = c.order[j + 1]; c.order[j + 1] = y;
                        }
                        save(c); apply(); build(); return;
                    }
                });
                wrap.addEventListener('change', function (e) {
                    var sc = e.target && e.target.getAttribute ? e.target.getAttribute('data-sc') : null;
                    if (sc === null) return;
                    var nm = c.order[parseInt(sc)];
                    if (e.target.checked) delete c.hidden[nm]; else c.hidden[nm] = true;
                    save(c); apply();
                });
            }
            try { var b = document.getElementById('secBtn'); if (b) b.onclick = panel; } catch (e) {}
            return { apply: apply, panel: panel };
        })();
        try { EXSEC.apply(); } catch (e) {}

        // ──  미니창(항상 위): PC=Document PiP, Android=canvas→video PiP ──
        var MINI = (function () {
            var docWin = null, video = null, canvas = null, track = null, lastHd = null;
            // 스타일 (사양: 각진 모서리 / 얇은 검정 실선 / 보라 70% / 굵은 녹색 시스템폰트)
            // 값만 바꾸면 즉시 반영된다.
            var MINI_STYLE = { radius: 0, border: '1px solid #555',
                               bg: 'rgba(0,0,0,0.85)', bgSolid: '#000000',
                               color: '#ffffff', weight: '700', fontSize: 44, opacity: 85 };
            var MINI_DEFAULT = JSON.parse(JSON.stringify(MINI_STYLE));
            try {
                var _saved = JSON.parse(localStorage.getItem('exMiniStyle') || 'null');
                if (_saved) Object.keys(MINI_STYLE).forEach(function (k) {
                    if (_saved[k] !== undefined) MINI_STYLE[k] = _saved[k];
                });
            } catch (e) {}
            var canvasTimer = null;
            var lockW = 0, lockH = 0;   // PiP 중 캔버스 해상도 고정
            // ── 스타일 샘플(프리셋)·투명도·탭 변경 유틸 ──
            var PRESETS = [
                { name: '검정', s: { radius: 0,  border: '1px solid #555', bg: 'rgba(0,0,0,0.85)', bgSolid: '#000000', color: '#ffffff', weight: '700', fontSize: 44, opacity: 85 } },
                { name: '화이트', s: { radius: 10, border: '1px solid #bbb', bg: 'rgba(255,255,255,0.92)', bgSolid: '#f2f2f2', color: '#111111', weight: '700', fontSize: 44, opacity: 92 } },
                { name: '유리', s: { radius: 14, border: '1px solid #9ec1d9', bg: 'rgba(210,230,245,0.55)', bgSolid: '#d7e6f2', color: '#0b2b45', weight: '700', fontSize: 44, opacity: 55 } },
                { name: '고대비', s: { radius: 0, border: '2px solid #ffffff', bg: 'rgba(0,0,0,0.95)', bgSolid: '#000000', color: '#ffee00', weight: '800', fontSize: 48, opacity: 95 } },
                { name: '녹색', s: { radius: 6,  border: '1px solid #00aa55', bg: 'rgba(0,40,25,0.85)', bgSolid: '#002819', color: '#4dff9d', weight: '700', fontSize: 44, opacity: 85 } }
            ];
            var presetIdx = 0;
            function styleGet(k) { return MINI_STYLE[k]; }
            function _miniKick() { try { doRefresh(); } catch (e) {} }
            var IV_CYCLE = [60000, 180000, 300000, 600000, 0];
            function _ivLabel() {
                var v = currentInterval;
                if (!(v >0)) return '수동';
                return v >= 60000 ? Math.round(v / 60000) + '분' : Math.round(v / 1000) + '초';
            }
            function setMainInterval(ms) {   // 미니창 ↔ 메인 주기 완전 동기
                try {
                    sel.value = String(ms);
                    currentInterval = ms;
                    schedule();
                } catch (e) {}
            }
            function cycleMainInterval() {
                var i = IV_CYCLE.indexOf(currentInterval);
                setMainInterval(IV_CYCLE[(i + 1 + IV_CYCLE.length) % IV_CYCLE.length]);
            }
            function withAlpha(cs, pct) {
                var a = Math.max(0, Math.min(100, parseInt(pct))) / 100;
                if (isNaN(a)) return cs;
                var s2 = String(cs).trim();
                if (s2.slice(0, 4) === 'rgba' || s2.slice(0, 4) === 'rgb(') {
                    var inner = s2.slice(s2.indexOf('(') + 1, s2.lastIndexOf(')'));
                    var p = inner.split(',');
                    if (p.length >= 3)
                        return 'rgba(' + p[0].trim() + ',' + p[1].trim() + ',' + p[2].trim() + ',' + a + ')';
                }
                if (s2.charAt(0) === '#' && s2.length === 7) {
                    var v = parseInt(s2.slice(1), 16);
                    if (!isNaN(v))
                        return 'rgba(' + ((v >>16) & 255) + ',' + ((v >>8) & 255) + ',' + (v & 255) + ',' + a + ')';
                }
                return cs;
            }
            function applyStyleObj(s, save) {
                Object.keys(s).forEach(function (k) { MINI_STYLE[k] = s[k]; });
                if (save) { try { localStorage.setItem('exMiniStyle', JSON.stringify(MINI_STYLE)); } catch (e) {} }
                if (docWin) { applyDocCss(); renderDoc(); }
                if (canvas) drawCanvas();
            }
            function announcePreset(nm) {
                try {
                    if (navigator.mediaSession && window.MediaMetadata)
                        navigator.mediaSession.metadata = new MediaMetadata({ title: '미니창 스타일: ' + nm });
                } catch (e) {}
                try { EX.setStatus('미니창 스타일: ' + nm); } catch (e) {}
            }
            function cyclePreset(dir) {
                presetIdx = (presetIdx + (dir || 1) + PRESETS.length) % PRESETS.length;
                applyStyleObj(PRESETS[presetIdx].s, true);
                announcePreset(PRESETS[presetIdx].name);
            }
            // PiP창과 동일한 3지표: 응급(hvec) · 입원(hvgc+hv36 합산) · 중환(hvicc)
            function metricsOf(h) {
                function pv(p) { return { a: (p && p.avail !== undefined) ? p.avail : -1,
                                          t: (p && p.total >0) ? p.total : 0 }; }
                var e  = pv((h.emergency || {}).hvec);
                var g1 = pv((h.general || {}).hvgc), g2 = pv((h.general || {}).hv36);
                var ga = (g1.a < 0 && g2.a < 0) ? -1 : Math.max(g1.a, 0) + Math.max(g2.a, 0);
                var gt = g1.t + g2.t;
                var i  = pv((h.icu || {}).hvicc);
                return [ { lbl: '응급', a: e.a, t: e.t },
                         { lbl: '입원', a: ga,  t: gt  },
                         { lbl: '중환', a: i.a, t: i.t } ];
            }
            function ratioColor(a, t) {
                if (a < 0 || t <= 0) return '#9e9e9e';
                var p = a / t;
                return p >= 0.5 ? '#2eff7b' : (p >= 0.2 ? '#ffd54f' : '#ff5252');
            }
            // PiP 모니터와 동일 팔레트 — bright=가용, dark=사용(옅은 동일계열)
            function ratioPair(a, t) {
                if (a < 0 && t <= 0) return { bright: '#333333', dark: '#222222' };
                if (t <= 0)          return { bright: '#E05550', dark: '#511210' };
                var p = Math.max(0, a) / t;
                if (p >= 0.5) return { bright: '#6BC96E', dark: '#1E421F' };
                if (p >= 0.2) return { bright: '#EDBB4A', dark: '#58400A' };
                return { bright: '#E05550', dark: '#511210' };
            }
            function valTxt(a, t) { return (a < 0 ? '-' : a) + '/' + (t >0 ? t : '-'); }
            function nowTs() {
                try { return document.getElementById('queryTime').textContent.slice(0, 5); }
                catch (e) { return ''; }
            }
            function applyDocCss() {
                if (!docWin) return;
                var _fs = parseInt(MINI_STYLE.fontSize) || 15;
                docWin.document.body.style.cssText =
                    'margin:0;padding:8px 10px;font-family:sans-serif;'
                    + 'font-size:' + _fs + 'px;'
                    + 'background:' + withAlpha(MINI_STYLE.bg, MINI_STYLE.opacity === undefined ? 100 : MINI_STYLE.opacity)
                    + ';border:' + MINI_STYLE.border + ';'
                    + 'border-radius:' + MINI_STYLE.radius + 'px;box-sizing:border-box;'
                    + 'color:' + MINI_STYLE.color + ';font-weight:' + MINI_STYLE.weight + ';';
            }
            function miniTick(d) {
                var bar = d.getElementById('mnBar'), cnt = d.getElementById('mnCnt');
                if (!bar || !cnt) return;
                if (!(currentInterval >0) || !nextRefreshTime) {
                    bar.style.width = '100%'; cnt.textContent = '수동'; return;
                }
                var remain = Math.max(0, nextRefreshTime - Date.now());
                bar.style.width = Math.max(0, Math.min(100, remain / currentInterval * 100)) + '%';
                var s = Math.ceil(remain / 1000);
                cnt.textContent = Math.floor(s / 60) + ':' + ('0' + (s % 60)).slice(-2);
                var ib = d.getElementById('mnIv') || d.getElementById('lmIv');
                if (ib) ib.textContent = '' + _ivLabel();
            }
            function renderDoc() {
                if (!docWin || !lastHd) return;
                var fs = parseInt(MINI_STYLE.fontSize) || 44;
                var fsName = Math.max(12, Math.round(fs * 0.46));
                var fsSm = Math.max(10, Math.round(fs * 0.40));
                docWin.document.body.innerHTML = lastHd.map(function (H) {
                    var ms = metricsOf(H);
                    var cols = ms.map(function (m) {
                        var pr = ratioPair(m.a, m.t);
                        var p = (m.a >= 0 && m.t >0) ? Math.min(1, m.a / m.t) : 0;
                        return '<div style="flex:1;min-width:0;">'
                             + '<div style="height:9px;background:' + pr.dark + ';">'
                             + '<div style="height:100%;width:' + (p * 100) + '%;'
                             + 'background:' + pr.bright + ';"></div></div>'
                             + '<div style="display:flex;justify-content:space-between;gap:4px;'
                             + 'font-size:' + fsSm + 'px;margin-top:2px;white-space:nowrap;">'
                             + '<span style="color:#aaaaaa;">(' + m.lbl + ')</span>'
                             + '<b style="color:' + pr.bright + ';">' + valTxt(m.a, m.t) + '</b></div></div>';
                    }).join('');
                    return '<div style="padding:2px 0 6px;">'
                         + '<div style="font-weight:800;font-size:' + fsName + 'px;white-space:nowrap;'
                         + 'overflow:hidden;text-overflow:ellipsis;">' + H.name + '</div>'
                         + '<div style="display:flex;gap:8px;margin-top:3px;">' + cols + '</div></div>';
                }).join('')
                 + '<div style="display:flex;align-items:center;gap:6px;margin-top:6px;">'
                 + '<div style="flex:1;height:7px;background:rgba(255,255,255,0.18);border:1px solid #000;">'
                 + '<div id="mnBar" style="height:100%;width:100%;background:'
                 + MINI_STYLE.color + ';"></div></div>'
                 + '<span id="mnCnt" style="font-weight:800;min-width:42px;'
                 + 'text-align:right;">--:--</span>'
                 + '<button id="mnIv" title="갱신 주기 변경(메인과 동기)" style="border:1px solid #000;'
                 + 'background:#fff;color:#000;font-weight:800;padding:1px 6px;cursor:pointer;">'
                 + _ivLabel() + '</button>'
                 + '<button id="mnRef" title="즉시 갱신" style="border:1px solid #000;background:#fff;'
                 + 'color:#000;font-weight:800;padding:1px 8px;cursor:pointer;">⟳</button>'
                 + '<button id="mnCls" title="닫기" style="border:1px solid #000;background:#fff;'
                 + 'color:#000;font-weight:800;padding:1px 8px;cursor:pointer;">X</button></div>'
                 + '<div style="text-align:right;font-weight:800;'
                 + 'margin-top:3px;">' + nowTs() + ' 갱신</div>';
                miniTick(docWin.document);
            }
            function drawCanvas() {
                if (!canvas || !lastHd || !lastHd.length) return;
                var CW = 1280, CH = 720;   // 16:9 고정 = PiP 창 비율 (무왜곡)
                if (canvas.width !== CW || canvas.height !== CH) { canvas.width = CW; canvas.height = CH; }
                var g = canvas.getContext('2d');
                g.setTransform(1, 0, 0, 1, 0, 0);
                g.fillStyle = '#000';
                g.fillRect(0, 0, CW, CH);
                g.fillStyle = withAlpha(MINI_STYLE.bgSolid, MINI_STYLE.opacity === undefined ? 100 : MINI_STYLE.opacity);
                g.fillRect(0, 0, CW, CH);
                var fscale = Math.max(0.6, Math.min(1.6, (parseInt(MINI_STYLE.fontSize) || 44) / 44));
                var n = lastHd.length, hFoot = 62, top = 10;
                var rowH = (CH - top - hFoot) / n;
                var padX = 26, colW = (CW - padX * 2) / 3, gap = 14;
                lastHd.forEach(function (H, i) {
                    var ms = metricsOf(H);
                    var y0 = top + rowH * i;
                    var fsName = Math.max(18, Math.min(54, rowH * 0.30 * fscale));
                    var fsVal  = Math.max(15, Math.min(46, rowH * 0.25 * fscale));
                    g.textBaseline = 'top';
                    g.textAlign = 'left';
                    g.fillStyle = MINI_STYLE.color;
                    g.font = '800 ' + Math.round(fsName) + 'px sans-serif';
                    var nm = H.name;
                    while (nm.length >2 && g.measureText(nm).width >CW - padX * 2) nm = nm.slice(0, -1);
                    g.fillText(nm, padX, y0 + rowH * 0.04);   // 병원명 = 병상정보 위
                    var barH = Math.max(10, rowH * 0.15);
                    var barY = y0 + rowH * 0.44;
                    var labY = barY + barH + Math.max(4, rowH * 0.05);
                    ms.forEach(function (m, k) {              // 응급·입원·중환 = 고정 3열
                        var x = padX + colW * k, w = colW - gap;
                        var pr = ratioPair(m.a, m.t);
                        var p = (m.a >= 0 && m.t >0) ? Math.min(1, m.a / m.t) : 0;
                        g.fillStyle = pr.dark;               // 사용 병상 = 옅은 동일계열
                        g.fillRect(x, barY, w, barH);
                        g.fillStyle = pr.bright;             // 가용 병상 = 밝은색
                        g.fillRect(x, barY, w * p, barH);
                        g.font = '700 ' + Math.round(fsVal) + 'px sans-serif';
                        g.textAlign = 'left';
                        g.fillStyle = '#aaaaaa';
                        g.fillText('(' + m.lbl + ')', x, labY);
                        g.textAlign = 'right';
                        g.fillStyle = pr.bright;
                        g.fillText(valTxt(m.a, m.t), x + w, labY);
                    });
                });
                var remainMs = (currentInterval >0 && nextRefreshTime)
                    ? Math.max(0, nextRefreshTime - Date.now()) : 0;
                var pct = (currentInterval >0)
                    ? Math.max(0, Math.min(1, remainMs / currentInterval)) : 1;
                var by = CH - hFoot + 8;
                g.fillStyle = 'rgba(255,255,255,0.22)';
                g.fillRect(26, by, CW - 52, 12);
                g.fillStyle = MINI_STYLE.color;
                g.fillRect(26, by, (CW - 52) * pct, 12);
                var sL = Math.ceil(remainMs / 1000);
                var cdt = (currentInterval >0)
                    ? (Math.floor(sL / 60) + ':' + ('0' + (sL % 60)).slice(-2)) : '수동';
                g.textBaseline = 'top';
                g.fillStyle = MINI_STYLE.color;
                g.font = '800 26px sans-serif';
                g.textAlign = 'left';
                g.fillText(cdt, 26, by + 20);
                g.font = '800 28px sans-serif';
                g.textAlign = 'right';
                g.fillText(nowTs() + ' 갱신', CW - 26, by + 18);
                if (track && track.requestFrame) { try { track.requestFrame(); } catch (e) {} }
            }
            async function open() {
                if (!lastHd) { EX.setStatus('미니창: 첫 갱신 후 사용 가능'); return; }
                if (window.documentPictureInPicture) {
                    try {
                        var _k = (parseInt(MINI_STYLE.fontSize) || 15) / 15;
                        docWin = await window.documentPictureInPicture.requestWindow(
                            { width: Math.round(360 * _k),
                              height: Math.round((64 * lastHd.length + 96) * _k) });
                        docWin.__exBaseW = Math.round(360 * _k);
                        docWin.__exBaseH = docWin.innerHeight || 300;
                        applyDocCss();
                        docWin.addEventListener('pagehide', function () { docWin = null; });
                        renderDoc();
                        // 본창이 백그라운드 스로틀로 밀려도 미니창 타이머가 만회 갱신
                        try {
                            docWin.document.addEventListener('click', function (ev) {
                                var id = ev.target && ev.target.id;
                                if (id === 'mnRef') { try { doRefresh(); } catch (e) {} }
                                else if (id === 'mnCls') { try { close(); } catch (e) {} }
                                else if (id === 'mnIv') { try { cycleMainInterval(); renderDoc(); } catch (e) {} }
                                else { try { cyclePreset(1); } catch (e) {} }
                            });
                            docWin.setInterval(function () {
                                try {
                                    miniTick(docWin.document);
                                    if (nextRefreshTime && Date.now() >= nextRefreshTime
                                        && !isRefreshing) doRefresh();
                                } catch (e) {}
                            }, 1000);
                        } catch (e) {}

                        var _fitZoom = function () {
                            try {
                                var z = Math.max(0.4, Math.min(
                                    docWin.innerWidth / (docWin.__exBaseW || 360),
                                    docWin.innerHeight / (docWin.__exBaseH || docWin.innerHeight || 300)));
                                docWin.document.body.style.zoom = z;
                            } catch (e) {}
                        };
                        _fitZoom();
                        docWin.addEventListener('resize', _fitZoom);
                        EX.setStatus('미니창 표시 중');
                        return;
                    } catch (e) { docWin = null; }
                }
                try {
                    canvas = document.createElement('canvas');
                    drawCanvas();
                    var stream = canvas.captureStream(0);
                    track = stream.getVideoTracks()[0];
                    video = document.createElement('video');
                    video.muted = true; video.playsInline = true; video.srcObject = stream;
                    video.style.cssText = 'position:fixed;left:-9999px;top:0;width:2px;height:2px;';
                    document.body.appendChild(video);
                    await video.play();
                    drawCanvas();
                    if (track && track.requestFrame) { try { track.requestFrame(); } catch (e) {} }
                    await new Promise(function (res) {   // 첫 프레임 크기 확정 후 PiP 진입 (비율 왜곡 방지)
                        var t0 = Date.now();
                        (function chk() {
                            if (video.videoWidth >0 || Date.now() - t0 >800) res();
                            else setTimeout(chk, 40);
                        })();
                    });
                    await video.requestPictureInPicture();
                    video.addEventListener('leavepictureinpicture', close);
                    try {
                        if (navigator.mediaSession) {
                            navigator.mediaSession.playbackState = 'playing';
                            navigator.mediaSession.setActionHandler('play', function () {
                                try { video.play(); } catch (e) {}
                            });
                            navigator.mediaSession.setActionHandler('pause', function () {
                                try { video.play(); } catch (e) {}
                                _miniKick();                        //  = 즉시 갱신
                            });
                            navigator.mediaSession.setActionHandler('nexttrack', function () { cyclePreset(1); });          //  = 디자인
                            navigator.mediaSession.setActionHandler('previoustrack', function () { cycleMainInterval(); }); //  = 주기
                            if (window.MediaMetadata)
                                navigator.mediaSession.metadata = new MediaMetadata(
                                    { title: '병상 미니창', artist: '갱신 · 주기 · 디자인 · 크기=핀치' });
                        }
                    } catch (e) {}
                    video.addEventListener('pause', function () { try { video.play(); } catch (e) {} });
                    canvasTimer = setInterval(drawCanvas, 1000);
                    EX.setStatus('미니창 표시 중');
                } catch (e) {
                    EX.setStatus('미니창 미지원: ' + (e && e.message ? e.message : e));
                    close();
                }
            }
            function close() {
                try { if (canvasTimer) clearInterval(canvasTimer); } catch (e) {}
                canvasTimer = null;
                lockW = 0; lockH = 0;
                try { if (docWin) docWin.close(); } catch (e) {}
                docWin = null;
                try { if (document.pictureInPictureElement) document.exitPictureInPicture(); } catch (e) {}
                try { if (track) track.stop(); } catch (e) {}
                try { if (video) video.remove(); } catch (e) {}
                video = null; canvas = null; track = null;
            }
            function toggle() { if (docWin || video) close(); else open(); }
            function update(hd) { lastHd = hd; if (docWin) renderDoc(); if (canvas) drawCanvas(); }
            function stylePanel() {
                var old = document.getElementById('miniStylePanel');
                if (old) { old.remove(); return; }
                var wrap = document.createElement('div');
                wrap.id = 'miniStylePanel';
                wrap.style.cssText = 'position:fixed;inset:0;z-index:9999;'
                    + 'background:rgba(0,0,0,0.45);display:flex;align-items:center;justify-content:center;';
                function row(lbl, key) {
                    var v = styleGet(key);
                    if (key === 'radius' || key === 'fontSize' || key === 'opacity') {
                        var mn = key === 'fontSize' ? 12 : 0;
                        var mx = key === 'radius' ? 24 : (key === 'fontSize' ? 72 : 100);
                        return '<label style="display:flex;gap:8px;align-items:center;'
                            + 'padding:6px 0;font-size:0.85rem;">'
                            + '<span style="width:74px;">' + lbl + '</span>'
                            + '<input type="range" data-k="' + key + '" min="' + mn + '" max="' + mx
                            + '" value="' + (parseInt(v) || mn) + '" style="flex:1;">'
                            + '<b data-v="' + key + '" style="width:34px;text-align:right;">'
                            + (parseInt(v) || mn) + '</b></label>';
                    }
                    var OPTS = {
                        color: [['#ffffff', '흰색'], ['#111111', '검정'], ['#00e676', '녹색'],
                                ['#ffee00', '노랑'], ['#4dc3ff', '하늘']],
                        weight: [['400', '보통'], ['700', '굵게'], ['800', '아주 굵게']],
                        border: [['none', '없음'], ['1px solid #555', '얇은 회색'],
                                 ['1px solid #000', '얇은 검정'], ['2px solid #ffffff', '굵은 흰색'],
                                 ['2px solid #000000', '굵은 검정']],
                        bg: [['rgba(0,0,0,1)', '검정'], ['rgba(255,255,255,1)', '흰색'],
                             ['rgba(10,25,60,1)', '남색'], ['rgba(0,40,25,1)', '짙은 녹색'],
                             ['rgba(60,60,60,1)', '회색']],
                        bgSolid: [['#000000', '검정'], ['#f2f2f2', '흰색'], ['#0a193c', '남색'],
                                  ['#002819', '짙은 녹색'], ['#3c3c3c', '회색']]
                    };
                    var cur = String(v), found = false;
                    var os = (OPTS[key] || []).map(function (o) {
                        var s2 = cur === o[0];
                        if (s2) found = true;
                        return '<option value="' + o[0] + '"' + (s2 ? ' selected' : '') + '>'
                             + o[1] + '</option>';
                    }).join('');
                    if (!found) os = '<option value="' + cur + '" selected>사용자값</option>' + os;
                    return '<label style="display:flex;gap:8px;align-items:center;'
                        + 'padding:6px 0;font-size:0.85rem;">'
                        + '<span style="width:74px;">' + lbl + '</span>'
                        + '<select data-k="' + key + '" style="flex:1;padding:5px;border:1px solid #ccc;'
                        + 'border-radius:8px;">' + os + '</select></label>';
                }
                wrap.innerHTML = '<div style="background:#fff;border-radius:14px;max-width:330px;'
                    + 'width:88vw;padding:16px;box-shadow:0 8px 30px rgba(0,0,0,0.3);">'
                    + '<div style="font-weight:700;margin-bottom:6px;">미니창 스타일</div>'
                    + '<div style="display:flex;gap:5px;margin:2px 0 8px;">'
                    + PRESETS.map(function (p, i) {
                        return '<button data-pi="' + i + '" style="flex:1;padding:6px 2px;'
                             + 'border:1px solid #999;border-radius:8px;background:' + p.s.bgSolid
                             + ';color:' + p.s.color + ';font-weight:700;font-size:0.72rem;">'
                             + p.name + '</button>';
                    }).join('') + '</div>'
                    + row('모서리(px)', 'radius') + row('테두리', 'border')
                    + row('배경', 'bg') + row('배경(영상)', 'bgSolid')
                    + row('글자색', 'color') + row('굵기', 'weight')
                    + row('글자크기(px)', 'fontSize') + row('투명도(0-100)', 'opacity')
                    + '<div style="display:flex;gap:8px;margin-top:10px;">'
                    + '<button id="msApply" style="flex:1;padding:8px;border:none;border-radius:10px;'
                    + 'background:#667eea;color:#fff;font-weight:700;">적용</button>'
                    + '<button id="msReset" style="flex:1;padding:8px;border:none;border-radius:10px;'
                    + 'background:#e0e0e0;font-weight:700;">기본값</button>'
                    + '<button id="msClose" style="padding:8px 12px;border:none;border-radius:10px;'
                    + 'background:#f5f5f5;">닫기</button></div></div>';
                document.body.appendChild(wrap);
                wrap.addEventListener('click', function (e) { if (e.target === wrap) wrap.remove(); });
                wrap.querySelectorAll('button[data-pi]').forEach(function (b) {
                    b.onclick = function () {
                        presetIdx = parseInt(b.getAttribute('data-pi'));
                        applyStyleObj(PRESETS[presetIdx].s, true);
                        wrap.querySelectorAll('input[data-k]').forEach(function (inp) {
                            inp.value = String(styleGet(inp.getAttribute('data-k')));
                        });
                    };
                });
                wrap.querySelector('#msClose').onclick = function () { wrap.remove(); };
                wrap.addEventListener('input', function (e) {
                    var k = e.target && e.target.getAttribute && e.target.getAttribute('data-k');
                    if (!k) return;
                    var v = e.target.value;
                    var o = {};
                    o[k] = (k === 'radius' || k === 'fontSize' || k === 'opacity') ? parseInt(v) : v;
                    applyStyleObj(o, true);   // 슬라이더/선택 즉시 반영·저장 (실시간 미리보기)
                    var bb = wrap.querySelector('b[data-v="' + k + '"]');
                    if (bb) bb.textContent = v;
                });
                function refreshMini() {
                    if (docWin) { applyDocCss(); renderDoc(); }
                    if (canvas) drawCanvas();
                }
                wrap.querySelector('#msApply').onclick = function () {
                    wrap.querySelectorAll('input[data-k]').forEach(function (inp) {
                        var k = inp.getAttribute('data-k'), v = inp.value;
                        MINI_STYLE[k] = (k === 'radius') ? (parseInt(v) || 0)
                            : (k === 'fontSize') ? (parseInt(v) || 15)
                            : (k === 'opacity') ? Math.max(0, Math.min(100, parseInt(v) || 0)) : v;
                    });
                    try { localStorage.setItem('exMiniStyle', JSON.stringify(MINI_STYLE)); } catch (e) {}
                    refreshMini(); wrap.remove();
                };
                wrap.querySelector('#msReset').onclick = function () {
                    Object.keys(MINI_DEFAULT).forEach(function (k) { MINI_STYLE[k] = MINI_DEFAULT[k]; });
                    try { localStorage.removeItem('exMiniStyle'); } catch (e) {}
                    refreshMini(); wrap.remove();
                };
            }
            return { toggle: toggle, update: update, stylePanel: stylePanel };
        })();
        try {
            document.getElementById('miniBtn').addEventListener('click', function () { MINI.toggle(); });
        } catch (e) {}
        try {
            document.getElementById('miniStyleBtn').addEventListener('click', function () { MINI.stylePanel(); });
        } catch (e) {}

        doRefresh();
    })();
    </script>
</body>
</html>'''


def _export_lerp(v1, v4, n):
    if n <= 1: return v1
    if n >= 4: return v4
    return v1 + (v4 - v1) * (n - 1) / 3.0


def _export_sizes(n):
    """비교화면 폰트/여백 프리셋 (compare 라우트와 동일 값)"""
    return {
        'title_font_size': '1.11rem',
        'base_font_size': '0.58rem',
        'table_font_size': f'{_export_lerp(0.92, 0.52, n):.2f}rem',
        'category_font_size': f'{_export_lerp(0.88, 0.52, n):.2f}rem',
        'label_font_size': f'{_export_lerp(0.88, 0.52, n):.2f}rem',
        'bed_number_font_size': f'{_export_lerp(0.92, 0.56, n):.2f}em',
        'pct_font_size_large': f'{_export_lerp(0.88, 0.58, n):.2f}em',
        'exception_font_size': f'{_export_lerp(0.78, 0.48, n):.2f}em',
        'cell_padding': '4px 2px',
        'bed_cell_padding': '3px 2px',
        'bar_height': '5px',
    }


def _export_css_template():
    import re as _re
    m = _re.search(r'<style>(.*?)</style>', COMPARE_WINDOW_HTML, _re.S)
    return m.group(1) if m else ''


def _build_export_html(entries, iv_ms):
    """(단일 비교화면) 저장본 조립 — 엔진 바이트 검증 기준본"""
    css = _export_css_template()
    for k, v in _export_sizes(len(entries)).items():
        css = css.replace('{{ ' + k + ' }}', v)
    cfg = {
        'entries': entries,
        'serviceKey': SERVICE_KEY,
        'iv': int(iv_ms),
        'generated': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }
    html = EXPORT_HTML_SHELL
    html = html.replace('__CSS__', css)
    html = html.replace('__CONFIG__', json.dumps(cfg, ensure_ascii=False))
    html = html.replace('__GENERATED__', cfg['generated'])
    return html


def _admin_json():
    """[일원화 2026-H1] JS 엔진에 주입할 행정구역 표 (파이썬 단일 원본)."""
    return json.dumps({
        'list': list(DISTRICTS.keys()),
        'alias': {k: _SIDO_ALIAS.get(k, []) for k in DISTRICTS},
        'gu': {k: list(v) for k, v in DISTRICTS.items()},
        'legacy': dict(_SIDO_LEGACY),
        'union': sorted(_UNION_SIDO),
    }, ensure_ascii=False)


def _export_engine_js(parent_cfg):
    """마커 사이 엔진 코드 추출 + 부모 페이지용 설정 주입.
    (JSON 이스케이프 사본과 구분하기 위해 마커 뒤 실제 개행을 요구)"""
    import re as _re
    _pat = _re.escape('/*EX-ENGINE-START*/') + '\n(.*?)' + _re.escape('/*EX-ENGINE-END*/')
    m = _re.search(_pat, EXPORT_HTML_SHELL, _re.S)
    if not m:
        raise RuntimeError('export: 엔진 마커 추출 실패')
    return (m.group(1)
            .replace('__ADMIN__', _admin_json())
            .replace('__CONFIG__', json.dumps(parent_cfg, ensure_ascii=False)))


_LIVE_ENGINE_TPL = ['']


def _live_engine_script(entries):
    """라이브 비교화면에 내장할 직접조회 폴백 엔진.
    서버(파이썬)가 강제종료돼도 브라우저가 데이터 갱신을 이어받는다."""
    if not _LIVE_ENGINE_TPL[0]:
        _LIVE_ENGINE_TPL[0] = _export_engine_js(
            {'entries': [], 'serviceKey': SERVICE_KEY, 'iv': 0})
    cfg = json.dumps({'entries': entries}, ensure_ascii=False).replace('<', '\\u003c')
    return ('<script>\n/*EX-ENGINE-START*/\n' + _LIVE_ENGINE_TPL[0]
            + '/*EX-ENGINE-END*/\n</script>\n'
            + '<script>try { EX.cfg.entries = ' + cfg
            + '.entries; } catch (e) {}</script>\n')


def _export_cut(sel, tag, repl):
    """저장본 수술: /*SRV-<TAG>-START*/ ~ /*SRV-<TAG>-END*/ 구간을 repl 로 치환.
    마커가 정확히 1쌍이 아니면 즉시 AssertionError (무증상 404 재발 방지)."""
    a, b = '/*SRV-%s-START*/' % tag, '/*SRV-%s-END*/' % tag
    assert sel.count(a) == 1 and sel.count(b) == 1, 'export: %s 마커 불일치' % tag
    p, q = sel.index(a), sel.index(b) + len(b)
    assert p < q, 'export: %s 마커 순서 오류' % tag
    return sel[:p] + repl + sel[q:]


def _build_full_export(auto_entries, iv_ms):
    """완전판 저장본: 병원 선택 + 비교 조회를 단일 HTML로.
    선택 화면은 실제 서비스 페이지를 렌더링한 뒤 서버 접점 2곳만 교체하고,
    비교 화면은 검증된 저장본 문서를 iframe(srcdoc)으로 띄운다.
    auto_entries 가 있으면 열자마자 해당 구성으로 비교 화면 자동 진입."""
    gen = datetime.now().strftime('%Y-%m-%d %H:%M')

    # ① 비교 문서 템플릿 (폰트/설정은 선택 시점에 JS가 채움)
    compare_tpl = EXPORT_HTML_SHELL
    compare_tpl = compare_tpl.replace('__CSS__', _export_css_template())
    compare_tpl = compare_tpl.replace('__ADMIN__', _admin_json())
    compare_tpl = compare_tpl.replace('__CONFIG__', '"__EXCFG__"')
    compare_tpl = compare_tpl.replace('__GENERATED__', gen)

    # ② 선택 화면 렌더링 + 서버 접점 수술 (마커 기반)
    sel = _render_cached(HTML, districts=DISTRICTS)
    sel = _export_cut(sel, 'REGION',
        "const data = await EXAPP.getHospitals(sido, gugun || '');")
    sel = _export_cut(sel, 'ALL',
        "const d = await EXAPP.getAllHospitals(false);")
    sel = _export_cut(sel, 'ALL2',
        "const dAll = await EXAPP.getAllHospitals(false);")
    sel = _export_cut(sel, 'SAT',
        "const sd = await EXAPP.getBedSaturation(force);")
    sel = _export_cut(sel, 'BEDS',
        "const d = await EXAPP.getBeds(sd);")
    sel = _export_cut(sel, 'DETAIL',
        "const d = await EXAPP.getDetail(hpid, h.sido || '', h.gugun || '');")
    sel = _export_cut(sel, 'COMPARE', "EXAPP.openCompare(hParam);")
    sel = _export_cut(sel, 'DBG',     "")     # 저장본엔 서버 로그 수집기 없음
    sel = _export_cut(sel, 'RECON',   "")     # 저장본엔 재접속 대상 서버 없음
    sel = _export_cut(sel, 'RECON2',  "")

    # 저장본 내부의 저장 버튼은 제거
    _btn_html = ('        <button class="btn" id="saveAppBtn" '
                 'style="margin-top:8px;background:linear-gradient(135deg,#556b8d,#3a4d6b);">'
                 '저장 (단독 HTML — 선택+조회)</button>\n')
    assert sel.count(_btn_html) == 1, 'export: 저장 버튼 마커 불일치'
    sel = sel.replace(_btn_html, '')
    sel = sel.replace(
        "\n        try { document.getElementById('saveAppBtn').onclick = saveStandaloneHtml; } catch(e) {}", '')

    # ③ 부모 글루 (엔진 + 비교문서 빌더 + 오버레이 iframe + 제목 릴레이)
    engine_js = _export_engine_js({'entries': [], 'serviceKey': SERVICE_KEY, 'iv': 0})
    size_sets = {str(n): _export_sizes(n) for n in (1, 2, 3, 4)}
    tpl_json = json.dumps(compare_tpl, ensure_ascii=False).replace('</', '<\\/')
    glue = (
        '\n<script>\n/*EX-ENGINE-START*/\n' + engine_js + '/*EX-ENGINE-END*/\n</script>\n'
        '<script>\n'
        'const EXAPP = (function () {\n'
        "    'use strict';\n"
        ' const COMPARE_TPL = ' + tpl_json + ';\n'
        ' const SIZE_SETS = ' + json.dumps(size_sets, ensure_ascii=False) + ';\n'
        ' const AUTO = ' + json.dumps(auto_entries if auto_entries else None, ensure_ascii=False) + ';\n'
        ' const AUTO_IV = ' + str(int(iv_ms)) + ';\n'
        ' const GEN = ' + json.dumps(gen, ensure_ascii=False) + ';\n'
        ' const BASE_TITLE = document.title;\n'
        ' let overlay = null;\n'
        ' function buildDoc(entries, iv) {\n'
        ' let doc = COMPARE_TPL;\n'
        "        const s = SIZE_SETS[String(Math.min(4, Math.max(1, entries.length)))];\n"
        "        Object.keys(s).forEach(k => { doc = doc.split('{{ ' + k + ' }}').join(s[k]); });\n"
        ' const cfg = { entries: entries, serviceKey: EX.cfg.serviceKey, iv: iv, generated: GEN };\n'
        ' doc = doc.replace(\'"__EXCFG__"\', JSON.stringify(cfg).replace(/</g, \'\\\\u003c\'));\n'
        ' return doc;\n'
        ' }\n'
        ' function openCompare(hParam) {\n'
        ' const entries = [];\n'
        "        String(hParam || '').split(',').forEach(t => {\n"
        "            const p = t.split('|');\n"
        ' if (p.length >= 3 && p[0].trim())\n'
        ' entries.push({ hpid: p[0].trim(), sido: p[1].trim(), gugun: p[2].trim() });\n'
        ' });\n'
        "        if (!entries.length) { alert('병원 구성이 없습니다.'); return; }\n"
        ' closeCompare();\n'
        "        overlay = document.createElement('div');\n"
        "        overlay.style.cssText = 'position:fixed;inset:0;z-index:99998;background:#f5f7fa;';\n"
        # 내부 문서(.back-sel)가 이미 복귀 버튼을 그리므로 바깥 버튼은 만들지 않는다.
        # (중복 표시 방지 — 내부 버튼이 parent.EXAPP.closeCompare 를 호출한다)
        "        const back = document.createElement('button');\n"
        "        back.textContent = '';\n"
        "        back.style.display = 'none';\n"
        # 앱(.back-sel)과 동일한 모양: 좌상단 밀착, 각진 형태, 연회색, 최소 여백
        "        back.style.cssText = 'position:fixed;top:0;left:0;z-index:99999;padding:1px 4px;border:1px solid #dfe3e8;border-radius:0;background:#eef0f3;color:#7b8492;font-size:0.58rem;font-weight:600;line-height:1.15;cursor:pointer;';\n"
        ' back.onclick = closeCompare;\n'
        "        const fr = document.createElement('iframe');\n"
        "        fr.style.cssText = 'width:100%;height:100%;border:none;display:block;';\n"
        "        fr.setAttribute('allow', 'picture-in-picture');\n"
        ' fr.srcdoc = buildDoc(entries, AUTO_IV);\n'
        ' overlay.appendChild(fr);\n'
        ' overlay.appendChild(back);\n'
        ' document.body.appendChild(overlay);\n'
        # [ROOT-FIX 2026-G2] 안드로이드 하드웨어 백버튼: 최상위 문서에서만
        #  history 항목을 쌓고 popstate 로 오버레이를 닫는다. content:// 에서
        #  pushState 가 거부되면(SecurityError) 버튼 경로만 남는다.
        "        try { history.pushState({ exCompare: 1 }, ''); } catch (e) {}\n"
        ' }\n'
        ' function closeCompare() {\n'
        ' if (overlay) { overlay.remove(); overlay = null; }\n'
        ' try { document.title = BASE_TITLE; } catch (e) {}\n'
        ' }\n'
        "    window.addEventListener('message', function (e) {\n"
        ' if (!e || !e.data) return;\n'
        ' if (e.data.exTitle) { try { document.title = e.data.exTitle; } catch (err) {} }\n'
        ' if (e.data.ermonClose) { closeCompare(); }\n'
        ' });\n'
        "    window.addEventListener('popstate', function () {\n"
        ' if (overlay) closeCompare();\n'
        ' });\n'
        ' if (AUTO && AUTO.length) setTimeout(function () {\n'
        "        openCompare(AUTO.map(e =>e.hpid + '|' + e.sido + '|' + e.gugun).join(','));\n"
        ' }, 50);\n'
        ' return { getHospitals: function (s, g) { return EX.fetchRegionHospitals(s, g); },\n'
        ' getAllHospitals: function (f) { return EX.fetchAllHospitals(f); },\n'
        ' getBedSaturation: function (f) { return EX.fetchBedSaturation(f); },\n'
        ' getBeds: function (s) { return EX.fetchBeds(s); },\n'
        ' getDetail: function (h, s, g) { return EX.fetchDetail(h, s, g); },\n'
        ' openCompare: openCompare, closeCompare: closeCompare, buildDoc: buildDoc };\n'
        '})();\n'
        '</script>\n</body>'
    )
    assert sel.count('</body>') == 1, 'export: body 종료 태그 불일치'
    sel = sel.replace('</body>', glue)
    return sel


@flask_app.route('/export')
def export_compare():
    """완전판 저장본(선택+조회) 다운로드 — h 있으면 해당 구성으로 자동 진입"""
    h_param = request.args.get('h', '').strip()
    try:
        iv_ms = int(request.args.get('iv', '180000') or '180000')
    except (ValueError, TypeError):
        iv_ms = 180000
    if iv_ms not in (0, 180000, 300000, 600000, 1800000):
        iv_ms = 180000

    entries = []
    if h_param:
        for token in h_param.split(','):
            parts = token.split('|')
            if len(parts) >= 3 and parts[0].strip():
                entries.append({'hpid': parts[0].strip(),
                                'sido': parts[1].strip(),
                                'gugun': parts[2].strip()})
    else:
        hpids = [p.strip() for p in request.args.get('hpids', '').split(',') if p.strip()]
        sido  = request.args.get('sido', '').strip()
        gugun = request.args.get('gugun', '').strip()
        entries = [{'hpid': hp, 'sido': sido, 'gugun': gugun} for hp in hpids if sido and gugun]

    html = _build_full_export(entries, iv_ms)
    fname = f"er_app_{datetime.now().strftime('%Y%m%d_%H%M')}.html"
    resp = flask_app.response_class(html, mimetype='text/html')
    resp.headers['Content-Disposition'] = f'attachment; filename="{fname}"'
    resp.headers['Cache-Control'] = 'no-store'
    _log(f'[export] 완전판 저장본: 자동진입 {len(entries)}개, iv={iv_ms}')
    return resp


_PIP_HTML = (
    '<!DOCTYPE html>'
    '<html lang="ko">'
    '<head>'
    '<meta charset="UTF-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>응급실 모니터</title>'
    '<style>'
    '*{margin:0;padding:0;box-sizing:border-box;}'
    'html{font-size:16px;}'
    'body{background:#1a1a2e;color:#e0e0e0;'
    ' font-family:"Malgun Gothic","Apple SD Gothic Neo",sans-serif;'
    ' font-size:13px;padding:6px;}'
    '.hdr{background:linear-gradient(135deg,#5b21b6,#4c1d95);padding:7px 10px;'
    ' border-radius:0;margin-bottom:4px;display:flex;'
    ' justify-content:space-between;align-items:center;}'
    '.hdr-title{font-weight:700;font-size:14px;}'
    '.hdr-time{font-size:11px;color:#a78bfa;}'
    '.ovbtns{display:none;gap:3px;margin-left:6px;}'
    '.ovbtns.on{display:flex;}'
    '.ovb{background:rgba(255,255,255,0.16);color:#fff;border:none;'
    ' border-radius:0;min-width:22px;height:22px;font-size:12px;'
    ' line-height:1;cursor:pointer;padding:0 4px;}'
    '.ovb:active{background:rgba(255,255,255,0.35);}'
    '.ovx{background:#dc2626;}'
    '.cbar-wrap{height:5px;background:#1e3a1e;border-radius:0;margin-bottom:5px;overflow:hidden;}'
    '.cbar-fill{height:100%;background:#2d6a2d;border-radius:0;transition:width 1s linear;}'
    'table{width:100%;border-collapse:collapse;table-layout:fixed;}'
    'th{background:#0f3460;color:#a78bfa;padding:5px 1px;font-size:11px;'
    ' font-weight:600;text-align:left;position:sticky;top:0;}'
    'th.nc{text-align:left;padding-left:4px;width:34%;}'
    'th.vc{width:22%;}'
    'td{padding:3px 1px;border-bottom:1px solid #16213e;vertical-align:middle;}'
    'td.n{font-size:11px;padding-left:4px;white-space:nowrap;'
    ' overflow:hidden;text-overflow:ellipsis;}'
    'td.v{text-align:left;padding-left:3px;width:22%;}'
    'td.ok .vn{color:#4CAF50;}td.wn .vn{color:#FFA726;}td.bd .vn{color:#E53935;}'
    'td.no .vn{color:#555;}'
    '.vn{font-weight:700;font-size:12px;display:block;white-space:nowrap;}'
    '.vb{height:5px;border-radius:0;overflow:hidden;margin-top:2px;display:flex;width:100%;}'
    '.bfa{height:100%;}'
    '.bfu{height:100%;opacity:0.25;}'
    '.ctrl{display:flex;gap:5px;margin-bottom:5px;flex-wrap:wrap;align-items:center;}'
    '.ctrl label{font-size:11px;color:#a78bfa;}'
    '.ctrl select{background:#16213e;color:#e0e0e0;border:1px solid #5b21b6;'
    ' border-radius:0;padding:2px 4px;font-size:11px;}'
    '.ctrl button{background:#5b21b6;color:white;border:none;border-radius:0;'
    ' padding:3px 8px;font-size:11px;cursor:pointer;}'
    '.ctrl button:hover{background:#6d28d9;}'
    '.st{font-size:10px;color:#a78bfa;margin-left:auto;}'
    '</style>'
    '</head>'
    '<body>'
    '<div class="hdr">'
    '<span class="hdr-title">응급실 모니터</span>'
    '<span class="hdr-time" id="ut">로드 중...</span>'
    '<span class="ovbtns" id="ovb">'
    '<button class="ovb" id="bSm" title="작게">&#8722;</button>'
    '<button class="ovb" id="bLg" title="크게">&#43;</button>'
    '<button class="ovb" id="bUp" title="위로">&#9650;</button>'
    '<button class="ovb" id="bDn" title="아래로">&#9660;</button>'
    '<button class="ovb ovx" id="bCl" title="닫기">&#10005;</button>'
    '</span>'
    '</div>'
    '<div class="cbar-wrap"><div class="cbar-fill" id="cbf" style="width:100%"></div></div>'
    '<div class="ctrl">'
    '<label>갱신:</label>'
    '<select id="iv">'
    '<option value="0">수동</option>'
    '<option value="60">1분</option>'
    '<option value="180" selected>3분</option>'
    '<option value="300">5분</option>'
    '<option value="600">10분</option>'
    '</select>'
    '<button id="nb">&#128260;</button>'
    '<span class="st" id="ct"></span>'
    '</div>'
    '<div id="tw"></div>'
    '<script>'
    'var HP=decodeURIComponent(new URLSearchParams(location.search).get("h")||"");'
    'var IVI=parseInt(new URLSearchParams(location.search).get("iv")||"180000");'
    '(function(){'
    ' var s=Math.round(IVI/1000),sel=document.getElementById("iv");'
    ' var best=null,bd=Infinity;'
    ' for(var i=0;i<sel.options.length;i++){'
    ' var d=Math.abs(parseInt(sel.options[i].value)-s);'
    ' if(d<bd){bd=d;best=i;}'
    ' }'
    ' if(best!==null)sel.selectedIndex=best;'
    '})();'
    'var _t=null,_ct=null,_na=0,_iv=0;'
    'function nz(v,d){return (v===undefined||v===null||isNaN(v))?((d===undefined)?-1:d):v;}'
    'function gcA(r){'
    ' var g=nz(r.hvgc),e=nz(r.hv36);'
    ' if(g>=0&&e>=0)return g+e; if(g>=0)return g; if(e>=0)return e; return -1;'
    '}'
    'function gcT(r){'
    ' var g=nz(r.hvgc),e=nz(r.hv36),gt=nz(r.hvgc_t,0),et=nz(r.hv36_t,0);'
    ' if(g>=0&&e>=0)return (gt>0?gt:0)+(et>0?et:0);'
    ' if(g>=0)return gt; if(e>=0)return et; return 0;'
    '}'
    'function vc(a,t){if(a<0)return "bd";if(t<=0)return a>0?"ok":a===0?"bd":"no";'
    ' var p=a/t;return p>=0.5?"ok":p>=0.2?"wn":"bd";}'
    'function cell(a,t){'
    ' if(a===-1&&t<=0)return "<td class=\'v no\'><span class=vn>-</span><div class=vb></div></td>";'
    ' var l=t>0?a+"/"+t:String(a);'
    ' var st=vc(a,t);'
    ' var bc=st==="ok"?"#4CAF50":st==="wn"?"#FFA726":"#E53935";'
    ' var wA=t>0?Math.min(100,Math.round(a/t*100)):(a>0?100:0);'
    ' var wU=100-wA;'
    ' return "<td class=\'v "+st+"\'><span class=vn>"+l+"</span>"'
    ' +"<div class=vb><div class=bfa style=\'width:"+wA+"%;background:"+bc+"\'></div>"'
    ' +"<div class=bfu style=\'width:"+wU+"%;background:"+bc+"\'></div></div></td>";'
    '}'
    'function go(){'
    ' if(!HP){'
    ' document.getElementById("tw").innerHTML="<p style=\'color:#f55;padding:8px\'>h 파라미터 없음</p>";'
    ' return;'
    ' }'
    ' fetch("/pip_data?h="+encodeURIComponent(HP)+"&_t="+Date.now(),{cache:"no-cache"})'
    ' .then(function(r){return r.json();})'
    ' .then(function(d){'
    ' document.getElementById("ut").textContent=d.fetched_at||"";'
    ' var rs=d.hospitals||[];'
    ' if(!rs.length){'
    ' document.getElementById("tw").innerHTML="<p style=\'color:#aaa;padding:8px\'>데이터 없음</p>";'
    ' return;'
    ' }'
    ' var h="<table><thead><tr>"'
    ' +"<th class=nc>병원</th>"'
    ' +"<th class=vc>응급실</th>"'
    ' +"<th class=vc>중환자</th>"'
    ' +"<th class=vc>입원</th>"'
    ' +"</tr></thead><tbody>";'
    ' rs.forEach(function(r){'
    ' h+="<tr><td class=n>"+r.name+"</td>"'
    ' +cell(nz(r.hvec),nz(r.hvec_t,0))'
    ' +cell(nz(r.hicu),nz(r.hicu_t,0))'
    ' +cell(gcA(r),gcT(r))+"</tr>";'
    ' });'
    ' document.getElementById("tw").innerHTML=h+"</tbody></table>";'
    ' })'
    ' .catch(function(e){'
    ' document.getElementById("tw").innerHTML="<p style=\'color:#f55;padding:8px\'>오류: "+e.message+"</p>";'
    ' });'
    '}'
    'function updateBar(){'
    ' if(_iv<=0)return;'
    ' var rem=Math.max(0,_na-Date.now());'
    ' var pct=Math.min(100,Math.round((_iv-Math.max(0,_na-Date.now()))/_iv*100));'
    ' document.getElementById("cbf").style.width=pct+"%";'
    ' var s=Math.round(rem/1000);'
    ' document.getElementById("ct").textContent='
    ' "다음 "+Math.floor(s/60)+":"+(("0"+s%60).slice(-2));'
    '}'
    'function st(){'
    ' clearInterval(_t);clearInterval(_ct);'
    ' _iv=parseInt(document.getElementById("iv").value)*1000;'
    ' document.getElementById("ct").textContent="";'
    ' document.getElementById("cbf").style.width="0%";'
    ' if(_iv<=0)return;'
    ' _na=Date.now()+_iv;'
    ' _ct=setInterval(updateBar,1000);'
    ' _t=setInterval(function(){go();_na=Date.now()+_iv;},_iv);'
    '}'
    'function fit(){'
    ' var w=document.documentElement.clientWidth||320;'
    ' var s=Math.max(10,Math.min(28,w/22));'
    ' document.documentElement.style.fontSize=s+"px";'
    ' var k=s/16;'
    ' var css=document.getElementById("fitcss");'
    ' if(!css){css=document.createElement("style");css.id="fitcss";'
    ' document.head.appendChild(css);}'
    ' css.textContent="body{font-size:"+(13*k)+"px;}"'
    ' +"th{font-size:"+(11*k)+"px;}td.n{font-size:"+(11*k)+"px;}"'
    ' +".vn{font-size:"+(12*k)+"px;}.hdr-title{font-size:"+(14*k)+"px;}"'
    ' +".hdr-time{font-size:"+(11*k)+"px;}.ovb{font-size:"+(12*k)+"px;"'
    ' +"min-width:"+(22*k)+"px;height:"+(22*k)+"px;}";'
    '}'
    'fit();window.addEventListener("resize",fit);'
    'var OV=(new URLSearchParams(location.search).get("ov")==="1");'
    'if(OV){'
    ' document.getElementById("ovb").className="ovbtns on";'
    ' var ova=function(q){'
    ' fetch("/api/overlay?"+q+"&_t="+Date.now(),{cache:"no-store"})'
    ' .catch(function(){});'
    ' };'
    ' document.getElementById("bSm").onclick=function(){ova("action=scale&v=-1");};'
    ' document.getElementById("bLg").onclick=function(){ova("action=scale&v=1");};'
    ' document.getElementById("bUp").onclick=function(){ova("action=move&v=-24");};'
    ' document.getElementById("bDn").onclick=function(){ova("action=move&v=24");};'
    ' document.getElementById("bCl").onclick=function(){ova("action=close");};'
    '}'
    'document.getElementById("iv").addEventListener("change",function(){st();go();});'
    'document.getElementById("nb").addEventListener("click",go);'
    'go();st();'
    '</script>'
    '</body>'
    '</html>'
)


@flask_app.route('/pip')
def pip_page():
    """백그라운드 팝업 창용 미니 대시보드 페이지"""
    return _PIP_HTML, 200, {'Content-Type': 'text/html; charset=utf-8'}


@flask_app.route('/api/overlay')
def api_overlay():
    """오버레이 제어 — action=close|scale|move (오버레이 내부 버튼이 호출).
    close 는 모니터까지 중지하고 저장 설정을 지워 재시작 시 재출현을 막는다."""
    action = (request.args.get('action') or '').strip()
    try:
        value = int(request.args.get('v', '0'))
    except (TypeError, ValueError):
        value = 0
    if action == 'close':
        _stop_bed_notify()
        _clear_monitor_cfg()
        _overlay_remove()
        _dlog('[오버레이] 사용자 닫기 — 모니터 중지 및 설정 삭제')
        return jsonify({'ok': True, 'closed': True})
    if action in ('scale', 'move'):
        return jsonify({'ok': _overlay_adjust(action, value)})
    return jsonify({'ok': False, 'msg': 'unknown action'})


# ─── 이후 라우트들 이어서 붙임 ───
@flask_app.route('/pip_data')
def pip_data():
    """
    /pip 페이지 자동갱신용 JSON 엔드포인트.
    h 파라미터: "hpid|sido|gugun,..." (URL 인코딩)
    응답: {fetched_at, hospitals:[{name,hvec,hvec_t,hvicc,hvicc_t,hvgc,hvgc_t},...]}
    """
    h_raw = request.args.get('h', '')
    if not h_raw:
        # h 파라미터 없을 때 400 대신 빈 결과 → fetch 오류 방지
        _dlog('[pip_data] h 비어있음 → 빈 결과 반환')
        return jsonify({'hospitals': [], 'fetched_at': '', 'note': 'h_param_empty'})

    entries = []
    try:
        for part in h_raw.split(','):
            segs = part.strip().split('|')
            if len(segs) >= 3:
                entries.append({'hpid': segs[0], 'sido': segs[1], 'gugun': segs[2]})
        if not entries:
            return jsonify({'error': 'invalid h param'}), 400

        from collections import defaultdict
        region_map = defaultdict(list)
        hpid_set = {e['hpid'] for e in entries}
        for e in entries:
            region_map[(e['sido'], e['gugun'])].append(e['hpid'])

        result_map = {}

        # ── ① 비교화면 공유 캐시 우선 활용 ──────────────────────────────────
        # /compare가 방금 API를 호출한 결과가 캐시에 있으면 API를 재호출하지 않는다.
        # 캐시 유효 시간: 갱신 주기(iv_sec) 내에서는 동일 데이터를 쓰는 것이 합리적.
        # 단순하게 iv_sec-10초 이내에 갱신된 캐시는 유효한 것으로 간주한다.
        _iv_valid = max(30, _pip_state.get('iv_sec', 180)) - 10
        _now_ts   = datetime.now()
        _cached_hpids = set()
        with _compare_bed_cache_lock:
            for e in entries:
                _hpid = e['hpid']
                _ce = _compare_bed_cache.get(_hpid)
                if _ce:
                    try:
                        _age = (_now_ts - datetime.strptime(
                            f"{_now_ts.strftime('%Y-%m-%d')} {_ce['fetched_at']}",
                            '%Y-%m-%d %H:%M:%S'
                        )).total_seconds()
                    except Exception:
                        _age = 9999
                    if _age <= _iv_valid:
                        # 캐시가 충분히 최신 → 직접 사용
                        _dlog(f'[pip_data] {_hpid} 캐시 재사용 (age={_age:.0f}s) '
                              f'hvgc={_ce["hvgc"]} hv36={_ce["hv36"]}')
                        result_map[_hpid] = {
                            #  FIX: entries는 hpid|sido|gugun 만 가짐 → e2['name'] → KeyError.
                            # KeyError는 next()의 default를 우회하고 외부 except로 전달되어
                            # hospitals=[] 를 반환하는 silent 오류를 일으킨다.
                            # 캐시 엔트리에 저장된 name을 우선 사용, 없으면 hpid로 대체.
                            'name': _ce.get('name', _hpid),
                            'hvec': _ce['hvec'], 'hvec_t': _ce['hvec_t'],
                            'hvgc': _ce['hvgc'], 'hvgc_t': _ce['hvgc_t'],
                            'hv36': _ce['hv36'], 'hv36_t': _ce['hv36_t'],
                            'hicu': _ce['hicu'], 'hicu_t': _ce['hicu_t'],
                        }
                        _cached_hpids.add(_hpid)

        # 캐시에 없는 병원만 API 호출 대상으로 재구성
        _remaining_entries = [e for e in entries if e['hpid'] not in _cached_hpids]
        if _remaining_entries:
            _remaining_region_map = defaultdict(list)
            for e in _remaining_entries:
                _remaining_region_map[(e['sido'], e['gugun'])].append(e['hpid'])
            _remaining_hpid_set = {e['hpid'] for e in _remaining_entries}
        else:
            _remaining_region_map = {}
            _remaining_hpid_set   = set()
        # ─────────────────────────────────────────────────────────────────────

        # [최적화] 지역별 API 호출을 병렬화 (기존: 지역 수만큼 직렬).
        # 각 지역 결과는 로컬 dict에 모은 뒤 기존 순회 순서대로 병합한다.
        def _pip_region_task(sido, gugun):
            local_results = {}
            try:
                #  ROOT-FIX 2026-D1: compare 와 동일한 별칭/STAGE2 폴백 적용
                root_el = _region_api_root(API_URL, sido, gugun, timeout=10,
                                           ctx='pip')
                if root_el is None:
                    return local_results
                for item in root_el.findall('.//item'):
                    hpid = (item.findtext('hpid') or '').strip()
                    if hpid in _remaining_hpid_set:
                        _icu_tags = ['hvicc','hv2','hv3','hvncc','hv32','hvcc',
                                     'hv6','hv34','hvccc','hv35','hv31','hv33']
                        _icu_hvs  = ['HVS17','HVS06','HVS07','HVS08','HVS09','HVS11',
                                     'HVS12','HVS15','HVS16','HVS18','HVS05','HVS10']
                        _icu_avail = 0; _icu_total = 0; _icu_any = False
                        for _tg, _hvs in zip(_icu_tags, _icu_hvs):
                            _a = safe_int(item.findtext(_tg))
                            _t = get_hvs(item, _hvs)
                            if _a >= 0:
                                _icu_avail += _a; _icu_any = True
                            if _t >= 0:
                                _icu_total += _t

                        # ── 각 HVS 합계 직접 계산 후 캐시 갱신/조회 ──
                        # HVS 태그가 일시적으로 누락될 경우 마지막 양수값으로 대체
                        _prev = _pip_bed_total_cache.get(hpid, {})

                        _hvec_t_raw = get_hvs(item, 'HVS01')
                        if _hvec_t_raw >0:
                            _prev['hvec_t'] = _hvec_t_raw
                            _hvec_t = _hvec_t_raw
                        else:
                            _hvec_t = _prev.get('hvec_t', 0)
                        _dlog(f'[pip_data][HVS] {hpid} HVS01(응급합계): '
                              f'raw={_hvec_t_raw} → 사용={_hvec_t} '
                              f'({"신규" if _hvec_t_raw >0 else "캐시폴백" if _hvec_t >0 else "캐시없음"})')

                        _hvgc_t_raw = get_hvs(item, 'HVS38')
                        if _hvgc_t_raw >0:
                            _prev['hvgc_t'] = _hvgc_t_raw
                            _hvgc_t = _hvgc_t_raw
                        else:
                            _hvgc_t = _prev.get('hvgc_t', 0)
                        _dlog(f'[pip_data][HVS] {hpid} HVS38(일반입원합계): '
                              f'raw={_hvgc_t_raw} → 사용={_hvgc_t} '
                              f'({"신규" if _hvgc_t_raw >0 else "캐시폴백" if _hvgc_t >0 else "캐시없음"})')

                        _hv36_t_raw = get_hvs(item, 'HVS19')
                        if _hv36_t_raw >0:
                            _prev['hv36_t'] = _hv36_t_raw
                            _hv36_t = _hv36_t_raw
                        else:
                            _hv36_t = _prev.get('hv36_t', 0)
                        _dlog(f'[pip_data][HVS] {hpid} HVS19(응급전용입원합계): '
                              f'raw={_hv36_t_raw} → 사용={_hv36_t} '
                              f'({"신규" if _hv36_t_raw >0 else "캐시폴백" if _hv36_t >0 else "캐시없음"})')

                        if _icu_total >0:
                            _prev['hicu_t'] = _icu_total
                        else:
                            _icu_total = _prev.get('hicu_t', 0)
                        _dlog(f'[pip_data][ICU] {hpid} '
                              f'avail={_icu_avail} total={_icu_total} any={_icu_any} '
                              f'({"신규" if _icu_total >0 else "캐시폴백" if _prev.get("hicu_t", 0) >0 else "캐시없음"})')

                        _pip_bed_total_cache[hpid] = _prev

                        _raw_hvgc = safe_int(item.findtext('hvgc'))
                        _raw_hv36 = safe_int(item.findtext('hv36'))
                        _dlog(f'[pip_data] {hpid} hvgc={_raw_hvgc}(t={_hvgc_t}) '
                              f'hv36={_raw_hv36}(t={_hv36_t}) '
                              f'hvec={safe_int(item.findtext("hvec"))}(t={_hvec_t})')
                        local_results[hpid] = {
                            'name': (item.findtext('dutyName') or '').strip(),
                            'hvec': safe_int(item.findtext('hvec')),
                            'hvec_t': _hvec_t,
                            # hvgc: 일반 입원실 가용, hv36: 응급전용 입원실 가용
                            # PiP 입원 표시는 두 값 합산 (비교화면의 일반+응급전용 합계와 일치)
                            'hvgc':   _raw_hvgc,
                            'hvgc_t': _hvgc_t,
                            'hv36':   _raw_hv36,
                            'hv36_t': _hv36_t,
                            'hicu':   _icu_avail if _icu_any else -1,
                            'hicu_t': _icu_total,
                        }
                        #  FIX(2025): pip_data API 결과를 _compare_bed_cache에 역기록
                        # /compare 방문 없이도 다음 pip_data 호출에서 캐시 히트 가능.
                        # name도 함께 저장해 캐시 읽기 경로의 KeyError를 방지한다.
                        _now_str = datetime.now().strftime('%H:%M:%S')
                        with _compare_bed_cache_lock:
                            _compare_bed_cache[hpid] = {
                                'name': (item.findtext('dutyName') or '').strip(),
                                'hvec': safe_int(item.findtext('hvec')),
                                'hvec_t': _hvec_t,
                                'hvgc':   _raw_hvgc,
                                'hvgc_t': _hvgc_t,
                                'hv36':   _raw_hv36,
                                'hv36_t': _hv36_t,
                                'hicu':   _icu_avail if _icu_any else -1,
                                'hicu_t': _icu_total,
                                'fetched_at': _now_str,
                            }
            except Exception as ex:
                _log(f'[pip_data] API 오류 ({sido} {gugun}): {ex}', 'ERROR')
            return local_results

        if _remaining_region_map:
            _pip_futures = [_NET_POOL.submit(_pip_region_task, sido, gugun)
                            for (sido, gugun) in _remaining_region_map]
            for _pf in _pip_futures:
                result_map.update(_pf.result())
            _sync_kick_monitor()   # PiP 조회로 캐시가 갱신된 경우도 동기화

        hospitals = [result_map[e['hpid']] for e in entries if e['hpid'] in result_map]
        # ── 동기화 타임스탬프: /pip_data는 갱신하지 않음 ──
        # ts는 브라우저 POST /api/notify_refresh 에서만 갱신.
        # 여기서 갱신하면 브라우저 checkPipSync가 PiP 신호로 오인 → 무한루프 발생.
        return jsonify({
            'fetched_at': datetime.now().strftime('%H:%M:%S'),
            'hospitals':  hospitals,
            'ts':         _refresh_notify_ts[0],   # 읽기만, 쓰기 없음
        })

    except Exception as _ex:
        #  FIX(2025): 전체 예외를 HTTP 500 대신 200+빈 결과로 반환.
        # _dlog 로 Kivy 디버그 패널에도 표시 → 원인 추적 가능.
        _tb = traceback.format_exc()
        _log(f'[pip_data] 전체 예외: {_ex}\n{_tb}', 'ERROR')
        _dlog(f'[pip_data] 예외 {_ex}')   # 디버그 패널에 즉시 표시
        return jsonify({
            'hospitals':  [],
            'fetched_at': datetime.now().strftime('%H:%M:%S'),
            'error':      str(_ex),
            'ts':         _refresh_notify_ts[0],
        }), 200


@flask_app.route('/api/enter_pip', methods=['POST'])
def api_enter_pip():
    """브라우저  버튼 → Flask → Kivy PiP 요청."""
    data = request.get_json(silent=True) or {}
    _pip_state['h_param'] = data.get('h', '')
    try:
        _pip_state['iv_sec'] = max(30, int(data.get('iv', 180)))
    except (ValueError, TypeError):
        _pip_state['iv_sec'] = 180
    _pip_state['pending'] = True
    _PIP_LAST.update({'stage': '요청 수신', 'ok': False, 'reason': '',
                      'ts': time.time()})
    _ulog('PIP', '브라우저 [백그라운드] 탭 → h=%s iv=%ss'
          % (_pip_state['h_param'][:40], _pip_state['iv_sec']))

    # Kivy Activity 포그라운드 복귀 시도 (Flask 스레드에서 jnius 호출)
    if _IS_ANDROID:
        try:
            from jnius import autoclass
            Intent         = autoclass('android.content.Intent')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            activity       = PythonActivity.mActivity
            intent         = Intent(activity, PythonActivity)
            intent.addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT |
                            Intent.FLAG_ACTIVITY_SINGLE_TOP)
            activity.startActivity(intent)
            _ulog('PIP', 'startActivity 성공 → on_resume 대기')
        except Exception as _je:
            _ulog('PIP', 'startActivity 실패: %s: %s → Clock 폴백'
                  % (type(_je).__name__, _je))

    return jsonify({'ok': True, 'build': BUILD_ID})


@flask_app.route('/api/pip_status')
def api_pip_status():
    """[FIX 2026-H2] 브라우저가 PiP 실제 결과와 실패 사유를 즉시 확인.
    (기존에는 요청만 던지고 성공 여부를 알 길이 없어 '무반응'으로 보였다)"""
    d = dict(_PIP_LAST)
    d['age'] = round(time.time() - (d.get('ts') or 0), 1)
    d['android'] = _IS_ANDROID
    d['build'] = BUILD_ID
    return jsonify(d)


# ── 동기화: 마지막 갱신 시각 조회 / 알림 ──────────────────────────
@flask_app.route('/api/notify_refresh', methods=['GET', 'POST'])
def api_notify_refresh():
    """브라우저 갱신 완료 시 POST(→ PiP 갱신 신호); GET → 현재 타임스탬프 반환"""
    if request.method == 'POST':
        _refresh_notify_ts[0] = time.time()
        # ── 병원 선택 변경 감지: 브라우저가 현재 URL의 h 파라미터를 함께 전송함
        # PiP가 이미 활성화된 상태에서 비교화면의 병원 목록이 바뀌면
        # 기존 h_param을 새 값으로 교체하여 PiP 표시 목록을 동기화한다.
        data = request.get_json(silent=True) or {}
        new_h = (data.get('h') or '').strip()
        if new_h and new_h != _pip_state.get('h_param', ''):
            _dlog(f'[Sync] notify_refresh: h_param 변경 감지 → '
                  f'{_pip_state.get("h_param","")[:30]} → {new_h[:30]}')
            _pip_state['h_param'] = new_h
        # PiP Kivy 쪽에서 즉시 갱신하도록 pip_state에 fetch 요청 플래그 추가
        if _pip_state.get('h_param'):
            _pip_state['fetch_pending'] = True
        return jsonify({'ok': True, 'ts': _refresh_notify_ts[0]})
    return jsonify({'ts': _refresh_notify_ts[0]})


# ── 햅틱: 브라우저 갱신 시 Kivy 진동+알림음 요청 ─────────────────
@flask_app.route('/api/haptic', methods=['POST'])
def api_haptic():
    """브라우저 갱신(수동/자동) 완료 후 POST → Kivy 햅틱 실행"""
    _haptic_pending[0] = True
    return jsonify({'ok': True})


# ══════════════════════════════════════════════════════════════════
#  [ROOT-FIX 2026-E1] 인스턴스 식별 · 완전종료
# ══════════════════════════════════════════════════════════════════
@flask_app.route('/api/whoami')
def api_whoami():
    """이 포트를 점유한 프로세스의 신원. 신규 인스턴스가 좀비 판별에 사용."""
    return jsonify({'ok': True, 'pid': _APP_PID, 'boot_ts': _APP_BOOT_TS,
                    'uptime': round(time.time() - _APP_BOOT_TS, 1)})


@flask_app.route('/api/app_exit', methods=['GET', 'POST'])
def api_app_exit():
    """완전종료. PiP·알림·오버레이·WakeLock·상태파일을 모두 정리한 뒤 프로세스 종료.
    - 사용자가 [종료] 버튼을 누른 경우
    - 신규 인스턴스가 구 인스턴스를 인수(takeover)하는 경우 (reason=takeover)
    """
    reason = request.args.get('reason', 'user')
    _dlog('[EXIT] /api/app_exit 수신 reason=%s pid=%s' % (reason, _APP_PID))
    for _fn in list(_EXIT_HOOKS):
        try:
            _fn(reason)
        except Exception as _he:
            _dlog('[EXIT] 훅 실패(무시): %s' % _he)

    def _hard_exit():
        time.sleep(0.6)          # 응답 flush 대기
        _dlog('[EXIT] os._exit(0)')
        try:
            for _h in logging.getLogger().handlers:
                try:
                    _h.flush()
                except Exception:
                    pass
        except Exception:
            pass
        os._exit(0)

    threading.Thread(target=_hard_exit, daemon=True, name='HardExit').start()
    return jsonify({'ok': True, 'pid': _APP_PID, 'reason': reason})
def debug_log():
    """앱 로그를 브라우저에서 확인 + compare 라우트 진단 정보"""
    global LOG_FILE
    # _LOG_FILE_REF는 모듈 레벨 공유 변수 — from __main__ 불필요
    if not LOG_FILE and _LOG_FILE_REF[0]:
        LOG_FILE = _LOG_FILE_REF[0]

    log_content = ''
    try:
        if LOG_FILE and os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r', encoding='utf-8', errors='replace') as f:
                log_content = f.read()
        else:
            for _ep in _crash_paths:
                if os.path.exists(_ep):
                    with open(_ep, 'r', encoding='utf-8', errors='replace') as f:
                        log_content = f'[emergency log: {_ep}]\n' + f.read()
                    break
            if not log_content:
                log_content = f'[로그파일 없음] LOG_FILE={LOG_FILE}'
    except Exception as e:
        log_content = f'로그 읽기 실패: {e}\n{traceback.format_exc()}'

    diag_html = '''
<div style="background:#2d2d2d;padding:12px;margin-bottom:10px;border-radius:6px;border-left:4px solid #9cdcfe;">
<h3 style="color:#9cdcfe;margin:0 0 8px 0;font-size:0.9rem;">Compare 라우트 테스트</h3>
<p style="color:#aaa;font-size:0.8rem;margin-bottom:8px;">아래 URL로 compare 페이지를 직접 테스트할 수 있습니다 (GET 방식):<br>
<code style="color:#4ec9b0;">/compare?h=HPID1|시도|시군구,HPID2|시도|시군구</code>
</p>
</div>'''

    return render_template_string('''
<!DOCTYPE html><html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>디버그 로그</title>
<style>body{font-family:monospace;background:#1e1e1e;color:#d4d4d4;margin:0;padding:0;}
.header{background:#333;padding:12px 16px;position:sticky;top:0;display:flex;
        justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;}
.header h2{margin:0;color:#9cdcfe;font-size:1rem;}
.btn{padding:6px 14px;background:#0e639c;color:white;border:none;
     border-radius:4px;cursor:pointer;font-size:0.85rem;text-decoration:none;}
.btn:hover{background:#1177bb;}
pre{padding:16px;margin:0;white-space:pre-wrap;word-break:break-all;
    font-size:0.8rem;line-height:1.5;}
.err{color:#f48771;} .warn{color:#dcdcaa;} .info{color:#9cdcfe;}
</style></head><body>
<div class="header">
  <h2>디버그 로그 — {{ log_file }}</h2>
  <div style="display:flex;gap:8px;">
    <a class="btn" href="/debug">새로고침</a>
    <a class="btn" href="/">홈</a>
  </div>
</div>
<div style="padding:12px;">{{ diag|safe }}</div>
<pre id="log">{{ content }}</pre>
<script>const pre = document.getElementById('log');
pre.innerHTML = pre.textContent
  .split('\\n')
  .map(l =>l.includes('[ERROR]') || l.includes('ERROR') ? `<span class="err">${l}</span>`
           : l.includes('[WARNING]') || l.includes('WARN') ? `<span class="warn">${l}</span>`
           : l.includes('[INFO]') || l.includes('[compare]') ? `<span class="info">${l}</span>` : l)
  .join('\\n');
window.scrollTo(0, document.body.scrollHeight);
</script></body></html>
''', content=log_content, log_file=LOG_FILE or '미설정', diag=diag_html)



@flask_app.route('/debug/msgs/<hpid>')
def debug_msgs(hpid):
    """분당제생병원 등 특정 병원의 메시지 API 원본 전체를 덤프"""
    import html as _html
    rows = []
    page = 1
    total_items = 0
    try:
        while True:
            params = {
                'serviceKey': SERVICE_KEY,
                'HPID': hpid.strip(),
                'pageNo': str(page),
                'numOfRows': '100',
            }
            resp = _http_get(MSG_API_URL, params=params, timeout=12)
            raw_xml = resp.text

            if resp.status_code != 200:
                rows.append(f'<tr style="background:#5c1a1a"><td colspan="9">HTTP {resp.status_code}</td></tr>')
                break

            root = ET.fromstring(resp.content)
            items = root.findall('.//item')
            total_count = int(root.findtext('.//totalCount') or '0')

            for idx, item in enumerate(items, 1):
                total_items += 1
                # 원시 필드 전체 수집
                all_tags = {child.tag: (child.text or '') for child in item}

                sym_blk_msg     = all_tags.get('symBlkMsg', '').strip()
                sym_typ_cod_mag = all_tags.get('symTypCodMag', '').strip()
                sym_typ_cod     = all_tags.get('symTypCod', '').strip()
                sym_out_dsp_yon = all_tags.get('symOutDspYon', '').strip()
                sym_blk_msg_typ = all_tags.get('symBlkMsgTyp', '').strip()

                # 처리 결과 시뮬레이션
                label_raw   = _resolve_type_label(sym_typ_cod_mag, sym_typ_cod)
                clean_msg   = _clean_msg(sym_blk_msg)
                label_final = '' if label_raw in ('응급실', '') else label_raw
                skipped     = (not clean_msg and not label_final)
                cat         = _categorize_exception(label_final, clean_msg) if not skipped else '(건너뜀)'
                if skipped:
                    row_bg = 'background:#3d1f1f'
                    status = ' SKIP'
                else:
                    row_bg = 'background:#1e2a1e'
                    status = ' OK'

                # 나머지 태그들
                extra_tags = {k:v for k,v in all_tags.items()
                              if k not in ('symBlkMsg','symTypCodMag','symTypCod',
                                          'symOutDspYon','symBlkMsgTyp')}

                rows.append(f'''<tr style="{row_bg}">
  <td style="color:#9cdcfe;white-space:nowrap;">p{page}-{idx}</td>
  <td style="color:#ce9178;">{_html.escape(sym_typ_cod)}</td>
  <td style="color:#4ec9b0;">{_html.escape(sym_typ_cod_mag)}</td>
  <td style="color:#dcdcaa;">{_html.escape(label_raw)}</td>
  <td style="color:#d4d4d4;">{_html.escape(sym_blk_msg)}</td>
  <td style="color:#d4d4d4;">{_html.escape(clean_msg)}</td>
  <td style="color:#9cdcfe;">{_html.escape(sym_blk_msg_typ)}</td>
  <td style="color:#9cdcfe;">{_html.escape(sym_out_dsp_yon)}</td>
  <td style="color:{"#f48771" if skipped else "#4ec9b0"};font-weight:700;">{status}<br><span style="font-size:0.8em;color:#dcdcaa;">{cat}</span></td>
  <td style="color:#888;font-size:0.75em;">{_html.escape(str(extra_tags))}</td>
</tr>''')

            if len(items) < 100 or page * 100 >= total_count:
                break
            page += 1

        raw_section = f'<details style="margin:12px;"><summary style="color:#9cdcfe;cursor:pointer;">원시 XML (마지막 페이지)</summary><pre style="background:#111;padding:12px;margin:8px;font-size:0.75rem;white-space:pre-wrap;overflow-x:auto;">{_html.escape(raw_xml)}</pre></details>'

    except Exception as e:
        rows.append(f'<tr><td colspan="10" style="color:#f48771;">오류: {_html.escape(str(e))}<br><pre>{_html.escape(traceback.format_exc())}</pre></td></tr>')
        raw_section = ''

    table = '<tr style="background:#333;"><th>순번</th><th>symTypCod</th><th>symTypCodMag(원본)</th><th>label(변환)</th><th>symBlkMsg(원본)</th><th>clean_msg</th><th>msgTyp</th><th>DspYon</th><th>처리결과</th><th>기타태그</th></tr>' + ''.join(rows)

    return f'''<!DOCTYPE html><html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MSG 디버그 – {_html.escape(hpid)}</title>
<style>body{{font-family:monospace;background:#1e1e1e;color:#d4d4d4;margin:0;padding:0;}}
.hdr{{background:#252526;padding:10px 14px;position:sticky;top:0;border-bottom:1px solid #444;display:flex;gap:10px;align-items:center;flex-wrap:wrap;}}
h2{{margin:0;color:#9cdcfe;font-size:1rem;}}
a.btn{{padding:5px 12px;background:#0e639c;color:white;border:none;border-radius:4px;
       font-size:0.82rem;text-decoration:none;}}
a.btn:hover{{background:#1177bb;}}
.wrap{{overflow-x:auto;margin:10px;}}
table{{border-collapse:collapse;min-width:100%;font-size:0.78rem;}}
th,td{{border:1px solid #3c3c3c;padding:5px 7px;vertical-align:top;word-break:break-all;}}
</style></head><body>
<div class="hdr">
  <h2>메시지 API 원본 덤프 — HPID: {_html.escape(hpid)} ({total_items}건)</h2>
  <a class="btn" href="/debug/msgs/{_html.escape(hpid)}">갱신</a>
  <a class="btn" href="/debug">일반로그</a>
  <a class="btn" href="/">홈</a>
</div>
{raw_section}
<div class="wrap"><table>{table}</table></div>
</body></html>'''




# ══════════════════════════════════════════════════════════════════
#  디버그 전용: 병원 검색 + 메시지 API 원본을 복사 가능한 텍스트로 출력
# ══════════════════════════════════════════════════════════════════

@flask_app.route('/debug/tool')
def debug_tool():
    """병원 이름으로 HPID 검색 → 메시지 API 원본 복사 도구"""
    return render_template_string(r"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>API 디버그 도구</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Malgun Gothic',monospace;background:#1e1e1e;color:#d4d4d4;padding:16px;}
h2{color:#9cdcfe;margin-bottom:16px;font-size:1.1rem;}
h3{color:#9cdcfe;margin:16px 0 8px;font-size:0.95rem;}
label{display:block;color:#9cdcfe;font-size:0.85rem;margin-bottom:4px;}
input,select{width:100%;padding:9px;background:#2d2d2d;border:1px solid #555;
  color:#d4d4d4;border-radius:5px;font-size:0.9rem;margin-bottom:10px;}
.row{display:flex;gap:8px;flex-wrap:wrap;}
.row input{flex:1;min-width:120px;}
.btn{padding:9px 18px;background:#0e639c;color:white;border:none;border-radius:5px;
  font-size:0.9rem;font-weight:600;cursor:pointer;white-space:nowrap;}
.btn:hover{background:#1177bb;}
.btn-green{background:#2d7a2d;}.btn-green:hover{background:#3a9a3a;}
.btn-copy{background:#5a3e9a;}.btn-copy:hover{background:#6d50b5;}
#result{margin-top:14px;}
.hospital-row{background:#252526;border:1px solid #3c3c3c;border-radius:6px;
  padding:10px;margin-bottom:8px;display:flex;justify-content:space-between;
  align-items:center;flex-wrap:wrap;gap:8px;}
.h-name{color:#4ec9b0;font-weight:700;}
.h-info{color:#888;font-size:0.8rem;}
textarea{width:100%;height:480px;background:#111;color:#d4d4d4;border:1px solid #555;
  border-radius:6px;padding:12px;font-size:0.78rem;font-family:monospace;
  line-height:1.5;resize:vertical;margin-top:8px;}
.status{padding:8px;border-radius:5px;margin:8px 0;font-size:0.85rem;}
.status.ok{background:#1e3a1e;color:#4ec9b0;}
.status.err{background:#3a1e1e;color:#f48771;}
.status.loading{background:#1e2d3a;color:#9cdcfe;}
a{color:#9cdcfe;}
</style></head><body>

<h2>API 디버그 도구 — 병원 검색 + 메시지 원본 추출</h2>

<div style="background:#252526;padding:14px;border-radius:8px;margin-bottom:16px;">
  <h3>① 병원 HPID 검색</h3>
  <div class="row">
    <div style="flex:1;min-width:140px;">
      <label>시/도</label>
      <input id="sido" placeholder="예: 경기도" value="경기도">
    </div>
    <div style="flex:1;min-width:140px;">
      <label>시/군/구</label>
      <input id="gugun" placeholder="예: 성남시" value="성남시">
    </div>
    <div style="flex:2;min-width:140px;">
      <label>병원명 (일부)</label>
      <input id="hname" placeholder="예: 분당제생" value="분당제생">
    </div>
  </div>
  <button class="btn" onclick="searchHospital()">병원 검색</button>
  <div id="result"></div>
</div>

<div style="background:#252526;padding:14px;border-radius:8px;">
  <h3>② HPID로 메시지 API 원본 직접 조회</h3>
  <div class="row">
    <input id="direct_hpid" placeholder="HPID 직접 입력 (예: C1300020)" style="flex:1;">
    <button class="btn btn-green" onclick="fetchMsgs()">API 원본 가져오기</button>
    <button class="btn btn-copy" onclick="copyAll()">전체 복사</button>
  </div>
  <div id="fetchStatus"></div>
  <textarea id="output" placeholder="여기에 원본 데이터가 출력됩니다..."></textarea>
</div>

<script>async function searchHospital() {
  const sido  = document.getElementById('sido').value.trim();
  const gugun = document.getElementById('gugun').value.trim();
  const name  = document.getElementById('hname').value.trim().toLowerCase();
  const res   = document.getElementById('result');
  if (!sido) { res.innerHTML='<div class="status err">시/도를 입력하세요</div>'; return; }
  res.innerHTML = '<div class="status loading">검색 중...</div>';
  try {
    const r = await fetch(`/api/hospitals?sido=${encodeURIComponent(sido)}&gugun=${encodeURIComponent(gugun)}`);
    const data = await r.json();
    if (!data.success) { res.innerHTML=`<div class="status err">오류: ${data.error}</div>`; return; }
    const filtered = name
      ? data.hospitals.filter(h =>h.name.toLowerCase().includes(name))
      : data.hospitals;
    if (!filtered.length) { res.innerHTML='<div class="status err">병원을 찾을 수 없습니다</div>'; return; }
    res.innerHTML = filtered.map(h => `
      <div class="hospital-row">
        <div>
          <div class="h-name">${h.name}</div>
          <div class="h-info">HPID: ${h.hpid} | ${h.dutyAddr || ''}</div>
        </div>
        <button class="btn btn-green" onclick="setHpid('${h.hpid}')">이 병원 선택</button>
      </div>`).join('');
  } catch(e) {
    res.innerHTML = `<div class="status err">네트워크 오류: ${e.message}</div>`;
  }
}

function setHpid(hpid) {
  document.getElementById('direct_hpid').value = hpid;
  fetchMsgs();
}

async function fetchMsgs() {
  const hpid = document.getElementById('direct_hpid').value.trim();
  const out   = document.getElementById('output');
  const stat  = document.getElementById('fetchStatus');
  if (!hpid) { stat.innerHTML='<div class="status err">HPID를 입력하세요</div>'; return; }
  stat.innerHTML = '<div class="status loading">API 조회 중...</div>';
  out.value = '';
  try {
    const r = await fetch(`/debug/msgs_json/${encodeURIComponent(hpid)}`);
    const data = await r.json();
    if (data.error) { stat.innerHTML=`<div class="status err">오류: ${data.error}</div>`; return; }
    const lines = [];
    lines.push(`=== 메시지 API 원본 덤프 ===`);
    lines.push(`HPID: ${hpid}`);
    lines.push(`조회시각: ${new Date().toLocaleString('ko-KR')}`);
    lines.push(`총 레코드: ${data.total}건`);
    lines.push('');
    data.items.forEach((item, i) => {
      lines.push(`── 레코드 ${i+1} ──────────────────────`);
      Object.entries(item).forEach(([k,v]) => {
        if (v !== null && v !== undefined && v !== '')
          lines.push(`  ${k}: ${v}`);
      });
      lines.push(`  [처리결과] label=${item._label_resolved || ''} | clean_msg=${item._clean_msg || ''} | cat=${item._category || ''}`);
      lines.push('');
    });
    lines.push('=== 원시 XML ===');
    lines.push(data.raw_xml || '(없음)');
    out.value = lines.join('\n');
    stat.innerHTML = `<div class="status ok"> ${data.total}건 로드 완료 — 아래 내용을 복사하세요</div>`;
    out.focus(); out.select();
  } catch(e) {
    stat.innerHTML = `<div class="status err">오류: ${e.message}</div>`;
  }
}

function copyAll() {
  const out = document.getElementById('output');
  out.select();
  try {
    navigator.clipboard.writeText(out.value)
      .then(()=>{ document.getElementById('fetchStatus').innerHTML='<div class="status ok">클립보드에 복사됨</div>'; })
      .catch(()=>{ document.execCommand('copy'); document.getElementById('fetchStatus').innerHTML='<div class="status ok">복사됨</div>'; });
  } catch(e) {
    document.execCommand('copy');
    document.getElementById('fetchStatus').innerHTML='<div class="status ok">복사됨</div>';
  }
}
</script>
</body></html>""")


@flask_app.route('/debug/msgs_json/<hpid>')
def debug_msgs_json(hpid):
    """메시지 API 원본을 JSON으로 반환 (debug_tool 용)"""
    import html as _html
    items_out = []
    raw_xml   = ''
    total     = 0
    page      = 1
    try:
        while True:
            params = {
                'serviceKey': SERVICE_KEY,
                'HPID': hpid.strip(),
                'pageNo': str(page),
                'numOfRows': '100',
            }
            resp = _http_get(MSG_API_URL, params=params, timeout=12)
            raw_xml = resp.text
            if resp.status_code != 200:
                return jsonify({'error': f'HTTP {resp.status_code}', 'raw_xml': raw_xml})

            root = ET.fromstring(resp.content)
            items = root.findall('.//item')
            total_count = int(root.findtext('.//totalCount') or '0')

            for item in items:
                total += 1
                # 모든 태그 수집
                row = {child.tag: (child.text or '').strip() for child in item}

                # 처리 시뮬레이션 (라벨 변환 결과 포함)
                sym_typ_cod_mag = row.get('symTypCodMag', '')
                sym_typ_cod     = row.get('symTypCod', '')
                sym_blk_msg     = row.get('symBlkMsg', '')
                label_raw   = _resolve_type_label(sym_typ_cod_mag, sym_typ_cod)
                clean_msg   = _clean_msg(sym_blk_msg)
                label_final = '' if label_raw in ('응급실', '') else label_raw
                skipped     = not clean_msg and not label_final
                cat         = '(건너뜀-SKIP)' if skipped else _categorize_exception(label_final, clean_msg)

                row['_label_resolved'] = label_raw
                row['_clean_msg']      = clean_msg
                row['_label_final']    = label_final
                row['_category']       = cat
                row['_skipped']        = skipped
                items_out.append(row)

            if len(items) < 100 or page * 100 >= total_count:
                break
            page += 1

    except Exception as e:
        return jsonify({'error': str(e), 'raw_xml': raw_xml, 'items': items_out, 'total': total})

    return jsonify({'total': total, 'items': items_out, 'raw_xml': raw_xml, 'error': None})



# ══════════════════════════════════════════════════════════════════
#  매뉴얼 9개 API 전체 호출 디버그 도구
# ══════════════════════════════════════════════════════════════════

# API URL 상수 (9개 전체)
_BASE = 'https://apis.data.go.kr/B552657/ErmctInfoInqireService/'
_APIS = {
    'getEmrrmRltmUsefulSckbdInfoInqire': _BASE + 'getEmrrmRltmUsefulSckbdInfoInqire',
    'getSrsillDissAceptncPosblInfoInqire': _BASE + 'getSrsillDissAceptncPosblInfoInqire',
    'getEgytListInfoInqire': _BASE + 'getEgytListInfoInqire',
    'getEgytLcinfoInqire': _BASE + 'getEgytLcinfoInqire',
    'getEgytBassInfoInqire': _BASE + 'getEgytBassInfoInqire',
    'getStrmListInfoInqire': _BASE + 'getStrmListInfoInqire',
    'getStrmLcinfoInqire': _BASE + 'getStrmLcinfoInqire',
    'getStrmBassInfoInqire': _BASE + 'getStrmBassInfoInqire',
    'getEmrrmSrsillDissMsgInqire': _BASE + 'getEmrrmSrsillDissMsgInqire',
}

def _call_api(endpoint_name, params):
    """API 호출 후 (raw_xml, items_list, error_str) 반환"""
    url = _APIS[endpoint_name]
    p = {'serviceKey': SERVICE_KEY}
    p.update(params)
    try:
        resp = _http_get(url, params=p, timeout=12)
        raw = resp.text
        if resp.status_code != 200:
            return raw, [], f'HTTP {resp.status_code}'
        root = ET.fromstring(resp.content)
        code = root.findtext('.//resultCode', '')
        msg  = root.findtext('.//resultMsg', '')
        if code != '00':
            return raw, [], f'API 오류 {code}: {msg}'
        items = []
        for item in root.findall('.//item'):
            items.append({child.tag: (child.text or '').strip() for child in item})
        return raw, items, None
    except Exception as e:
        return '', [], str(e)


@flask_app.route('/debug/full')
def debug_full_form():
    """병원 HPID 입력 폼"""
    return render_template_string(r"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>전체 API 디버그</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Malgun Gothic',monospace;background:#1e1e1e;color:#d4d4d4;padding:20px;}
h2{color:#9cdcfe;margin-bottom:18px;}
label{display:block;color:#9cdcfe;font-size:0.85rem;margin-bottom:5px;}
input{width:100%;padding:10px;background:#2d2d2d;border:1px solid #555;
  color:#d4d4d4;border-radius:5px;font-size:0.95rem;margin-bottom:12px;}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;}
.row input{margin-bottom:0;flex:1;min-width:120px;}
.btn{padding:10px 20px;background:#0e639c;color:white;border:none;border-radius:5px;
  font-size:0.95rem;font-weight:600;cursor:pointer;}
.btn:hover{background:#1177bb;}
.note{color:#888;font-size:0.82rem;margin-top:8px;}
a{color:#4ec9b0;}
</style></head><body>
<h2>매뉴얼 9개 API 전체 조회 디버그</h2>
<p class="note" style="margin-bottom:18px;">특정 병원 HPID를 입력하면 매뉴얼의 모든 API를 호출하여 원본 응답 전체를 표시합니다.</p>

<form method="get" action="/debug/full/result">
  <label>HPID *</label>
  <input name="hpid" placeholder="예: A2100025 (분당제생병원)" required>
  <label>시/도 (STAGE1/Q0 파라미터용)</label>
  <input name="sido" placeholder="예: 경기도">
  <label>시/군/구 (STAGE2/Q1 파라미터용)</label>
  <input name="gugun" placeholder="예: 성남시">
  <button class="btn" type="submit">전체 API 조회 시작</button>
</form>
<p class="note">시/도·시/군/구를 입력하지 않으면 기본정보 API에서 자동으로 추출을 시도합니다.</p>
<p style="margin-top:14px;"><a href="/">← 홈으로</a></p>
</body></html>""")


@flask_app.route('/debug/full/result')
def debug_full_result():
    """9개 API 전체 호출 후 결과를 복사 가능한 텍스트로 출력"""
    import html as _html

    hpid  = request.args.get('hpid', '').strip()
    sido  = request.args.get('sido', '').strip()
    gugun = request.args.get('gugun', '').strip()

    if not hpid:
        return '<p style="color:red;padding:20px;">HPID가 필요합니다.</p>', 400

    results = {}  # {api_name: {'raw': str, 'items': list, 'error': str|None}}

    # ── ① getEgytBassInfoInqire (HPID 기반 기본정보 → lat/lon/sido/gugun 추출)
    raw, items, err = _call_api('getEgytBassInfoInqire', {'HPID': hpid, 'numOfRows': '1'})
    results['getEgytBassInfoInqire'] = {'raw': raw, 'items': items, 'error': err}

    lat = lon = ''
    name_from_basic = ''
    if items:
        lat  = items[0].get('wgs84Lat', '')
        lon  = items[0].get('wgs84Lon', '')
        name_from_basic = items[0].get('dutyName', '')
        # sido/gugun 자동 추출 (주소에서)
        if not sido or not gugun:
            addr = items[0].get('dutyAddr', '')
            parts = addr.split()
            if len(parts) >= 2:
                if not sido:  sido  = parts[0]
                if not gugun: gugun = parts[1]

    # ── ② getEmrrmRltmUsefulSckbdInfoInqire (실시간 가용병상, STAGE1/2)
    raw, items, err = _call_api('getEmrrmRltmUsefulSckbdInfoInqire',
        {'STAGE1': sido, 'STAGE2': gugun, 'numOfRows': '100', 'pageNo': '1'})
    # HPID 필터
    filtered = [it for it in items if it.get('hpid','').strip() == hpid]
    results['getEmrrmRltmUsefulSckbdInfoInqire'] = {
        'raw': raw, 'items': filtered,
        'error': err,
        'note': f'전체 {len(items)}건 중 HPID 일치 {len(filtered)}건 표시'
    }

    # ── ③ getSrsillDissAceptncPosblInfoInqire (중증질환 수용가능정보)
    raw, items, err = _call_api('getSrsillDissAceptncPosblInfoInqire',
        {'STAGE1': sido, 'STAGE2': gugun, 'numOfRows': '100', 'pageNo': '1'})
    filtered = [it for it in items if it.get('hpid','').strip() == hpid]
    results['getSrsillDissAceptncPosblInfoInqire'] = {
        'raw': raw, 'items': filtered,
        'error': err,
        'note': f'전체 {len(items)}건 중 HPID 일치 {len(filtered)}건 표시'
    }

    # ── ④ getEgytListInfoInqire (응급의료기관 목록)
    raw, items, err = _call_api('getEgytListInfoInqire',
        {'Q0': sido, 'Q1': gugun, 'numOfRows': '100', 'pageNo': '1'})
    filtered = [it for it in items if it.get('hpid','').strip() == hpid]
    results['getEgytListInfoInqire'] = {
        'raw': raw, 'items': filtered,
        'error': err,
        'note': f'전체 {len(items)}건 중 HPID 일치 {len(filtered)}건 표시'
    }

    # ── ⑤ getEgytLcinfoInqire (응급의료기관 위치정보, 위경도 필요)
    if lat and lon:
        raw, items, err = _call_api('getEgytLcinfoInqire',
            {'WGS84_LON': lon, 'WGS84_LAT': lat, 'numOfRows': '20', 'pageNo': '1'})
        filtered = [it for it in items if it.get('hpid','').strip() == hpid]
        results['getEgytLcinfoInqire'] = {
            'raw': raw, 'items': filtered, 'error': err,
            'note': f'위경도({lat},{lon}) 기준 전체 {len(items)}건 중 HPID 일치 {len(filtered)}건'
        }
    else:
        results['getEgytLcinfoInqire'] = {
            'raw': '', 'items': [], 'error': '위경도 정보 없음 (기본정보 API 조회 실패)',
            'note': ''
        }

    # ── ⑥ getStrmListInfoInqire (외상센터 목록)
    raw, items, err = _call_api('getStrmListInfoInqire',
        {'Q0': sido, 'Q1': gugun, 'numOfRows': '100', 'pageNo': '1'})
    filtered = [it for it in items if it.get('hpid','').strip() == hpid]
    results['getStrmListInfoInqire'] = {
        'raw': raw, 'items': filtered, 'error': err,
        'note': f'전체 {len(items)}건 중 HPID 일치 {len(filtered)}건'
    }

    # ── ⑦ getStrmLcinfoInqire (외상센터 위치정보, 위경도 필요)
    if lat and lon:
        raw, items, err = _call_api('getStrmLcinfoInqire',
            {'WGS84_LON': lon, 'WGS84_LAT': lat, 'numOfRows': '20', 'pageNo': '1'})
        filtered = [it for it in items if it.get('hpid','').strip() == hpid]
        results['getStrmLcinfoInqire'] = {
            'raw': raw, 'items': filtered, 'error': err,
            'note': f'위경도 기준 전체 {len(items)}건 중 HPID 일치 {len(filtered)}건'
        }
    else:
        results['getStrmLcinfoInqire'] = {
            'raw': '', 'items': [], 'error': '위경도 정보 없음', 'note': ''
        }

    # ── ⑧ getStrmBassInfoInqire (외상센터 기본정보)
    raw, items, err = _call_api('getStrmBassInfoInqire',
        {'HPID': hpid, 'numOfRows': '1'})
    results['getStrmBassInfoInqire'] = {'raw': raw, 'items': items, 'error': err, 'note': ''}

    # ── ⑨ getEmrrmSrsillDissMsgInqire (예외상황 메시지, 페이지네이션)
    all_msg_items = []
    msg_raw_last = ''
    msg_page = 1
    msg_err = None
    while True:
        raw, items, err = _call_api('getEmrrmSrsillDissMsgInqire',
            {'HPID': hpid, 'numOfRows': '100', 'pageNo': str(msg_page)})
        msg_raw_last = raw
        if err:
            msg_err = err
            break
        all_msg_items.extend(items)
        if len(items) < 100:
            break
        msg_page += 1
    results['getEmrrmSrsillDissMsgInqire'] = {
        'raw': msg_raw_last, 'items': all_msg_items,
        'error': msg_err,
        'note': f'페이지 {msg_page}까지 수집, 총 {len(all_msg_items)}건'
    }

    # ── 텍스트 출력 생성
    SEP = '=' * 72
    SSEP = '-' * 50
    lines = []
    lines.append(SEP)
    lines.append(f' 매뉴얼 9개 API 전체 조회 결과')
    lines.append(f' HPID: {hpid}  병원명: {name_from_basic}')
    lines.append(f' 시도: {sido}  시군구: {gugun}')
    lines.append(f' 위도: {lat}  경도: {lon}')
    lines.append(f' 조회시각: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append(SEP)

    api_labels = {
        'getEgytBassInfoInqire': '① 응급의료기관 기본정보 조회',
        'getEmrrmRltmUsefulSckbdInfoInqire': '② 응급실 실시간 가용병상정보 조회',
        'getSrsillDissAceptncPosblInfoInqire': '③ 중증질환자 수용가능정보 조회',
        'getEgytListInfoInqire': '④ 응급의료기관 목록정보 조회',
        'getEgytLcinfoInqire': '⑤ 응급의료기관 위치정보 조회',
        'getStrmListInfoInqire': '⑥ 외상센터 목록정보 조회',
        'getStrmLcinfoInqire': '⑦ 외상센터 위치정보 조회',
        'getStrmBassInfoInqire': '⑧ 외상센터 기본정보 조회',
        'getEmrrmSrsillDissMsgInqire': '⑨ 응급실 및 중증질환 메시지 조회',
    }

    for api_name, label in api_labels.items():
        r = results.get(api_name, {})
        lines.append('')
        lines.append(SSEP)
        lines.append(f' {label}')
        lines.append(f' 엔드포인트: {api_name}')
        if r.get('note'):
            lines.append(f' [{r["note"]}]')
        if r.get('error'):
            lines.append(f' 오류: {r["error"]}')
        lines.append(SSEP)
        items_list = r.get('items', [])
        if items_list:
            lines.append(f' 파싱 결과: {len(items_list)}건')
            for i, item in enumerate(items_list, 1):
                lines.append(f' ── 레코드 {i} ──')
                for k, v in sorted(item.items()):
                    if v:
                        lines.append(f' {k}: {v}')
        else:
            lines.append(' 파싱 결과: 없음 (해당 병원 데이터 미존재 또는 오류)')
        lines.append('')
        lines.append(' [원시 XML]')
        raw_text = r.get('raw', '').strip()
        lines.append(raw_text if raw_text else ' (없음)')

    lines.append('')
    lines.append(SEP)
    lines.append(' END')
    lines.append(SEP)

    full_text = '\n'.join(lines)

    return render_template_string(r"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>전체 API 결과 – {{ hpid }}</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:monospace;background:#1e1e1e;color:#d4d4d4;padding:0;}
.hdr{background:#252526;padding:11px 16px;position:sticky;top:0;
  border-bottom:1px solid #444;display:flex;gap:10px;align-items:center;
  flex-wrap:wrap;z-index:10;}
.hdr h2{margin:0;color:#9cdcfe;font-size:0.95rem;flex:1;}
.btn{padding:7px 16px;background:#0e639c;color:white;border:none;border-radius:4px;
  font-size:0.85rem;font-weight:600;cursor:pointer;text-decoration:none;white-space:nowrap;}
.btn:hover{background:#1177bb;}
.btn-green{background:#1e6b1e;}.btn-green:hover{background:#287328;}
.status{padding:6px 12px;border-radius:4px;font-size:0.82rem;background:#1a2d1a;
  color:#4ec9b0;display:none;}
.status.show{display:block;}
textarea{width:100%;height:calc(100vh - 70px);background:#111;color:#d4d4d4;
  border:none;padding:16px;font-size:0.78rem;font-family:monospace;
  line-height:1.55;resize:none;outline:none;}
</style></head><body>
<div class="hdr">
  <h2>전체 API 덤프 — {{ hpid }} ({{ name }})</h2>
  <button class="btn btn-green" onclick="copyAll()">전체 복사</button>
  <a class="btn" href="/debug/full">다른 병원</a>
  <a class="btn" href="/">홈</a>
  <span class="status" id="copyStatus">클립보드에 복사됨</span>
</div>
<textarea id="out" readonly>{{ text }}</textarea>
<script>function copyAll() {
  const ta = document.getElementById('out');
  ta.select();
  const st = document.getElementById('copyStatus');
  try {
    navigator.clipboard.writeText(ta.value)
      .then(() => { st.classList.add('show'); setTimeout(()=>st.classList.remove('show'),2500); })
      .catch(() => { document.execCommand('copy'); st.classList.add('show'); setTimeout(()=>st.classList.remove('show'),2500); });
  } catch(e) {
    document.execCommand('copy');
    st.classList.add('show'); setTimeout(()=>st.classList.remove('show'),2500);
  }
}
</script>
</body></html>""", hpid=hpid, name=_html.escape(name_from_basic), text=full_text)

_write_crash('[2] All Flask routes registered OK')

def _open_browser_android(url: str, delay: float = 2.0):
    import time, subprocess
    time.sleep(delay)
    try:
        r = subprocess.run(['am','start','-a','android.intent.action.VIEW','-d',url],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0: print(f"[AUTO] am start 성공: {url}"); return
        else: print(f"[AUTO] am start 실패 (rc={r.returncode}): {r.stderr.strip()}")
    except Exception as e: print(f"[AUTO] am start 예외: {e}")
    try:
        from jnius import autoclass
        Intent = autoclass('android.content.Intent'); Uri = autoclass('android.net.Uri')
        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        PythonActivity.mActivity.startActivity(intent)
        print(f"[AUTO] jnius 브라우저 열기: {url}"); return
    except Exception as e: print(f"[AUTO] jnius 방법 실패: {e}")
    try:
        import webbrowser; webbrowser.open(url); print(f"[AUTO] webbrowser.open: {url}")
    except Exception as e: print(f"[AUTO] webbrowser 실패: {e}"); print(f"[AUTO] 수동 접속: {url}")


# ═══════════════════════════════════════════════════════════════════
#  Android / PC 진입점
# ═══════════════════════════════════════════════════════════════════

_IS_ANDROID = hasattr(sys, 'getandroidapilevel')

#  [일원화 2026-H1] emergency_crash.log 폐지 → ermon.log 단일화
def _early_write(msg):
    return _ulog('BOOT', msg)

if _IS_ANDROID:
    _early_write(f'[STEP0] main.py module loaded OK, __name__={__name__}')



def _open_browser_android(url: str, delay: float = 2.0):
    import time, subprocess
    time.sleep(delay)
    try:
        r = subprocess.run(['am','start','-a','android.intent.action.VIEW','-d',url],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0: print(f"[AUTO] am start 성공: {url}"); return
        else: print(f"[AUTO] am start 실패 (rc={r.returncode}): {r.stderr.strip()}")
    except Exception as e: print(f"[AUTO] am start 예외: {e}")
    try:
        from jnius import autoclass
        Intent = autoclass('android.content.Intent'); Uri = autoclass('android.net.Uri')
        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        PythonActivity.mActivity.startActivity(intent)
        print(f"[AUTO] jnius 브라우저 열기: {url}"); return
    except Exception as e: print(f"[AUTO] jnius 방법 실패: {e}")
    try:
        import webbrowser; webbrowser.open(url); print(f"[AUTO] webbrowser.open: {url}")
    except Exception as e: print(f"[AUTO] webbrowser 실패: {e}")


# ═══════════════════════════════════════════════════════════════════
#  Android / PC 진입점
# ═══════════════════════════════════════════════════════════════════

_IS_ANDROID = hasattr(sys, 'getandroidapilevel')

#  [일원화 2026-H1] emergency_crash.log 폐지 → ermon.log 단일화
def _early_write(msg):
    return _ulog('BOOT', msg)

if _IS_ANDROID:
    _early_write(f'[STEP0] main.py module loaded OK, __name__={__name__}')


if _IS_ANDROID:

    _TARGET_URL = 'http://localhost:5000'

    _write_crash('[3] Importing kivy...')
    try:
        from kivy.app import App
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.uix.button import Button
        from kivy.uix.scrollview import ScrollView
        from kivy.uix.progressbar import ProgressBar
        from kivy.core.window import Window
        from kivy.core.text import LabelBase
        from kivy.clock import Clock
        _write_crash('[3] All kivy imports OK')
    except Exception as _e:
        _write_crash(f'[3] kivy imports FAILED: {_e}')
        raise

    # ─── 병원명 PiP용 축약 ────────────────────────────────────────
    def _pip_shortname(name, max_chars=13):
        """PiP 표시용 병원명 — 법인명·대학교명 접미사 제거 후 최대 max_chars 글자 제한.
        shorten=True + markup 조합의 Kivy 렌더링 깨짐을 Python 단에서 방지."""
        import re as _re
        # 법인명·기관명 접미사 제거 (앞·뒤 위치 모두)
        name = _re.sub(
            r'\s*(의료법인|학교법인|재단법인|사회복지법인|의료재단|학교법인)\s*', '', name)
        name = name.strip()
        if len(name) >max_chars:
            name = name[:max_chars] + '…'
        return name

    # ─── PiP 선호 저장/복원 ─────────────────────────────────────
    #  [일원화 2026-H1] pip_prefs.json / emergency_state.json 폐지
    #   → ermon_state.json 의 'pip_prefs' · 'pip_state' 섹션
    def _load_pip_prefs():
        try:
            return _state_get('pip_prefs', {'aspect_w': 16, 'aspect_h': 9})
        except Exception:
            return {'aspect_w': 16, 'aspect_h': 9}  # 기본 가로화면

    def _save_pip_prefs(prefs):
        try:
            _state_set('pip_prefs', prefs)
        except Exception as _e:
            _dlog(f'[PiP] prefs 저장 실패: {_e}')


    class EmergencyApp(App):
        _pip_refresh_ev = None
        _pip_timer_ev   = None
        _h_param        = ''
        _iv_sec         = 180
        _last_fetch_ts  = 0.0
        _pip_prefs      = None
        _loading_step   = 0
        _pip_base_sp    = 13    # 자동조절 기준 폰트 (병상텍스트)
        _pip_bar_sp     = 9     # 자동조절 막대 폰트 (갱신막대와 공유)
        _last_log_len   = 0     # 로그 변경 감지 (불필요한 갱신 방지)
        _last_pip_data  = None  # 마지막 PiP 데이터 캐시 (resize 시 재빌드용)

        # ── 앱 시작 ─────────────────────────────────────────────
        @staticmethod
        def _purge_stale_notifications():
            """이전 프로세스가 남긴 알림 제거 (좀비 방지)."""
            if not _IS_ANDROID:
                return
            try:
                from jnius import autoclass
                PA  = autoclass('org.kivy.android.PythonActivity')
                ctx = PA.mActivity.getApplicationContext()
                nm  = PA.mActivity.getSystemService(ctx.NOTIFICATION_SERVICE)
                nm.cancelAll()
                _dlog('[Notify] 기동 시 잔존 알림 전체 제거')
            except Exception as _pe:
                _dlog(f'[Notify] 잔존 알림 제거 실패 (무시): {_pe}')

        def on_start(self):
            EmergencyApp._purge_stale_notifications()
            _early_write('[STEP2] on_start()')
            _dlog('[Lifecycle] on_start')
            try:
                self._pip_busy  = False   #  FIX(2026-C3): _enter_pip_mode 중복호출 방지 플래그
                self._exiting   = False
                self._torn_down = False
                self._pip_prefs = _load_pip_prefs()
                self._setup_logging()
                self._log_selftest()
                _dlog('[Lifecycle] 로깅 설정 완료')
                #  ROOT-FIX 2026-E1: /api/app_exit 가 호출되면 동일 정리 수행
                try:
                    _register_exit_hook(lambda _r: self._teardown(_r))
                    _dlog('[Lifecycle] 종료훅 등록 완료')
                except Exception as _he:
                    _dlog(f'[Lifecycle] 종료훅 등록 실패: {_he}')
                self._start_flask()
                _dlog('[Lifecycle] Flask 스레드 시작')
                self._schedule_browser()
                _dlog('[Lifecycle] 브라우저 오픈 예약 (3s)')
                Clock.schedule_interval(self._check_pip_request, 0.5)
                Clock.schedule_once(lambda dt: self._setup_pip_auto_enter(), 2)
                # 로딩 프로그레스 시뮬레이션
                Clock.schedule_interval(self._advance_loading, 0.4)
                # Flask watchdog — 15초마다 ping, 실패시 로그
                Clock.schedule_interval(self._watchdog_flask, 15)
                _dlog('[Lifecycle] watchdog 등록')
                # WakeLock 획득 (프로세스 kill 방지)
                Clock.schedule_once(lambda dt: self._acquire_wakelock(), 2)
                # 포커스 없이도 kill 방지 (배터리 최적화 제외 + 영구 알림)
                # — 12초 지연: /autostart 브라우저 오픈 완료 후 다이얼로그 표시
                Clock.schedule_once(lambda dt: self._prevent_kill(), 12)
            except Exception as _e:
                _dlog(f'[Lifecycle] on_start 오류: {_e}')
                _early_write(f'[STEP2] on_start ERROR: {_e}')


        def _acquire_wakelock(self):
            """PARTIAL_WAKE_LOCK 획득 — PiP 중 CPU sleep 방지"""
            if not _IS_ANDROID:
                return
            try:
                from jnius import autoclass
                PA = autoclass('org.kivy.android.PythonActivity')
                activity = PA.mActivity
                Context  = autoclass('android.content.Context')
                PM       = autoclass('android.os.PowerManager')
                pm = activity.getSystemService(Context.POWER_SERVICE)
                self._wakelock = pm.newWakeLock(
                    PM.PARTIAL_WAKE_LOCK, 'EmergencyMonitor::PipLock')
                if not self._wakelock.isHeld():
                    self._wakelock.acquire()
                _dlog('[WakeLock] PARTIAL_WAKE_LOCK 획득 완료')
            except Exception as _wle:
                _dlog(f'[WakeLock] 획득 실패 (무시): {_wle}')

        def _prevent_kill(self):
            """포커스 없이도 kill 방지:
            1) 배터리 최적화 제외 요청
            2) 영구 알림 등록 (OS가 앱을 중요 프로세스로 인식)
            3) 주기적 keep-alive ping으로 프로세스 활성 유지
            buildozer.spec에 다음 권한 필요:
              android.permissions = ..., REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                                        FOREGROUND_SERVICE, RECEIVE_BOOT_COMPLETED
            """
            if not _IS_ANDROID:
                return
            try:
                from jnius import autoclass
                PA = autoclass('org.kivy.android.PythonActivity')
                activity = PA.mActivity
                ctx = activity.getApplicationContext()
                pkg = ctx.getPackageName()

                # ① 배터리 최적화 제외 요청
                try:
                    PM2  = autoclass('android.os.PowerManager')
                    pm2  = activity.getSystemService(ctx.POWER_SERVICE)
                    if not pm2.isIgnoringBatteryOptimizations(pkg):
                        Intent   = autoclass('android.content.Intent')
                        Settings = autoclass('android.provider.Settings')
                        Uri      = autoclass('android.net.Uri')
                        intent   = Intent(
                            Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
                        intent.setData(Uri.parse(f'package:{pkg}'))
                        activity.startActivity(intent)
                        _dlog('[Kill방지] 배터리 최적화 제외 다이얼로그 표시')
                    else:
                        _dlog('[Kill방지] 배터리 최적화 이미 제외됨')
                except Exception as _be:
                    _dlog(f'[Kill방지] 배터리 최적화 요청 실패 (무시): {_be}')

                # ② 영구(Ongoing) 알림 등록 — OS가 이 프로세스를 중요하게 인식
                try:
                    NM = autoclass('android.app.NotificationManager')
                    nm = activity.getSystemService(ctx.NOTIFICATION_SERVICE)
                    # 알림 채널 (API 26+)
                    try:
                        NC = autoclass('android.app.NotificationChannel')
                        ch = NC('em_bg_ch', '응급실 모니터', NM.IMPORTANCE_MIN)
                        ch.setShowBadge(False)
                        ch.setSound(None, None)
                        nm.createNotificationChannel(ch)
                    except Exception:
                        pass
                    NB = autoclass('android.app.Notification$Builder')
                    nb = NB(ctx, 'em_bg_ch')
                    nb.setSmallIcon(ctx.getApplicationInfo().icon)
                    nb.setContentTitle('응급의료 모니터')
                    nb.setContentText('백그라운드 갱신 실행 중 — 탭하여 열기')
                    #  FIX(2026-C2): Pydroid3 대응 — 테스트 환경은 Ongoing=False
                    # APK(정식빌드)에서만 영구 알림으로 동작시켜
                    # Pydroid3 종료 후 알림이 남는 문제를 방지한다.
                    _is_apk = not any('pydroid' in _p.lower() for _p in sys.path)
                    nb.setOngoing(False)
                    nb.setPriority(-2)   # PRIORITY_MIN
                    nb.setCategory('service')
                    #  좀비 알림 차단 (사용자 결정: 백그라운드 유지 포기)
                    #   최근앱 스와이프로 태스크가 제거되면 프로세스가 죽어
                    #   on_stop/atexit 가 실행되지 않는다. 그때 알림만 남는
                    #   문제를 원천 차단하기 위해 상시 알림을 발행하지 않고,
                    #   기존에 남아 있을 수 있는 알림을 제거만 한다.
                    _ = _is_apk
                    for _nid in (9001, 9002):
                        try:
                            nm.cancel(_nid)
                        except Exception:
                            pass
                    _dlog('[Kill방지] 상시 알림 미발행 + 잔존 알림 정리 완료')

                    # atexit + SIGTERM/SIGINT 핸들러: on_stop() 미호출 시에도 알림 제거
                    import atexit as _atexit, signal as _signal

                    def _cancel_notification_safe():
                        try:
                            from jnius import autoclass as _acn
                            _PA2  = _acn('org.kivy.android.PythonActivity')
                            _ctx2 = _PA2.mActivity.getApplicationContext()
                            _nm2  = _PA2.mActivity.getSystemService(
                                _ctx2.NOTIFICATION_SERVICE)
                            _nm2.cancel(9001)
                            _nm2.cancelAll()
                            _dlog('[Kill방지] atexit 알림 제거 완료')
                        except Exception:
                            pass

                    _atexit.register(_cancel_notification_safe)

                    for _sig in (_signal.SIGTERM, _signal.SIGINT):
                        try:
                            def _sig_handler(_sn, _fr,
                                             _fn=_cancel_notification_safe):
                                _fn()
                                _signal.signal(_sn, _signal.SIG_DFL)
                                raise SystemExit(0)
                            _signal.signal(_sig, _sig_handler)
                        except Exception:
                            pass

                except Exception as _ne:
                    _dlog(f'[Kill방지] 알림 등록 실패 (무시): {_ne}')

                # ③ Keep-alive: 주기적 Flask self-ping (120초마다)
                Clock.schedule_interval(self._keepalive_ping, 120)
                _dlog('[Kill방지] keep-alive ping 등록 (120초 주기)')

            except Exception as _ke:
                _dlog(f'[Kill방지] 전체 실패 (무시): {_ke}')

        def _keepalive_ping(self, dt):
            """Flask 자기 ping — 프로세스 활성 상태 유지"""
            def _ping():
                try:
                    import urllib.request
                    with urllib.request.urlopen(
                            'http://127.0.0.1:5000/', timeout=3) as r:
                        _ = r.read(64)
                except Exception:
                    pass
            threading.Thread(target=_ping, daemon=True,
                             name='KeepAlivePing').start()

        def _try_restore_state(self):
            """PiP 갱신 타이머만 복원 (브라우저 오픈은 /autostart 라우트가 담당)."""
            if self._h_param:
                _dlog('[State] h_param 이미 세팅됨 — PiP 복원 스킵')
                return
            if self._restore_state():
                _dlog(f'[State] PiP 복원: h={self._h_param[:40]}')
                self._start_pip_refresh()

        def _watchdog_flask(self, dt):
            """Flask 서버 생존 확인 — 응답 없으면 재시작 + pip fetch 재개"""
            def _ping():
                if getattr(self, '_exiting', False):
                    return
                ok = False
                try:
                    import urllib.request
                    with urllib.request.urlopen('http://127.0.0.1:5000/', timeout=3) as r:
                        ok = (r.status == 200)
                except Exception as _we:
                    _dlog(f'[Watchdog] Flask 무응답: {_we}')

                #  ROOT-FIX 2026-E1: 200 이 와도 "내 프로세스"가 아닐 수 있다.
                #   구 인스턴스가 포트를 물고 있으면 데이터가 통째로 낡는다.
                if ok:
                    who = EmergencyApp._peer_whoami(5000, timeout=2.0)
                    peer = (who or {}).get('pid')
                    if peer is not None and peer != _APP_PID:
                        _dlog(f'[Watchdog] 외부 인스턴스 응답 pid={peer} '
                              f'(내 pid={_APP_PID}) → 인수 후 재기동')
                        ok = False
                        try:
                            self._takeover_port(5000)
                        except Exception as _te:
                            _dlog(f'[Watchdog] 인수 실패: {_te}')

                if not ok:
                    need_restart = True
                    try:
                        if hasattr(self, '_flask_thread') and self._flask_thread.is_alive():
                            # 스레드는 살아있지만 응답 없음 — 새 포트 충돌 가능
                            need_restart = False
                            _dlog('[Watchdog] Flask 스레드 살아있음 — 재시작 스킵')
                    except Exception:
                        pass

                    if need_restart:
                        _dlog('[Watchdog] Flask 완전 재시작 시도')
                        Clock.schedule_once(lambda _dt: self._start_flask(), 0)
                        # 3초 후 pip fetch 재개
                        if self._h_param:
                            def _delayed_refetch(_dt2):
                                _dlog('[Watchdog] Flask 재시작 후 pip fetch 재개')
                                self._start_pip_refresh()
                            Clock.schedule_once(_delayed_refetch, 4)
            threading.Thread(target=_ping, daemon=True, name='Watchdog').start()

        def _advance_loading(self, dt):
            """서버 준비 중 로딩 바 애니메이션"""
            self._loading_step += 1
            try:
                if hasattr(self, '_progress_bar'):
                    self._progress_bar.value = min(self._loading_step * 11, 90)
            except Exception:
                pass
            if self._loading_step >= 8:
                return False  # Clock 정지

        # ── 라이프사이클 ─────────────────────────────────────────
        # ══════════════════════════════════════════════════════════
        #  [ROOT-FIX 2026-E2] 완전종료 경로
        #  근본원인: on_pause() 가 항상 True + PARTIAL_WAKE_LOCK 유지 →
        #  안드로이드가 프로세스를 죽이지 않는다. 그런데 종료 경로가
        #  전혀 없어서 (a) PiP 창이 좀비로 남고 (b) 구 프로세스가 5000
        #  포트를 물고 있어 재실행 시 신규 Flask 가 bind 실패한다.
        #  → _teardown(정리) / _full_exit(정리+프로세스 종료) 를 신설하고
        #    on_stop · [종료] 버튼 · /api/app_exit 세 경로에서 공유한다.
        # ══════════════════════════════════════════════════════════
        def _teardown(self, reason='user'):
            """프로세스 종료 직전 정리. Flask /api/app_exit 훅에서도 호출됨."""
            if getattr(self, '_torn_down', False):
                return
            self._torn_down = True
            self._exiting = True
            _dlog(f'[EXIT] teardown 시작 reason={reason}')

            # ① PiP 자동진입 해제 — 다음 기동에서 좀비 PiP 재현 차단
            try:
                if _IS_ANDROID and EmergencyApp._get_real_api_level() >= 31:
                    from jnius import autoclass
                    PA   = autoclass('org.kivy.android.PythonActivity')
                    PIPB = autoclass('android.app.PictureInPictureParams$Builder')
                    PA.mActivity.setPictureInPictureParams(
                        PIPB().setAutoEnterEnabled(False).build())
                    _dlog('[EXIT] setAutoEnterEnabled(False)')
            except Exception as _pe:
                _dlog(f'[EXIT] autoEnter 해제 실패(무시): {_pe}')

            # ② 모든 주기 타이머 정지
            for _nm in ('_pip_refresh_ev', '_pip_timer_ev'):
                try:
                    _ev = getattr(self, _nm, None)
                    if _ev:
                        _ev.cancel()
                    setattr(self, _nm, None)
                except Exception:
                    pass
            for _fn in (getattr(self, '_do_pip_fetch', None),
                        getattr(self, '_keepalive_ping', None),
                        getattr(self, '_watchdog_flask', None),
                        getattr(self, '_check_pip_request', None),
                        getattr(self, '_poll', None),
                        getattr(self, '_tick_timer', None),
                        getattr(self, '_update_debug_panel', None)):
                try:
                    if _fn:
                        Clock.unschedule(_fn)
                except Exception:
                    pass
            _dlog('[EXIT] 타이머 정지 완료')

            # ③ 병상 알림 모니터 / 오버레이 정리
            try:
                _stop_bed_notify(True)
            except Exception as _be:
                _dlog(f'[EXIT] 알림모니터 정지 실패(무시): {_be}')
            try:
                _clear_monitor_cfg()
            except Exception:
                pass
            try:
                _overlay_remove()
            except Exception:
                pass

            # ④ 상태바 알림 전체 제거
            if _IS_ANDROID:
                try:
                    from jnius import autoclass
                    PA  = autoclass('org.kivy.android.PythonActivity')
                    ctx = PA.mActivity.getApplicationContext()
                    nm  = PA.mActivity.getSystemService(ctx.NOTIFICATION_SERVICE)
                    nm.cancel(9001)
                    nm.cancel(9002)
                    nm.cancelAll()
                    _dlog('[EXIT] 알림 제거 완료')
                except Exception as _se:
                    _dlog(f'[EXIT] 알림 제거 실패(무시): {_se}')

            # ⑤ WakeLock 해제
            try:
                if hasattr(self, '_wakelock') and self._wakelock.isHeld():
                    self._wakelock.release()
                    _dlog('[EXIT] WakeLock 해제')
            except Exception:
                pass

            # ⑥ 상태파일 삭제 — 재기동 시 PiP 자동복원(좀비 재현) 차단
            try:
                _state_set('pip_state', None)
                _dlog('[EXIT] PiP 상태 섹션 삭제')
            except Exception:
                pass
            self._h_param = ''
            try:
                _pip_state['pending'] = False
                _pip_state['h_param'] = ''
            except Exception:
                pass
            try:
                for _h in logging.getLogger().handlers:
                    _h.flush()
            except Exception:
                pass
            _dlog(f'[EXIT] teardown 완료 reason={reason}')

        def _full_exit(self, reason='user'):
            """[종료] 버튼 / on_stop 공용 — 정리 후 태스크 제거 + 프로세스 종료."""
            _dlog(f'[EXIT] 완전종료 요청 reason={reason}')
            try:
                self._status_lbl.text = '종료 중...'
            except Exception:
                pass
            self._teardown(reason)

            def _fin():
                if _IS_ANDROID:
                    def _kill_task():
                        try:
                            from jnius import autoclass
                            PA  = autoclass('org.kivy.android.PythonActivity')
                            act = PA.mActivity
                            try:
                                act.finishAndRemoveTask()
                                _dlog('[EXIT] finishAndRemoveTask()')
                            except Exception:
                                act.finish()
                                _dlog('[EXIT] finish()')
                        except Exception as _fe:
                            _dlog(f'[EXIT] finish 실패(무시): {_fe}')
                    try:
                        _run_on_ui(_kill_task, wait=True, timeout=3.0)
                    except Exception:
                        _kill_task()
                time.sleep(0.5)
                _dlog('[EXIT] os._exit(0)')
                try:
                    for _h in logging.getLogger().handlers:
                        _h.flush()
                except Exception:
                    pass
                os._exit(0)

            threading.Thread(target=_fin, daemon=True, name='FullExit').start()

        def on_stop(self):
            """앱 종료(활동 파괴 / PiP 창 X 버튼) → 정리 + 프로세스 실제 종료.
            기존에는 알림만 지우고 프로세스가 살아남아 5000 포트를 계속
            점유했다(재실행 시 좀비 인스턴스의 직접 원인)."""
            _dlog('[Lifecycle] on_stop — 완전종료 진행')
            self._full_exit('on_stop')

        def on_pause(self):
            """
            True 반환 → 프로세스 유지.
             개선: pending 플래그 없이도 h_param이 세팅된 상태라면
              홈/백 버튼 즉시 PiP 진입 (초기화면 통과 불필요).
            """
            _dlog('[Lifecycle] on_pause')
            logging.info('[Lifecycle] on_pause')
            #  ROOT-FIX 2026-E2: 종료 진행 중에는 PiP 진입/프로세스 유지 금지
            if getattr(self, '_exiting', False):
                _dlog('[Lifecycle] on_pause: 종료 진행 중 → PiP 생략, False 반환')
                return False
            # 현재 상태 저장 (kill 시 복원용)
            if self._h_param:
                threading.Thread(target=self._save_state, daemon=True).start()
            # PIP 갱신 interval이 살아있는지 확인하고 재등록
            if self._h_param and self._pip_refresh_ev is None:
                _dlog('[Lifecycle] on_pause: pip_refresh_ev 없음 → 재등록')
                self._start_pip_refresh()
            if _pip_state.get('pending'):
                _pip_state['pending'] = False
                self._h_param = _pip_state.get('h_param', '')
                self._iv_sec  = _pip_state.get('iv_sec', 180)
                _dlog(f'[PiP] on_pause pending 처리: h={self._h_param[:50]}')
                self._start_pip_refresh()
                self._enter_pip_mode()
            elif self._h_param and (self._last_good_pip_data or {}).get('hospitals'):
                # 이전에 선택된 병원 + 표시할 데이터 있음 → 즉시 PiP
                _dlog('[PiP] on_pause: h_param + 데이터 보존 → 즉시 PiP 진입')
                self._enter_pip_mode()
            else:
                _dlog('[PiP] on_pause: 표시할 데이터 없음 → PiP 진입 생략(빈 창 방지)')
            return True  # 절대 kill하지 않음

        def on_resume(self):
            """포그라운드 복귀 시 pending 확인"""
            _ulog('PIP', 'on_resume — busy 플래그 해제')
            self._pip_busy = False        # [FIX 2026-H2] 좀비 잠김 방지
            _dlog('[Lifecycle] on_resume')
            _dlog(f'[Lifecycle] on_resume _pip_state={_pip_state}')
            if getattr(self, '_exiting', False):
                _dlog('[Lifecycle] on_resume: 종료 진행 중 → 무시')
                return
            #  ROOT-FIX 2026-E2: PiP 에서 빠져나온 시점에 autoEnter 를
            #   현재 데이터 보유 상태로 다시 맞춘다(기존엔 기동 2초 1회뿐).
            Clock.schedule_once(lambda _dt: self._setup_pip_auto_enter(), 0.3)
            if _pip_state.get('pending'):
                _pip_state['pending'] = False
                self._h_param = _pip_state.get('h_param', '')
                self._iv_sec  = _pip_state.get('iv_sec', 180)
                _dlog(f'[PiP] on_resume pending: h={self._h_param[:50]}')
                #  FIX(2025): 타이머는 유지하고 즉시 한 번만 fetch
                # _start_pip_refresh()는 타이머 cancel+재등록 → Sync/on_pause와
                # 중복 호출 시 3중 fetch thread → apis.data.go.kr 폭주 → HTTP 500.
                # 타이머가 없을 때만 등록, 있을 때는 _do_pip_fetch 단독 실행.
                if self._pip_refresh_ev is None:
                    self._start_pip_refresh()
                else:
                    Clock.schedule_once(lambda dt: self._do_pip_fetch(0), 0)
                #  즉시 PiP (이슈3: 백그라운드 버튼 → 즉시 최소화)
                Clock.schedule_once(lambda dt: self._enter_pip_mode(), 0.1)
            else:
                _dlog('[Lifecycle] on_resume: pending 없음')
                #  FIX(2025-B3): pending 없이도 last_pip_data가 있으면 재렌더링
                # moveTaskToBack 도중 clear_widgets()가 실행된 상태로 백그라운드에
                # 진입했을 경우, 복귀 시 빈 컨테이너가 표시될 수 있음.
                # on_resume 시 마지막 성공 데이터로 즉시 화면을 복원한다.
                if self._last_pip_data:
                    Clock.schedule_once(
                        lambda dt: self._update_pip_ui(self._last_pip_data), 0.15)

        # ── UI 빌드 ─────────────────────────────────────────────
        def build(self):
            _early_write('[STEP3] build()')
            try:
                Window.clearcolor = (0.05, 0.05, 0.10, 1)
                Window.bind(on_resize=self._on_window_resize)

                # ── 한국어 폰트 등록 ─────────────────────────────────
                # Roboto(Kivy 기본값) 이름으로 등록 → 모든 Label에 자동 적용
                # 번들 TTF → 앱 내부 → 시스템 폰트 순서로 탐색
                _FONT_CANDIDATES = [
                    # 앱 번들 (buildozer로 패키징한 폰트)
                    os.path.join(os.path.dirname(__file__), 'NanumGothic.ttf'),
                    os.path.join(os.path.dirname(__file__), 'fonts', 'NanumGothic.ttf'),
                    # Pydroid3 / 일반 Android 위치
                    '/data/user/0/ru.iiec.pydroid3/files/arm-linux-androideabi/lib/python3.11/site-packages/kivy/data/fonts/NotoSansCJK.ttf',
                    # Android 시스템 폰트
                    '/system/fonts/NotoSansCJK-Regular.ttc',
                    '/system/fonts/NotoSansCJKkr-Regular.otf',
                    '/system/fonts/NotoSansCJKsc-Regular.otf',
                    '/system/fonts/NotoSerifCJK-Regular.ttc',
                    '/system/fonts/DroidSansFallback.ttf',
                    '/system/fonts/DroidSans.ttf',
                    '/system/fonts/Roboto-Regular.ttf',
                ]
                _font_ok = False
                for _fp in _FONT_CANDIDATES:
                    if os.path.exists(_fp):
                        try:
                            # 'Roboto'로 등록 = Kivy 기본 폰트 교체 → 한글 전체 적용
                            LabelBase.register(name='Roboto', fn_regular=_fp)
                            _dlog(f'[Font] Roboto 등록 OK: {_fp}')
                            _font_ok = True
                            break
                        except Exception as _fe:
                            _dlog(f'[Font] 등록 실패: {_fp}: {_fe}')
                if not _font_ok:
                    _dlog('[Font] 한글 폰트 없음 — 기본 폰트 사용 (한글 깨짐 가능)')

                # ═══════════════════════════════════════════════════
                # 루트 레이아웃
                # [헤더] [PIP데이터·확장] [타이머텍스트] [타이머막대]
                # [디버그로그 1/3] [버튼행·맨아래 고정]
                # ═══════════════════════════════════════════════════
                root = BoxLayout(orientation='vertical',
                                 padding=[4, 4, 4, 2], spacing=2)

                # ── 헤더 행 ──────────────────────────────────
                hdr = BoxLayout(orientation='horizontal',
                                size_hint_y=None, height=26, spacing=4)
                _hdr_title = Label(
                    text='EMERGENCY MONITOR',
                    color=(0.55, 0.32, 0.95, 1),
                    font_size='12sp', bold=True,
                    size_hint_x=0.60,
                    halign='left', valign='middle')
                _hdr_title.bind(size=_hdr_title.setter('text_size'))
                hdr.add_widget(_hdr_title)

                self._status_lbl = Label(
                    text='시작 중...',
                    color=(0.7, 0.5, 0.1, 1),
                    font_size='9sp',
                    size_hint_x=0.40,
                    halign='right', valign='middle')
                self._status_lbl.bind(size=self._status_lbl.setter('text_size'))
                hdr.add_widget(self._status_lbl)
                root.add_widget(hdr)

                # ── 로딩 프로그레스 바 ────────────────────────
                self._progress_bar = ProgressBar(
                    max=100, value=0,
                    size_hint_y=None, height=3)
                root.add_widget(self._progress_bar)

                # ── 구분선 ────────────────────────────────────
                root.add_widget(Label(
                    text='- PiP MONITOR -',
                    color=(0.30, 0.52, 1, 1),
                    font_size='8sp', bold=True,
                    size_hint_y=None, height=12))

                # ── PiP 데이터 메인 영역 — 병원별 행: 이름(좌) + 병상(우) ─
                from kivy.uix.gridlayout import GridLayout
                pip_sv = ScrollView(size_hint=(1, 1))
                self._pip_container = BoxLayout(
                    orientation='vertical', size_hint_y=None, spacing=2, padding=[2,0,2,0])
                self._pip_container.bind(
                    minimum_height=self._pip_container.setter('height'))
                # 안내 레이블 (데이터 없을 때 표시)
                self._pip_data_lbl = Label(
                    text=(
                        '[color=#555555]병원 선택 후\n'
                        '[b]백그라운드[/b] 버튼을\n'
                        '누르면 PiP 시작[/color]'
                    ),
                    color=(0.88, 0.88, 0.88, 1),
                    font_size='18sp',
                    size_hint_y=None,
                    halign='left', valign='top',
                    markup=True,
                    padding=(4, 2))
                self._pip_data_lbl.bind(
                    texture_size=self._pip_data_lbl.setter('size'))
                self._pip_container.add_widget(self._pip_data_lbl)
                pip_sv.add_widget(self._pip_container)
                root.add_widget(pip_sv)

                # ── 카운트다운: [갱신 X:XX] [████████████████] 한 행 ──
                # 텍스트(고정폭 좌) + 막대(나머지 전체폭) 수평 레이아웃
                from kivy.uix.boxlayout import BoxLayout as _BL2
                #  [FIX 2026-G3] 진행막대가 최하단 병원행을 가려서:
                #   행 높이를 1/2 로 줄인다. 세로 BoxLayout 에서 이 행의
                #   아래변은 고정이므로, 줄어든 만큼(= 새 막대 두께만큼)
                #   막대 윗변이 아래로 내려가고 그 공간은 병원목록이 회수한다.
                _timer_row = _BL2(orientation='horizontal',
                                  size_hint_y=None, height=11, spacing=2)
                self._timer_row = _timer_row  # resize 핸들러에서 참조
                self._timer_lbl = Label(
                    text='',
                    color=(0.55, 0.85, 0.55, 1),
                    font_size='11sp',
                    markup=True,
                    size_hint_x=None, width=56,
                    halign='left', valign='middle')
                self._timer_lbl.bind(size=self._timer_lbl.setter('text_size'))
                # 막대: Canvas 기반 비율 바 (픽셀 완벽, 길이 일정)
                from kivy.uix.widget import Widget as _TimerBarW
                from kivy.graphics import Color as _TC, Rectangle as _TR
                self._timer_bar_widget = _TimerBarW(size_hint_x=1)
                self._timer_bar_ratio = [0.0]  # [remaining_ratio]
                with self._timer_bar_widget.canvas:
                    self._tbg_rem = _TC(1.0, 1.0, 1.0, 1)      # 남은시간 흰색
                    self._tb_rem  = _TR(pos=(0,0), size=(0,11))
                    self._tbg_ela = _TC(0.22, 0.22, 0.22, 1)   # 경과 어두운 회색
                    self._tb_ela  = _TR(pos=(0,0), size=(0,11))
                def _upd_timer_bar(w, *a,
                                   _rb=self._tb_rem, _re=self._tb_ela,
                                   _ratio=self._timer_bar_ratio):
                    bw = int(w.width * _ratio[0])
                    _rb.pos  = (w.x, w.y); _rb.size = (bw, w.height)
                    _re.pos  = (w.x + bw, w.y); _re.size = (w.width - bw, w.height)
                self._timer_bar_widget.bind(pos=_upd_timer_bar, size=_upd_timer_bar)
                self._upd_timer_bar_fn = _upd_timer_bar
                # 호환성 유지 (기존 코드 참조 방지용 더미)
                self._timer_bar_lbl = Label(text='', size_hint_x=None, width=0, opacity=0)
                _timer_row.add_widget(self._timer_lbl)
                _timer_row.add_widget(self._timer_bar_widget)
                _timer_row.add_widget(self._timer_bar_lbl)
                root.add_widget(_timer_row)

                # ═══════════════════════════════════════════════════
                # [하단 1/3] 디버그 로그 패널 (디폴트 폰트, 폰트 확대 금지)
                # ═══════════════════════════════════════════════════
                from kivy.uix.textinput import TextInput
                from kivy.uix.label import Label as KLabel

                log_area = BoxLayout(orientation='vertical',
                                     size_hint_y=0.33, spacing=1)
                self._log_area = log_area  # resize 핸들러에서 참조

                log_hdr_row = BoxLayout(orientation='horizontal',
                                        size_hint_y=None, height=14)
                self._log_path_lbl = KLabel(
                    text='[debug log]',
                    color=(0.35, 0.35, 0.35, 1),
                    font_size='7.5sp',
                    halign='left', valign='middle',
                    size_hint_x=1)
                self._log_path_lbl.bind(size=self._log_path_lbl.setter('text_size'))
                log_hdr_row.add_widget(self._log_path_lbl)
                log_area.add_widget(log_hdr_row)

                self._debug_ti = TextInput(
                    text='',
                    readonly=True,
                    background_color=(0.06, 0.06, 0.09, 1),
                    foreground_color=(0.60, 0.82, 0.60, 1),
                    font_size='7.5sp',
                    size_hint=(1, 1),
                    multiline=True)
                log_area.add_widget(self._debug_ti)
                root.add_widget(log_area)

                # ═══════════════════════════════════════════════════
                # [맨 아래 고정] 버튼 행 — PiP / 가로↔세로 / 로그복사 / 브라우저
                # ═══════════════════════════════════════════════════
                btn_row = BoxLayout(orientation='horizontal',
                                    size_hint_y=None, height=40, spacing=3)
                self._btn_row = btn_row  # resize 핸들러에서 참조

                pip_btn = Button(
                    text='[b]PiP[/b]',
                    markup=True,
                    background_color=(0.38, 0.12, 0.68, 1),
                    font_size='12sp')
                pip_btn.bind(on_press=lambda _btn: Clock.schedule_once(
                    lambda dt: self._enter_pip_mode(), 0))
                btn_row.add_widget(pip_btn)

                refr_btn = Button(
                    text='즉시갱신',
                    background_color=(0.10, 0.45, 0.22, 1),
                    font_size='10sp')
                refr_btn.bind(on_press=lambda _btn: Clock.schedule_once(
                    lambda dt: self._do_pip_fetch(0), 0))
                btn_row.add_widget(refr_btn)

                self._orient_btn = Button(
                    text='가로↔세로',
                    background_color=(0.25, 0.45, 0.65, 1),
                    font_size='10sp')
                self._orient_btn.bind(on_press=lambda _btn: self._toggle_orientation())
                btn_row.add_widget(self._orient_btn)

                copy_btn = Button(
                    text='로그복사',
                    background_color=(0.10, 0.35, 0.18, 1),
                    font_size='10sp')
                copy_btn.bind(on_press=lambda _btn: Clock.schedule_once(
                    lambda dt: self._copy_debug_to_clipboard(), 0))
                btn_row.add_widget(copy_btn)

                browser_btn = Button(
                    text='브라우저',
                    background_color=(0.18, 0.38, 0.75, 1),
                    font_size='10sp')
                #  FIX: 고정 URL(_TARGET_URL) 대신 최근 비교화면(/last)으로 복귀
                #   → PiP에서 돌아올 때 엉뚱한 병원이 표시되던 문제 해결
                browser_btn.bind(on_press=lambda _btn: threading.Thread(
                    target=_open_browser_android,
                    args=('http://127.0.0.1:5000/last', 0.05), daemon=True).start())
                btn_row.add_widget(browser_btn)

                #  [ROOT-FIX 2026-E2] 완전종료 버튼
                #   PiP/백그라운드를 확실히 끝내는 유일한 사용자 경로.
                exit_btn = Button(
                    text='종료',
                    background_color=(0.55, 0.10, 0.10, 1),
                    font_size='10sp')
                exit_btn.bind(on_press=lambda _btn: Clock.schedule_once(
                    lambda dt: self._full_exit('button'), 0))
                btn_row.add_widget(exit_btn)

                root.add_widget(btn_row)  # 항상 맨 아래 고정

                # 스케줄
                Clock.schedule_interval(self._poll, 3)
                Clock.schedule_once(self._poll, 2)
                Clock.schedule_interval(self._tick_timer, 1)
                Clock.schedule_interval(self._update_debug_panel, 2)

                #  이슈4: 탭/더블탭 감지 — root 전체 터치 바인딩
                self._tap_count   = 0
                self._tap_timer   = None
                self._last_tap_t  = 0.0
                root.bind(on_touch_down=self._on_root_touch)

                #  이슈4: 흔들기 감지 (Android 가속도계)
                Clock.schedule_once(lambda dt: self._setup_shake_sensor(), 3)

                _early_write('[STEP3] build() OK')
                return root
            except Exception as _e:
                _early_write(f'[STEP3] build() ERROR: {_e}')
                import traceback as _tb
                _early_write(_tb.format_exc())
                raise

        # ── 창 크기 변화 시 PiP UI 재빌드 + 레이아웃 보정 ──────────
        def _on_window_resize(self, window, width, height):
            try:
                is_landscape = (width >height * 1.2)
                _dlog(f'[Resize] {width}x{height} → {"가로" if is_landscape else "세로"} | data:{getattr(self,"_pip_base_sp","-")}sp bar:{getattr(self,"_pip_bar_sp","-")}sp')

                # 가로화면 감지: 로그패널 축소, 버튼행 확보
                if hasattr(self, '_log_area'):
                    self._log_area.size_hint_y = 0.12 if is_landscape else 0.33
                if hasattr(self, '_orient_btn'):
                    self._orient_btn.text = ('→세로' if is_landscape else '가로↔세로')
                if hasattr(self, '_btn_row'):
                    self._btn_row.height = max(34, min(50, int(height * 0.07)))

                #  갱신막대 행 높이 동적 조절 (가로/세로 전환 시 최대화)
                if hasattr(self, '_timer_row'):
                    #  [FIX 2026-G3] 기존 대비 1/2 두께 유지
                    self._timer_row.height = max(9, min(15, int(height * 0.0225)))

                #  이슈9: PiP 창 크기 조절 시 폰트/레이아웃 동적 재계산
                # Window 크기 변화 = PiP 창 크기 변화 → 즉시 재빌드
                if self._last_pip_data:
                    # 약간의 지연으로 레이아웃 안정화 후 재빌드
                    Clock.schedule_once(
                        lambda dt: self._update_pip_ui(self._last_pip_data), 0.1)

            except Exception:
                pass

        # ── 탭 / 더블탭 / 흔들기 처리 (이슈4) ──────────────────
        def _on_root_touch(self, widget, touch):
            """Kivy 전체화면일 때 탭 감지
            1탭: PiP 재진입 (상황판 보다가 다시 최소화)
            2탭 연속: 가로/세로 전환
            """
            try:
                now = time.time()
                dt  = now - self._last_tap_t
                self._last_tap_t = now

                if dt < 0.35:
                    # 더블탭 (0.35초 이내 두 번째 탭)
                    self._tap_count = 0
                    if self._tap_timer:
                        try: self._tap_timer.cancel()
                        except Exception: pass
                    _dlog('[Touch] 더블탭 → 가로/세로 전환')
                    self._toggle_orientation()
                else:
                    # 단일 탭 확인 (0.3초 대기 후 단탭 처리)
                    self._tap_count += 1
                    if self._tap_timer:
                        try: self._tap_timer.cancel()
                        except Exception: pass
                    self._tap_timer = Clock.schedule_once(
                        self._on_single_tap, 0.30)
            except Exception:
                pass
            return False  # 이벤트 전파 유지

        def _on_single_tap(self, dt):
            """단일 탭 처리: h_param이 있으면 PiP 재진입"""
            self._tap_count = 0
            if self._h_param:
                _dlog('[Touch] 단탭 → PiP 재진입')
                Clock.schedule_once(lambda _dt: self._enter_pip_mode(), 0)
            else:
                _dlog('[Touch] 단탭 (h_param 없음 — 무시)')

        def _setup_shake_sensor(self):
            """Android 가속도계 기반 흔들기 감지 설정"""
            if not _IS_ANDROID:
                return
            try:
                from jnius import autoclass
                PA      = autoclass('org.kivy.android.PythonActivity')
                Ctx     = autoclass('android.content.Context')
                SM      = autoclass('android.hardware.SensorManager')
                Sensor  = autoclass('android.hardware.Sensor')
                ctx     = PA.mActivity
                sm      = ctx.getSystemService(Ctx.SENSOR_SERVICE)
                acc_s   = sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
                if acc_s is None:
                    _dlog('[Shake] 가속도계 없음')
                    return

                # Python 구현 리스너 (jnius 인터페이스)
                # SensorEventListener → Java 인터페이스이므로 직접 구현 불가
                # → Clock 폴링 방식으로 대체: 0.1s 마다 가속도 읽기
                # plyer 없이 직접 jnius 구현은 인터페이스 어려우므로
                # plyer.accelerometer 사용 시도
                try:
                    from plyer import accelerometer as _acc
                    _acc.enable()
                    self._acc_sensor = _acc
                    self._shake_last_mag = 0.0
                    Clock.schedule_interval(self._check_shake, 0.12)
                    _dlog('[Shake] plyer 가속도계 등록 완료')
                except Exception as _pe:
                    _dlog(f'[Shake] plyer 없음 ({_pe}) — 흔들기 기능 비활성')
            except Exception as _se:
                _dlog(f'[Shake] 센서 설정 실패 (무시): {_se}')

        def _check_shake(self, dt):
            """주기적 가속도 체크 → 흔들기 감지 시 즉시 갱신"""
            try:
                val = self._acc_sensor.acceleration
                if not val or None in val:
                    return
                x, y, z = val
                import math
                mag = math.sqrt(x*x + y*y + z*z)
                delta = abs(mag - self._shake_last_mag)
                self._shake_last_mag = mag
                # 임계값: 15 m/s² (가벼운 흔들기)
                if delta >15:
                    _dlog(f'[Shake] 흔들기 감지 (Δ={delta:.1f}) → 즉시 갱신')
                    Clock.schedule_once(lambda _dt: self._do_pip_fetch(0), 0)
                    # 상황판도 함께 갱신 (pip&상황판 연동, 이슈5)
                    Clock.schedule_once(lambda _dt: self._pip_refetch_browser(), 0.1)
            except Exception:
                pass

        def _pip_refetch_browser(self):
            """브라우저 상황판 갱신 알림 (SSE/reload 트리거용)"""
            # 현재 구조에서는 브라우저가 자체 자동갱신하므로 별도 트리거 불필요
            pass

        # ── 가로/세로 전환 ───────────────────────────────────
        def _toggle_orientation(self):
            try:
                if _IS_ANDROID:
                    from jnius import autoclass
                    PM  = autoclass('android.content.pm.ActivityInfo')
                    PA  = autoclass('org.kivy.android.PythonActivity')
                    act = PA.mActivity
                    cur = act.getRequestedOrientation()
                    # 세로 계열: 1(PORTRAIT), 2(REVERSE_PORTRAIT), 5(SENSOR_PORTRAIT), 7(USER_PORTRAIT)
                    # 가로 계열: 0(LANDSCAPE), 6(SENSOR_LANDSCAPE), 8(REVERSE_LANDSCAPE)
                    # -1(UNSPECIFIED) 포함 모든 비-세로 모드에서 세로 전환
                    PORTRAIT_MODES = (1, 2, 5, 7)
                    if cur in PORTRAIT_MODES:
                        act.setRequestedOrientation(0)  # LANDSCAPE
                        _dlog('[Orient] → 가로')
                    else:
                        # 가로(0,6,8) 또는 UNSPECIFIED(-1) → 세로 전환
                        act.setRequestedOrientation(1)  # PORTRAIT
                        _dlog('[Orient] → 세로')
                else:
                    # PC: Window rotation 토글
                    Window.rotation = 90 if Window.rotation == 0 else 0
                    _dlog(f'[Orient] PC rotation={Window.rotation}')
            except Exception as e:
                _dlog(f'[Orient] 전환 실패: {e}')

        # ── 카운트다운 타이머 (1초 주기) ────────────────────
        def _tick_timer(self, dt):
            try:
                if not self._h_param or self._last_fetch_ts == 0.0:
                    self._timer_lbl.text = ''
                    self._timer_bar_lbl.text = ''
                    return
                elapsed   = time.time() - self._last_fetch_ts
                remaining = max(0.0, self._iv_sec - elapsed)
                m, s      = divmod(int(remaining), 60)

                # 병상바와 동일 굵기 (pip bar_sp 참조, 없으면 기본값)
                bar_sp = getattr(self, '_pip_bar_sp', 9)

                # 타이머 텍스트 (남은시간) — 좌측 고정폭 레이블
                #  [FIX 2026-G3] 행 높이 1/2 → 글자도 축소해야 잘리지 않는다
                self._timer_lbl.text = (
                    f'[size={max(6, bar_sp - 2)}sp][color=#888888]갱신 {m}:{s:02d}[/color][/size]'
                )

                # Canvas 타이머 바: 남은비율로 직접 업데이트 (픽셀 완벽)
                rem_ratio = (remaining / self._iv_sec) if self._iv_sec >0 else 0.0
                rem_ratio = max(0.0, min(1.0, rem_ratio))
                try:
                    self._timer_bar_ratio[0] = rem_ratio
                    self._upd_timer_bar_fn(self._timer_bar_widget)
                except Exception:
                    pass
            except Exception:
                pass

        # ── 서버 상태 폴링 ───────────────────────────────────
        def _poll(self, dt):
            try:
                if _LOG_FILE_REF[0]:
                    self._log_path_lbl.text = f'log: {_LOG_FILE_REF[0]}'
            except Exception:
                pass

        # ── 디버그 패널 업데이트 (2초 주기) ──────────────────
        def _update_debug_panel(self, dt):
            try:
                cur_len = len(_DEBUG_LINES)
                if cur_len != self._last_log_len and _DEBUG_LINES:
                    self._last_log_len = cur_len
                    lines = _DEBUG_LINES[-40:]
                    self._debug_ti.text = '\n'.join(lines)
                    # 스크롤 맨 아래 (로그가 늘어난 경우에만)
                    try:
                        self._debug_ti.cursor = (len(lines[-1]) if lines else 0, len(lines))
                    except Exception:
                        pass
            except Exception:
                pass
            import socket
            try:
                _s = socket.create_connection(('127.0.0.1', 5000), timeout=1)
                _s.close()
                self._status_lbl.text  = 'Flask OK'
                self._status_lbl.color = (0.10, 0.75, 0.20, 1)
                #  Flask 준비 완료 → 진행바 숨김 (그래픽 깨짐 방지)
                try:
                    self._progress_bar.height  = 0
                    self._progress_bar.opacity = 0
                except Exception:
                    pass
            except Exception:
                self._status_lbl.text  = '서버 대기'
                self._status_lbl.color = (0.70, 0.50, 0.10, 1)

        # ── PiP 요청 Clock 감지 (0.5s 주기) ─────────────────
        def _check_pip_request(self, dt):
            # ① PiP 진입 요청
            if _pip_state.get('pending'):
                _pip_state['pending'] = False
                self._h_param = _pip_state.get('h_param', '')
                self._iv_sec  = _pip_state.get('iv_sec', 180)
                _dlog(f'[PiP] Clock 감지: h={self._h_param[:40]}')
                self._start_pip_refresh()
                #  즉시 PiP 진입 (이슈3: 백그라운드 버튼 시 즉시 최소화)
                Clock.schedule_once(lambda dt: self._enter_pip_mode(), 0.05)

            # ② 브라우저 갱신 완료 → PiP도 즉시 갱신 (동기화)
            if _pip_state.get('fetch_pending') and self._h_param:
                _pip_state['fetch_pending'] = False
                # ── 병원 목록 변경 감지: notify_refresh가 새 h_param을 전달한 경우
                _new_h = _pip_state.get('h_param', '')
                if _new_h and _new_h != self._h_param:
                    _dlog(f'[Sync] h_param 변경 적용: {self._h_param[:30]} → {_new_h[:30]}')
                    self._h_param = _new_h
                _dlog('[Sync] 브라우저 갱신 감지 → PiP 즉시 갱신 (단독 fetch, 타이머 유지)')
                #  FIX(2025): _start_pip_refresh → _do_pip_fetch(0) 단독 호출로 변경
                # 이유: _start_pip_refresh는 타이머 cancel+재등록을 수행한다.
                #   on_resume(pending=True) → _start_pip_refresh ①
                #   fetch_pending Sync 감지 → _start_pip_refresh ② (이 라인, 구 코드)
                #   on_pause pip_refresh_ev=None → _start_pip_refresh ③
                # 세 경로가 거의 동시에 실행되면 4개 구 × 3중 스레드 = 12~16개
                # 동시 요청이 apis.data.go.kr로 향해 HTTP 500 유발.
                # 타이머는 on_resume에서 이미 등록됐으므로 여기선 fetch만 수행.
                Clock.schedule_once(lambda dt: self._do_pip_fetch(0), 0)

            # ③ 브라우저 갱신 → Kivy 햅틱+알림음
            if _haptic_pending[0]:
                _haptic_pending[0] = False
                if _IS_ANDROID:
                    def _do_hap():
                        try:
                            from jnius import autoclass
                            PA  = autoclass('org.kivy.android.PythonActivity')
                            ctx = PA.mActivity
                            try:
                                Vibrator = autoclass('android.os.Vibrator')
                                vib = ctx.getSystemService(ctx.VIBRATOR_SERVICE)
                                if vib and vib.hasVibrator():
                                    vib.vibrate(40)
                            except Exception: pass
                            try:
                                TG = autoclass('android.media.ToneGenerator')
                                AM = autoclass('android.media.AudioManager')
                                tg = TG(AM.STREAM_NOTIFICATION, 40)
                                tg.startTone(TG.TONE_PROP_BEEP, 120)
                            except Exception: pass
                        except Exception: pass
                    threading.Thread(target=_do_hap, daemon=True).start()

        # ── 실제 API 레벨 조회 ───────────────────────────────
        @staticmethod
        def _get_real_api_level():
            sys_val = 0
            try: sys_val = _sys.getandroidapilevel()
            except Exception: pass
            if sys_val >21:
                _dlog(f'[API] sys={sys_val} (신뢰)')
                return sys_val
            _dlog(f'[API] sys={sys_val} ≤ 21 → Java 조회')
            try:
                from jnius import autoclass
                BV = autoclass('android.os.Build$VERSION')
                real = BV.SDK_INT
                _dlog(f'[API] Build.VERSION.SDK_INT={real}')
                return real
            except Exception as _e:
                _dlog(f'[API] Build.VERSION 실패: {_e}')
            try:
                from jnius import autoclass
                SP = autoclass('android.os.SystemProperties')
                real = int(SP.get('ro.build.version.sdk', '0'))
                if real >0:
                    _dlog(f'[API] SystemProperties={real}')
                    return real
            except Exception as _e2:
                _dlog(f'[API] SystemProperties 실패: {_e2}')
            _dlog('[API] 전부 실패 → 99 강행')
            return 99

        def _pip_aspect_for(self, prefs=None):
            """병원 수에 맞춘 PiP 종횡비. 내용이 잘리지 않도록 세로를 늘린다.
            안드로이드 허용 범위(0.418~2.39)를 넘지 않게 클램프한다."""
            prefs = prefs or self._pip_prefs or {'aspect_w': 16, 'aspect_h': 9}
            try:
                n = len((self._last_good_pip_data or {}).get('hospitals', []))
            except Exception:
                n = 0
            if n <= 0:
                return int(prefs.get('aspect_w', 16)), int(prefs.get('aspect_h', 9))
            # 헤더 1행 + 병원 n행 → 가로 16 기준 세로 = 4 + 4.2n (경험값)
            aw = 16
            ah = int(round(4 + 4.2 * n))
            ah = max(7, min(38, ah))          # 16/38≈0.42, 16/7≈2.29 → 허용범위 내
            return aw, ah

        # ── API 31+: 자동 PiP 진입 설정 ─────────────────────
        def _setup_pip_auto_enter(self):
            if not _IS_ANDROID:
                return
            try:
                api_level = EmergencyApp._get_real_api_level()
                if api_level < 31:
                    _dlog(f'[PiP] API {api_level} < 31 → autoEnter 스킵')
                    return
                from jnius import autoclass
                PA       = autoclass('org.kivy.android.PythonActivity')
                activity = PA.mActivity
                PIPB     = autoclass('android.app.PictureInPictureParams$Builder')
                Rational = autoclass('android.util.Rational')
                prefs    = self._pip_prefs or {'aspect_w': 16, 'aspect_h': 9}
                #  좀비 PiP 차단: 표시할 데이터가 있을 때만 자동 진입 허용.
                #   빈 상태에서 홈으로 나가면 까만 PiP 창이 뜨던 문제.
                _has_data = bool(getattr(self, '_h_param', '')) and bool(
                    (getattr(self, '_last_good_pip_data', None) or {}).get('hospitals'))
                aw, ah = self._pip_aspect_for(prefs)
                params = (PIPB()
                          .setAspectRatio(Rational(aw, ah))
                          .setAutoEnterEnabled(bool(_has_data))
                          .build())
                activity.setPictureInPictureParams(params)
                _dlog(f'[PiP] setAutoEnterEnabled({bool(_has_data)}) 비율={aw}:{ah}')
            except Exception as e:
                _dlog(f'[PiP] _setup_pip_auto_enter 실패: {e}')

        # ── 디버그 로그 클립보드 복사 ────────────────────────
        # ── 상태 저장/복원 (kill 후 재복귀) ────────────────
        def _save_state(self):
            """현재 h_param, iv_sec을 파일에 저장 → 프로세스 kill 후 복원용"""
            try:
                state = {
                    'h_param': self._h_param,
                    'iv_sec':  self._iv_sec,
                    'saved_at': time.time(),
                }
                _state_set('pip_state', state)
                _dlog(f'[State] 저장: h={self._h_param[:30]} iv={self._iv_sec}s')
            except Exception as _se:
                _dlog(f'[State] 저장 실패: {_se}')

        def _restore_state(self):
            """저장된 상태를 복원. 성공 시 True 반환"""
            try:
                state = _state_get('pip_state') or {}
                h = state.get('h_param', '')
                #  좀비 PiP 차단: 30분 지난 상태는 폐기한다.
                if h and time.time() - float(state.get('saved_at', 0)) > 1800:
                    _dlog('[State] 만료된 저장 상태 → 폐기')
                    _state_set('pip_state', None)
                    h = ''
                if h:
                    self._h_param = h
                    self._iv_sec = int(state.get('iv_sec', 180))
                    _dlog(f'[State] 복원 성공: h={h[:40]} iv={self._iv_sec}s')
                    return True
            except Exception as _re2:
                _dlog(f'[State] 복원 예외: {_re2}')
            _dlog('[State] 복원할 저장 상태 없음')
            return False

        def _copy_debug_to_clipboard(self):
            _dlog('[Debug] 클립보드 복사 요청')
            try:
                full_text = (
                    f'=== 응급앱 로그 ({len(_DEBUG_LINES)}줄) ===\n'
                    + '\n'.join(_DEBUG_LINES)
                )
                if _IS_ANDROID:
                    from jnius import autoclass
                    PA  = autoclass('org.kivy.android.PythonActivity')
                    CM  = autoclass('android.content.ClipboardManager')
                    CD  = autoclass('android.content.ClipData')
                    ctx = PA.mActivity.getApplicationContext()
                    cb  = ctx.getSystemService(ctx.CLIPBOARD_SERVICE)
                    cb.setPrimaryClip(CD.newPlainText('EmergencyLog', full_text))
                    _dlog(f'[Debug] 복사 완료 ({len(_DEBUG_LINES)}줄)')
                    orig_text  = self._status_lbl.text
                    orig_color = tuple(self._status_lbl.color)
                    self._status_lbl.text  = '로그 복사됨!'
                    self._status_lbl.color = (0.2, 0.9, 0.3, 1)
                    def _restore(dt):
                        try:
                            self._status_lbl.text  = orig_text
                            self._status_lbl.color = orig_color
                        except Exception: pass
                    Clock.schedule_once(_restore, 3)
                else:
                    print(full_text)
            except Exception as e:
                _dlog(f'[Debug] 복사 실패: {e}')

        # ── PiP 자동갱신 타이머 ──────────────────────────────
        def _start_pip_refresh(self):
            _dlog(f'[PiP] 갱신 타이머 시작: iv={self._iv_sec}s')
            if self._pip_refresh_ev:
                try: self._pip_refresh_ev.cancel()
                except Exception: pass
            self._do_pip_fetch(0)
            if self._iv_sec >0:
                self._pip_refresh_ev = Clock.schedule_interval(
                    self._do_pip_fetch, self._iv_sec)

        def _do_pip_fetch(self, dt):
            h = self._h_param
            if not h:
                _dlog('[PiP] _do_pip_fetch: h_param 없어 스킵')
                return
            def _fetch():
                MAX_RETRY = 3
                for attempt in range(MAX_RETRY):
                    try:
                        import urllib.request, urllib.parse
                        url = (f'http://127.0.0.1:5000/pip_data'
                               f'?h={urllib.parse.quote(h)}&_t={int(time.time())}')
                        _dlog(f'[PiP] fetch 시작 (시도{attempt+1})')
                        with urllib.request.urlopen(url, timeout=8) as resp:
                            data = json.loads(resp.read().decode('utf-8'))
                        cnt = len(data.get('hospitals', []))
                        _dlog(f'[PiP] fetch 완료: {cnt}개')
                        Clock.schedule_once(lambda dt, d=data: self._update_pip_ui(d))
                        return
                    except Exception as e:
                        _dlog(f'[PiP] fetch 오류(시도{attempt+1}): {e}')
                        if attempt < MAX_RETRY - 1:
                            time.sleep(2)
                _dlog('[PiP] fetch 최대 재시도 초과')
            threading.Thread(target=_fetch, daemon=True, name='PipFetch').start()

        def _update_pip_ui(self, data):
            hospitals = data.get('hospitals', [])
            fetched   = data.get('fetched_at', '')

            #  FIX(2025): 빈 결과 수신 시 마지막 성공 데이터 유지
            # pip_data 예외 핸들러가 hospitals=[] 를 반환할 때 화면이 "데이터 없음"으로
            # 지워지지 않도록, 직전 성공 fetch 데이터를 그대로 재사용한다.
            if not hospitals:
                _prev_hospitals = getattr(self, '_last_good_pip_data', {}).get('hospitals', [])
                if _prev_hospitals:
                    _dlog(f'[PiP] fetch 0개 수신 → 직전 데이터 유지 ({len(_prev_hospitals)}개)'
                          + (f' error={data.get("error","")}' if data.get('error') else ''))
                    hospitals = _prev_hospitals
                    fetched   = getattr(self, '_last_good_pip_data', {}).get('fetched_at', fetched)
                else:
                    _dlog('[PiP] fetch 0개 + 이전 데이터 없음 → 데이터 없음 표시')

            # 성공 데이터(hospitals 있음) 시에만 _last_good_pip_data 갱신
            if hospitals:
                self._last_good_pip_data = {**data, 'hospitals': hospitals}
                #  ROOT-FIX 2026-E2: 데이터가 생긴 시점에 autoEnter 를 재설정.
                #   (기존엔 기동 2초 1회뿐이라 항상 False 로 굳어 있었다)
                try:
                    Clock.schedule_once(lambda _dt: self._setup_pip_auto_enter(), 0)
                except Exception:
                    pass
            self._last_fetch_ts = time.time()

            # ── 갱신 햅틱 + 알림음 (Android) ──────────────────────
            if _IS_ANDROID and hospitals:
                try:
                    def _do_haptic_beep():
                        try:
                            from jnius import autoclass
                            PA  = autoclass('org.kivy.android.PythonActivity')
                            ctx = PA.mActivity
                            # 가벼운 진동 (40ms)
                            try:
                                Vibrator = autoclass('android.os.Vibrator')
                                vib = ctx.getSystemService(ctx.VIBRATOR_SERVICE)
                                if vib and vib.hasVibrator():
                                    vib.vibrate(40)
                            except Exception as _ve:
                                _dlog(f'[Haptic] 진동 실패: {_ve}')
                            # 알림음 (ToneGenerator)
                            try:
                                TG = autoclass('android.media.ToneGenerator')
                                AM = autoclass('android.media.AudioManager')
                                tg = TG(AM.STREAM_NOTIFICATION, 40)
                                tg.startTone(TG.TONE_PROP_BEEP, 120)
                            except Exception as _te:
                                _dlog(f'[Haptic] 알림음 실패: {_te}')
                        except Exception as _he:
                            _dlog(f'[Haptic] 전체 실패 (무시): {_he}')
                    threading.Thread(target=_do_haptic_beep, daemon=True).start()
                except Exception:
                    pass

            #  최신 데이터 캐시 저장 — resize 시 _on_window_resize가 재빌드에 사용
            # hospitals 변수는 이미 0개 폴백 처리가 완료된 값을 사용한다.
            self._last_pip_data = {**data, 'hospitals': hospitals}

            # ── 폰트 자동조절: 병원수·PiP창 크기 기반 ─────────────
            # Window.on_resize 이벤트 → PiP 진입/크기조절 시 실제 창 크기 전달됨.
            # Window.width/height 직접 사용으로 가로/세로 전환 시 최대화 유지.
            try:
                w = Window.width
                h = Window.height
                n = max(1, len(hospitals))
                #  이슈8: 시인성 최대 폰트 — 병원수 2개 기준 16sp, 많을수록 축소
                #  창 비례 산식은 PiP 창에서 글자가 잘려 원복 (하한 11 / 상한 16)
                base_sp = max(11, min(16, 16 - max(0, n - 2)))
            except Exception:
                w = 480; h = 960; n = 1; base_sp = 13
            bar_sp = max(9, base_sp - 2)   # 막대 행: base보다 2sp 작게 (최소 9sp)
            self._pip_base_sp = base_sp
            self._pip_bar_sp  = bar_sp
            _dlog(f'[PiP] 폰트: base={base_sp}sp bar={bar_sp}sp (win={w}x{h}, n={n})')

            # BAR_N: 아래 _cell_bar_n() 으로 셀별 동적 계산

            # ── sp→dp 변환: 기기 fontScale·밀도 반영 ────────────────
            # Kivy의 height 속성은 dp 단위이나, [size=Xsp] 마크업은 sp 단위.
            # sp = dp * fontScale이므로 fontScale >1 환경(접근성 설정 등)에서는
            # sp값 그대로 height에 사용하면 텍스트가 컨테이너를 넘쳐 클리핑됨.
            # kivy.metrics.sp()가 sp → dp(픽셀) 변환값을 정확히 반환하므로
            # 이를 height 계산에 사용하면 클리핑을 완전히 방지할 수 있음.
            try:
                from kivy.metrics import sp as _kivy_sp
                _sp = _kivy_sp
            except Exception:
                _sp = lambda x: float(x)  # 폴백: 그대로 사용

            # ── 셀별 동적 블록 수 계산 (Window 실제 폭 기준 최대화) ──
            # beds_container=58% × 각 셀=1/3 → 셀 실제 픽셀폭 = w*0.58*0.333
            # 상한 min(12→50) 제거로 빈병상 비율 낮아도 블록 생략 없음
            # 가로/세로 전환 시 Window.width 변화로 자동 최대화 유지
            try:
                from kivy.metrics import sp as _msp_bar
                _char_w_px = max(4.0, _msp_bar(bar_sp) * 0.72)
            except Exception:
                _char_w_px = bar_sp * 0.72

            def _cell_bar_n(cell_sx=0.333):
                # 실제 셀 픽셀폭 = Window.width × beds_ratio(0.58) × cell_fraction
                cell_px = max(30, w * 0.58 * cell_sx)
                return max(5, min(50, int(cell_px / _char_w_px)))

            _bn_ec = _cell_bar_n(0.333)   # 응급실 (1/3)
            _bn_gc = _cell_bar_n(0.333)   # 입원실 (1/3)
            _bn_ic = _cell_bar_n(0.333)   # 중환자실 (1/3)

            def _hex_to_rgba(hex_str):
                """HEX 색상 문자열 → Kivy RGBA 튜플 변환"""
                h = hex_str.lstrip('#')
                return (int(h[0:2],16)/255.0, int(h[2:4],16)/255.0,
                        int(h[4:6],16)/255.0, 1.0)

            def _fmt(a, t=0, _bar_n=8):
                """(숫자 markup, 가용비율 0~1, 밝은색 hex, 어두운색 hex, 색상 hex) 반환
                반환값: (num_markup, p_ratio, bright_hex, dark_hex, color_hex)
                Canvas 기반 비율 바 — 픽셀 완벽, 항상 동일 전체 길이
                """
                _C_GREEN  = '#6BC96E'; _C_GUSED = '#1E421F'
                _C_YELLOW = '#EDBB4A'; _C_YUSED = '#58400A'
                _C_RED    = '#E05550'; _C_RUSED = '#511210'

                #  FIX(2026-C1): format_bed_cell()과 동일한 sentinel 조건으로 통일.
                # 구 코드: a == -1 → total 무관하게 회색 '-' 처리
                #   → hvec=-1 이지만 total=39인 경우(초과운용 데이터)를
                #     "정보없음"으로 잘못 숨기는 버그 (세브란스 응급 미표시).
                # 신 코드: a == -1 AND t <= 0 인 경우만 "정보없음" 처리.
                #   → a < 0 이지만 t >0 (예: -1/39)은 빨간색으로 정상 표시.
                #   브라우저 format_bed_cell()의 'avail == -1 and total <= 0' 조건과 동일.
                if a == -1 and t <= 0:
                    return ('[color=#444444]-[/color]',
                            0.0, '#333333', '#222222', '#444444')

                _a_display = a  # 표시용 원본 (음수 그대로 표시)
                if a < 0:
                    a = 0

                label = f'{_a_display}/{t}' if t >0 else str(_a_display)
                p     = (a / t) if t >0 else (1.0 if a >0 else 0.0)
                p     = max(0.0, min(1.0, p))

                c  = _C_GREEN  if p >= 0.5 else _C_YELLOW if p >= 0.2 else _C_RED
                cu = _C_GUSED  if p >= 0.5 else _C_YUSED  if p >= 0.2 else _C_RUSED

                return f'[color={c}][b]{label}[/b][/color]', p, c, cu, c

            #  FIX(2025-B3): 원자적 위젯 교체 — 사라짐 방지
            # 기존 방식: clear_widgets() → 하나씩 add_widget()
            #   → moveTaskToBack 타이밍과 겹치면 clear 후 add 전 상태가
            #     화면에 노출 → 백그라운드 복귀 시 빈 컨테이너 표시("사라짐")
            # 수정: 새 위젯을 먼저 모두 만들고 → 단일 교체 연산으로 swap
            _new_widgets = []

            # 행 높이·행간격 계산 — sp() 변환으로 실제 dp 높이 반영
            row_h = int(_sp(base_sp)) + int(_sp(bar_sp)) + 10
            spacer_h = max(4, row_h // 2)
            self._pip_container.spacing = 0

            # 조회 시각 행
            if fetched:
                ts_lbl = Label(
                    text=f'[size={base_sp}sp][color=#9d79f0][b]{fetched}[/b][/color][/size]',
                    markup=True,
                    size_hint_y=None, height=int(_sp(base_sp)) + 4,
                    halign='left', valign='middle')
                ts_lbl.bind(size=ts_lbl.setter('text_size'))
                _new_widgets.append(ts_lbl)

            if not hospitals:
                empty_lbl = Label(
                    text='[color=#444444]데이터 없음[/color]',
                    markup=True,
                    size_hint_y=None, height=row_h,
                    halign='left', valign='middle')
                empty_lbl.bind(size=empty_lbl.setter('text_size'))
                _new_widgets.append(empty_lbl)

            for idx, h in enumerate(hospitals):
                raw_name = h.get('name') or ''
                name     = _pip_shortname(raw_name)

                hvec  = h.get('hvec', -1); hvec_t  = h.get('hvec_t',  0)
                hvgc  = h.get('hvgc', -1); hvgc_t  = h.get('hvgc_t',  0)
                hv36  = h.get('hv36', -1); hv36_t  = h.get('hv36_t',  0)
                hicu  = h.get('hicu', -1); hicu_t  = h.get('hicu_t',  0)

                #  입원 표시: hvgc(일반) + hv36(응급전용) 합산
                # 비교화면의 "입원실 일반 + 응급전용" 합계와 동일하게 표시
                # 한쪽만 데이터 있는 경우(-1 제외)도 올바르게 합산
                if hvgc >= 0 and hv36 >= 0:
                    _gc_combined   = hvgc + hv36
                    _gc_t_combined = (hvgc_t if hvgc_t >0 else 0) + (hv36_t if hv36_t >0 else 0)
                elif hvgc >= 0:
                    _gc_combined   = hvgc
                    _gc_t_combined = hvgc_t
                elif hv36 >= 0:
                    _gc_combined   = hv36
                    _gc_t_combined = hv36_t
                else:
                    _gc_combined   = -1
                    _gc_t_combined = 0

                _gc_branch = ('양쪽합산' if hvgc >= 0 and hv36 >= 0
                              else 'hvgc만' if hvgc >= 0
                              else 'hv36만' if hv36 >= 0 else '없음')
                _dlog(f'[PiP][입원합산] {name} '
                      f'hvgc={hvgc}/{hvgc_t} hv36={hv36}/{hv36_t} '
                      f'→ 합={_gc_combined}/{_gc_t_combined} ({_gc_branch})')

                # 컬럼 순서: 응급 | 입원 | 중환자(우측)
                ec, ep, ec_c, ec_cu, e_color = _fmt(hvec, hvec_t, _bn_ec)
                gc, gp, gc_c, gc_cu, g_color = _fmt(_gc_combined, _gc_t_combined, _bn_gc)
                ic, ip, ic_c, ic_cu, i_color = _fmt(hicu, hicu_t, _bn_ic)

                _dlog(f'[PiP][fmt] {name} '
                      f'응급={hvec}/{hvec_t} ratio={ep:.2f} 색={e_color} | '
                      f'입원={_gc_combined}/{_gc_t_combined} ratio={gp:.2f} 색={g_color} | '
                      f'중환={hicu}/{hicu_t} ratio={ip:.2f} 색={i_color}')

                # ── 병원 행 전체: 이름(38%) | [3열 병상 62%] ──────────
                # row_h: 텍스트 사이즈 연동 (base_sp + bar_sp + 6)
                hosp_row = BoxLayout(orientation='horizontal',
                                     size_hint_y=None, height=row_h, spacing=1)

                # 병원명 — 좌측정렬 (42%) — 긴 병원명 표시 여유 확보
                name_lbl = Label(
                    text=f'[size={base_sp}sp][b][color=#dde0ff]{name}[/color][/b][/size]',
                    markup=True,
                    size_hint_x=0.42,
                    #  줄바꿈 금지 — 넘치면 말줄임 (잘림 방지)
                    shorten=True, shorten_from='right', split_str='',
                    max_lines=1,
                    halign='left', valign='middle')
                name_lbl.bind(size=name_lbl.setter('text_size'))
                hosp_row.add_widget(name_lbl)

                # ── 병상 3열 컨테이너 (58%) ──────────────────────────
                beds_container = BoxLayout(orientation='horizontal',
                                           size_hint_x=0.58, spacing=2)

                def _make_bed_cell(p_ratio, bright_hex, dark_hex, num_mu, col_tag, col_color, h_align, sx,
                                   _bsp=bar_sp, _bbase=base_sp,
                                   _rh=row_h, _sp_fn=_sp):
                    """병상 셀: Canvas 기반 비율 막대 + 숫자 레이블
                    - 막대: Canvas Rectangle으로 픽셀 완벽 비율 (항상 동일 전체 길이)
                    - bright_hex: 가용 병상색(밝은), dark_hex: 사용 병상색(어두운)
                    """
                    import re as _re_cell
                    _plain = _re_cell.sub(r'\[.*?\]', '', num_mu)
                    _plen  = len(_plain)
                    #  FIX(2025): 글자 수에 따라 폰트 크기와 숫자 열 폭을 동시 조정.
                    # 고DPI 기기(1440px 폭) 기준:
                    #   num_lbl 폭 ≈ num_sx × 0.333 × 0.58 × screen_w ≈ 153px (num_sx=0.55 시)
                    #   입원 "351/1268"(8자) → ~22px/자 × 8 = 176px >153px → 오버플로우
                    #   우측정렬이므로 맨 끝 1자리만 표시되는 증상 발생.
                    #   7자("74/1527")만 가까스로 들어가 마지막 병원만 정상 표시됨.
                    # 해결: 글자 수가 늘어날수록 폰트 축소 + 태그 열을 좁혀 숫자 열 확보.
                    if _plen <= 6:
                        _num_sp      = _bbase
                        _num_sx_frac = 0.55   # 숫자 55%, 태그 45%
                    elif _plen == 7:
                        _num_sp      = max(7, _bbase - 1)
                        _num_sx_frac = 0.62   # 숫자 62%, 태그 38%
                    elif _plen == 8:
                        _num_sp      = max(7, _bbase - 2)
                        _num_sx_frac = 0.68   # 숫자 68%, 태그 32%
                    else:  # 9자+
                        _num_sp      = max(7, _bbase - 3)
                        _num_sx_frac = 0.75   # 숫자 75%, 태그 25%
                    _tag_sp = _num_sp

                    bar_h = int(_sp_fn(_bsp)) + 3
                    num_h = int(_sp_fn(_num_sp)) + 6

                    cell = BoxLayout(orientation='vertical',
                                     size_hint_x=sx, spacing=0,
                                     padding=[1, 0, 1, 0])

                    # ── Canvas 기반 비율 막대 ──────────────────────────
                    from kivy.uix.widget import Widget as _BW
                    from kivy.graphics import Color as _GC, Rectangle as _GR
                    _p   = float(p_ratio)
                    _br  = _hex_to_rgba(bright_hex)
                    _dr  = _hex_to_rgba(dark_hex)
                    bar_widget = _BW(size_hint=(1, None), height=bar_h)
                    with bar_widget.canvas:
                        _gc_b = _GC(*_br)
                        _rect_b = _GR(pos=(0,0), size=(0, bar_h))
                        _gc_d = _GC(*_dr)
                        _rect_d = _GR(pos=(0,0), size=(0, bar_h))
                    def _upd(_w, *_a,
                             _rb=_rect_b, _rd=_rect_d, _pp=_p, _bh=bar_h):
                        _bw = int(_w.width * _pp)
                        _rb.pos  = (_w.x, _w.y);       _rb.size = (_bw, _w.height)
                        _rd.pos  = (_w.x + _bw, _w.y); _rd.size = (_w.width - _bw, _w.height)
                    bar_widget.bind(pos=_upd, size=_upd)

                    # ── 막대 아래: 태그(좌) + 숫자(우) 분리 레이아웃 ──
                    # col_tag=(응급)/(입원)/(중환) 은 막대 기준 좌측, 병상수는 우측
                    bottom_row = BoxLayout(orientation='horizontal',
                                           size_hint=(1, None), height=num_h,
                                           spacing=0, padding=[0, 0, 0, 0])
                    if col_tag:
                        tag_lbl = Label(
                            text=f'[color=#AAAAAA][size={_tag_sp}sp]{col_tag}[/size][/color]',
                            markup=True,
                            size_hint_x=(1.0 - _num_sx_frac),
                            halign='left', valign='middle')
                        tag_lbl.bind(size=tag_lbl.setter('text_size'))
                        bottom_row.add_widget(tag_lbl)
                        num_sx = _num_sx_frac
                    else:
                        num_sx = 1.0
                    num_lbl = Label(
                        text=f'[size={_num_sp}sp][b]{num_mu}[/b][/size]',
                        markup=True,
                        size_hint_x=num_sx,
                        halign='right', valign='middle')
                    num_lbl.bind(size=num_lbl.setter('text_size'))
                    bottom_row.add_widget(num_lbl)

                    cell.add_widget(bar_widget)
                    cell.add_widget(bottom_row)
                    return cell

                # 응급실: 좌측 정렬 (1/3)
                beds_container.add_widget(
                    _make_bed_cell(ep, ec_c, ec_cu, ec, '(응급)', e_color, 'right', 0.333))

                # 입원실: 중앙 (1/3)
                beds_container.add_widget(
                    _make_bed_cell(gp, gc_c, gc_cu, gc, '(입원)', g_color, 'right', 0.333))

                # 중환자실: 우측 (1/3)
                beds_container.add_widget(
                    _make_bed_cell(ip, ic_c, ic_cu, ic, '(중환)', i_color, 'right', 0.333))

                hosp_row.add_widget(beds_container)
                _new_widgets.append(hosp_row)

                # ── 행 사이 빈 행 추가 (마지막 행 제외) ─────────────
                if idx < len(hospitals) - 1:
                    spacer = Label(
                        text='',
                        size_hint_y=None,
                        height=spacer_h)
                    _new_widgets.append(spacer)

            # ── 원자적 교체: 새 위젯 목록이 준비된 후에만 화면 갱신 ──
            # 이 시점 이전에는 기존 화면이 그대로 유지됨
            self._pip_container.clear_widgets()
            try:
                self._pip_container.canvas.clear()
            except Exception:
                pass
            for _w in _new_widgets:
                self._pip_container.add_widget(_w)

            # _pip_data_lbl은 숨김 (호환성 유지용으로만 보존)
            # 강제 캔버스 갱신 (그래픽 깨짐 방지)
            try:
                self._pip_container.canvas.ask_update()
            except Exception:
                pass
            logging.info(f'[PiP] UI 갱신: {len(hospitals)}개 @ {fetched}')

        # ── PiP 모드 진입 ────────────────────────────────────
        def _pip_note(self, stage, msg='', ok=None):
            """[FIX 2026-H2] PiP 전 과정을 단일 태그로 기록 + 결과 슬롯 갱신."""
            _PIP_LAST['stage'] = stage
            _PIP_LAST['ts'] = time.time()
            if ok is not None:
                _PIP_LAST['ok'] = bool(ok)
                _PIP_LAST['reason'] = '' if ok else (msg or stage)
            _ulog('PIP', '%s%s' % (stage, (' | ' + msg) if msg else ''))

        def _pip_release(self, why=''):
            """busy 플래그 해제 단일 지점 — 어떤 경로로도 영구 잠김이 없게 한다."""
            self._pip_busy = False
            if why:
                _ulog('PIP', 'busy 해제 (%s)' % why)

        def _pip_capability_log(self):
            """PiP 가능 여부의 '근거'를 기록한다. 무반응의 최다 원인은
            매니페스트의 supportsPictureInPicture 누락이므로 반드시 남긴다."""
            try:
                from jnius import autoclass
                act = None
                for _c in ('org.kivy.android.PythonActivity',
                           'org.kivy.android.GenericActivity'):
                    try:
                        act = autoclass(_c).mActivity
                        if act is not None:
                            break
                    except Exception:
                        continue
                if act is None:
                    _PIP_LAST['device_feature'] = None
                    _PIP_LAST['manifest_flag'] = None
                    _ulog('PIP', 'capability: Activity 없음')
                    return
                pm = act.getPackageManager()
                feat = bool(pm.hasSystemFeature('android.software.picture_in_picture'))
                sup = None
                try:
                    ai = pm.getActivityInfo(act.getComponentName(), 0)
                    #  ActivityInfo.FLAG_SUPPORTS_PICTURE_IN_PICTURE = 0x00400000
                    sup = bool(int(ai.flags) & 0x00400000)
                except Exception as _aie:
                    _ulog('PIP', 'ActivityInfo 조회 실패: %s' % _aie)
                _PIP_LAST['device_feature'] = feat
                _PIP_LAST['manifest_flag'] = sup
                _ulog('PIP', 'capability: 기기지원=%s 매니페스트선언=%s pkg=%s'
                      % (feat, sup, act.getPackageName()))
                if sup is False:
                    _ulog('PIP', '★ 매니페스트에 android:supportsPictureInPicture="true" '
                                 '가 없음 → enterPictureInPictureMode 는 항상 실패한다')
            except Exception as e:
                _ulog('PIP', 'capability 조회 예외: %s' % e)

        def _enter_pip_mode(self):
            self._pip_note('enter 요청', 'h=%s busy=%s' % (
                (getattr(self, '_h_param', '') or '')[:40],
                getattr(self, '_pip_busy', False)))
            if not _IS_ANDROID:
                self._pip_note('스킵', 'PC 환경', ok=False)
                return

            #  [ROOT-FIX 2026-H2] _pip_busy 영구 잠김 제거
            #   기존 코드는 'API<26 → return' 경로에서 _pip_busy=True 를 남긴 채
            #   빠져나갔다. 해제 지점은 _do_pip 의 finally 뿐이므로, 그 경로를
            #   한 번이라도 타면 이후 모든 PiP 요청이 '이미 실행 중'으로 조용히
            #   무시된다 → [백그라운드]/[PiP] 버튼 무반응의 직접 원인.
            #   이제 ① 모든 조기 return 에서 해제 ② 5초 워치독 ③ 6초 이상
            #   잠겨 있으면 강제 해제 후 진행 — 어떤 경로로도 영구 잠김 불가.
            if getattr(self, '_pip_busy', False):
                if time.time() - getattr(self, '_pip_busy_ts', 0) > 6:
                    self._pip_note('busy 6초 초과', '강제 해제 후 진행')
                    self._pip_busy = False
                else:
                    self._pip_note('중복 호출 무시', 'busy')
                    return
            self._pip_busy = True
            self._pip_busy_ts = time.time()
            try:
                Clock.schedule_once(
                    lambda _dt: self._pip_release('워치독 5s'), 5.0)
            except Exception:
                pass

            api_level = EmergencyApp._get_real_api_level()
            _PIP_LAST['api'] = api_level
            self._pip_note('API 확인', 'level=%s' % api_level)
            self._pip_capability_log()

            if api_level not in (0, 99) and api_level < 26:
                self._pip_note('중단', 'API %s < 26 (PiP 미지원)' % api_level, ok=False)
                self._pip_release('API 미지원')
                return

            prefs = self._pip_prefs or {'aspect_w': 16, 'aspect_h': 9}

            def _do_pip():
                try:
                    try:
                        from jnius import autoclass
                        _dlog('[PiP] jnius import 성공')
                    except ImportError as _ie:
                        _dlog(f'[PiP] jnius import 실패: {_ie}')
                        return

                    activity = None
                    for _cls in ['org.kivy.android.PythonActivity',
                                 'org.kivy.android.GenericActivity']:
                        try:
                            PA    = autoclass(_cls)
                            _cand = PA.mActivity
                            if _cand is not None:
                                activity = _cand
                                _dlog(f'[PiP] Activity: {_cls}')
                                break
                            else:
                                _dlog(f'[PiP] {_cls}.mActivity = None')
                        except Exception as _ce:
                            _dlog(f'[PiP] {_cls}: {_ce}')

                    if activity is None:
                        try:
                            from android import activity as _aa
                            activity = _aa
                            _dlog('[PiP] Activity: android.activity')
                        except Exception as _ae:
                            _dlog(f'[PiP] android.activity: {_ae}')

                    if activity is None:
                        try:
                            AT       = autoclass('android.app.ActivityThread')
                            activity = AT.currentActivity()
                            _dlog('[PiP] Activity: ActivityThread')
                        except Exception as _at:
                            _dlog(f'[PiP] ActivityThread: {_at}')

                    if activity is None:
                        self._pip_note('중단', 'Activity 획득 실패(4경로 전부)', ok=False)
                        return

                    try:
                        PIPB     = autoclass('android.app.PictureInPictureParams$Builder')
                        Rational = autoclass('android.util.Rational')
                        _dlog('[PiP] PIPBuilder 로드 성공')

                        builder = PIPB().setAspectRatio(
                            Rational(prefs['aspect_w'], prefs['aspect_h']))
                        if api_level >= 31:
                            try:
                                #  ROOT-FIX 2026-E2: 무조건 True 로 켜면 이후
                                #   모든 백그라운드 이탈에서 자동 PiP 가 재현되어
                                #   좀비 창이 남는다. 데이터 보유 시에만 켠다.
                                _ae = bool(getattr(self, '_h_param', '')) and bool(
                                    (getattr(self, '_last_good_pip_data', None)
                                     or {}).get('hospitals'))
                                builder = builder.setAutoEnterEnabled(_ae)
                                _dlog(f'[PiP] setAutoEnterEnabled({_ae})')
                            except Exception:
                                pass
                        # 화면 우측 1/3 위치 힌트 (API 26+)
                        # sourceRectHint: PiP 진입 애니메이션 출발점 + 기본 위치 힌트
                        # 우측 1/3: x = 2/3*w, y = 화면 중간쯤, w = 1/3*w, h = 1/3*h
                        try:
                            Rect = autoclass('android.graphics.Rect')
                            dm   = autoclass('android.util.DisplayMetrics')()
                            activity.getWindowManager().getDefaultDisplay().getMetrics(dm)
                            sw, sh = dm.widthPixels, dm.heightPixels
                            rw = int(sw * 1 / 3)
                            rh = int(sh * 1 / 3)
                            rx = sw - rw          # 우측 끝 기준 1/3 폭
                            ry = int(sh * 1 / 6)  # 화면 위에서 1/6 지점
                            src_rect = Rect(rx, ry, rx + rw, ry + rh)
                            builder = builder.setSourceRectHint(src_rect)
                            _dlog(f'[PiP] sourceRectHint 설정: {rx},{ry} {rw}x{rh}')
                        except Exception as _re:
                            _dlog(f'[PiP] sourceRectHint 실패 (무시): {_re}')
                        params = builder.build()
                        result = activity.enterPictureInPictureMode(params)
                        if result is False:
                            self._pip_note('거부', 'enterPictureInPictureMode()=False '
                                                   '(매니페스트/포그라운드 조건 확인)', ok=False)
                        else:
                            self._pip_note('진입 성공', 'result=%s' % result, ok=True)

                        # 사용된 aspect ratio 저장
                        _save_pip_prefs(prefs)

                    except Exception as _be:
                        self._pip_note('파라미터 진입 실패',
                                       '%s: %s' % (type(_be).__name__, _be))
                        try:
                            activity.enterPictureInPictureMode()
                            self._pip_note('진입 성공', '파라미터 없이', ok=True)
                        except Exception as _e2:
                            #  PiP 불가 폴백: moveTaskToBack 으로 백그라운드 전환
                            #  (PiP 창은 안 뜨지만 타이머는 계속 동작)
                            _why = '%s: %s' % (type(_e2).__name__, _e2)
                            try:
                                activity.moveTaskToBack(True)
                                self._pip_note('폴백 백그라운드',
                                               'moveTaskToBack(True) — PiP 원인: ' + _why,
                                               ok=False)
                            except Exception as _mbe:
                                self._pip_note('전부 실패',
                                               'PiP=%s / moveTaskToBack=%s' % (_why, _mbe),
                                               ok=False)
                except Exception as _oe:
                    import traceback as _tb3
                    self._pip_note('예외', '%s: %s\n%s'
                                   % (type(_oe).__name__, _oe, _tb3.format_exc()),
                                   ok=False)
                finally:
                    #  _do_pip 완료(성공/실패/예외 불문) 후 플래그 해제.
                    # 1.5초 지연: on_pause 가 재트리거돼도 그 안의 호출까지 차단.
                    Clock.schedule_once(
                        lambda _dt: self._pip_release('_do_pip 완료'), 1.5)

            try:
                from android.runnable import run_on_ui_thread as _rut
                _rut(_do_pip)()
                self._pip_note('UI스레드 예약', 'run_on_ui_thread')
            except ImportError:
                Clock.schedule_once(lambda dt: _do_pip(), 0)
                self._pip_note('UI스레드 예약', 'android.runnable 없음 → Clock 대체')
            except Exception as _rue:
                self._pip_note('UI스레드 예약 실패', '%s → 직접 호출' % _rue)
                _do_pip()

        # ── 로깅 설정 ────────────────────────────────────────
        def _setup_logging(self):
            """[일원화 2026-H1] emergency_app.log 폐지.
            logging 모듈 출력까지 ermon.log 한 파일로 흘려보낸다."""
            global LOG_FILE
            #  안드로이드 외부저장 경로를 최우선 후보로 승격 (앱 폴더는 접근 불가)
            log_dir = None
            try:
                from jnius import autoclass as _ac
                _PA = _ac('org.kivy.android.PythonActivity')
                _ext = _PA.mActivity.getExternalFilesDir(None)
                if _ext:
                    log_dir = _ext.getAbsolutePath()
            except Exception:
                pass
            if not log_dir:
                try:
                    from android.storage import app_storage_path as _asp
                    log_dir = _asp()
                except Exception:
                    pass
            #  [일원화 2026-H3] ERMON_LOG(부트스트랩 지정)가 있으면 그 파일이
            #   최종이다. 없을 때만, 그리고 현재 경로가 사용자에게 보이지 않는
            #   내부 경로일 때만 외부저장소로 '이관'한다(복사+원본삭제).
            if _LOG_FIXED:
                _ulog('BOOT', '로그 경로 고정(ERMON_LOG): %s' % _log_path())
            elif log_dir:
                try:
                    os.makedirs(log_dir, exist_ok=True)
                    _cand = os.path.join(log_dir, LOG_NAME)
                    _cur = _LOG_PICK[0] or ''
                    _hidden = ('/data/data/' in _cur or '/data/user/' in _cur
                               or not _cur)
                    if _cur != _cand and _hidden:
                        _log_migrate(_cand)
                except Exception:
                    pass
            LOG_FILE = _log_path()
            _LOG_FILE_REF[0] = LOG_FILE

            class _UHandler(logging.Handler):
                def emit(self, rec):
                    try:
                        _ulog('LOG', self.format(rec))
                    except Exception:
                        pass

            _h = _UHandler()
            _h.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
            _root = logging.getLogger()
            _root.setLevel(logging.DEBUG)
            _root.handlers.clear()
            _root.addHandler(_h)
            _install_crash_hooks()
            try:                       # Kivy 내부 예외도 통합 로그로
                from kivy.base import ExceptionHandler, ExceptionManager
                import traceback as _tb2

                class _KivyEH(ExceptionHandler):
                    def handle_exception(self, inst):
                        _ulog('CRASH', 'KIVY %s\n%s' % (inst, _tb2.format_exc()))
                        return ExceptionManager.PASS

                ExceptionManager.add_handler(_KivyEH())
            except Exception as _ke:
                _ulog('BOOT', 'Kivy 예외핸들러 등록 실패: %s' % _ke)
            _ulog('BOOT', '통합 로그: %s' % LOG_FILE)
            _ulog('BOOT', '통합 상태파일: %s' % _state_path())
            _early_write(f'[STEP2] log: {LOG_FILE}')

        def _log_selftest(self):
            """디버깅 로그가 '실시간으로 파일에 반영되는지' 즉시 교차검증.
            표식을 기록 → 즉시 flush → 파일을 되읽어 존재를 확인한다.
            실패 시 헤더 상태라벨과 로그에 명시(무음 실패 금지)."""
            import uuid as _uuid
            mark = 'LOGSELFTEST-%s' % _uuid.uuid4().hex[:8]
            path = _LOG_FILE_REF[0]
            ok, detail = False, 'log path 없음'
            try:
                _ulog('SELFTEST', mark)
                path = _log_path()
                if path and os.path.exists(path):
                    with open(path, encoding='utf-8', errors='replace') as _f:
                        try:
                            _f.seek(max(0, os.path.getsize(path) - 65536))
                        except Exception:
                            pass
                        ok = mark in _f.read()
                    detail = '%s (%d bytes)' % (path, os.path.getsize(path))
                else:
                    detail = 'path=%s 미존재' % path
            except Exception as _e:
                detail = '예외 %s' % _e
            _dlog(f'[LogSelfTest] 실시간 기록 {"OK" if ok else "FAIL"} — {detail}')
            _early_write(f'[STEP2] logselftest={"OK" if ok else "FAIL"} {detail}')
            if not ok:
                try:
                    self._status_lbl.text = '로그기록 실패'
                    self._status_lbl.color = (0.95, 0.35, 0.35, 1)
                except Exception:
                    pass
            return ok

        # ══════════════════════════════════════════════════════════
        #  [ROOT-FIX 2026-E1] 단일 인스턴스 강제
        #  근본원인: 구 프로세스가 5000 포트를 문 채 살아 있으면 신규
        #  flask_app.run() 은 bind 실패로 스레드만 죽고, watchdog 은
        #  구 프로세스의 200 응답을 정상으로 오판한다. 결과적으로
        #  화면은 새 인스턴스 / 데이터는 구 인스턴스(오래된 로스터 =
        #  광주 0건) 라는 좀비 상태가 그대로 유지된다.
        # ══════════════════════════════════════════════════════════
        @staticmethod
        def _port_busy(port=5000, timeout=0.4):
            import socket
            sk = socket.socket()
            sk.settimeout(timeout)
            try:
                sk.connect(('127.0.0.1', port))
                return True
            except Exception:
                return False
            finally:
                try:
                    sk.close()
                except Exception:
                    pass

        @staticmethod
        def _peer_whoami(port=5000, timeout=2.0):
            try:
                import urllib.request
                with urllib.request.urlopen(
                        'http://127.0.0.1:%d/api/whoami' % port, timeout=timeout) as r:
                    return json.loads(r.read().decode('utf-8'))
            except Exception as _e:
                _dlog(f'[Instance] whoami 실패: {_e}')
                return None

        def _takeover_port(self, port=5000, wait=8.0):
            """이전 인스턴스가 포트를 점유 중이면 종료시키고 인수한다."""
            if not EmergencyApp._port_busy(port):
                _dlog(f'[Instance] 포트 {port} 비어있음 → 정상 기동')
                return True
            who = EmergencyApp._peer_whoami(port)
            pid = (who or {}).get('pid')
            if pid == _APP_PID:
                _dlog('[Instance] 이미 내 프로세스가 점유 중 → 재기동 불필요')
                return False
            _dlog(f'[Instance] 이전 인스턴스 감지 pid={pid} '
                  f'uptime={(who or {}).get("uptime")} → 자동 종료 요청')
            try:
                import urllib.request
                with urllib.request.urlopen(
                        'http://127.0.0.1:%d/api/app_exit?reason=takeover' % port,
                        timeout=3) as r:
                    r.read(128)
            except Exception as _e:
                _dlog(f'[Instance] app_exit 요청 실패: {_e}')
            t0 = time.time()
            while time.time() - t0 < wait:
                if not EmergencyApp._port_busy(port):
                    _dlog(f'[Instance] 포트 해제 확인 ({time.time() - t0:.1f}s)')
                    return True
                time.sleep(0.3)
            # 최후수단: 동일 UID 이므로 SIGKILL 가능
            if isinstance(pid, int) and pid > 0 and pid != _APP_PID:
                try:
                    import signal as _sg
                    os.kill(pid, _sg.SIGKILL)
                    _dlog(f'[Instance] SIGKILL pid={pid}')
                except Exception as _ke:
                    _dlog(f'[Instance] SIGKILL 실패: {_ke}')
            t0 = time.time()
            while time.time() - t0 < 4.0:
                if not EmergencyApp._port_busy(port):
                    _dlog('[Instance] SIGKILL 후 포트 해제 확인')
                    return True
                time.sleep(0.3)
            _dlog('[Instance] 포트 인수 실패 — 이전 인스턴스가 계속 점유 중', )
            return False

        def _start_flask(self):
            if getattr(self, '_exiting', False):
                _dlog('[Flask] 종료 진행 중 → 기동 생략')
                return

            def _run():
                try:
                    self._takeover_port(5000)
                except Exception as _te:
                    _dlog(f'[Instance] 포트 인수 예외(무시): {_te}')
                try:
                    logging.info('Flask 시작 (127.0.0.1:5000)...')
                    self._flask_bind_err = None
                    flask_app.run(host='127.0.0.1', port=5000,
                                  debug=False, use_reloader=False, threaded=True)
                except Exception as _e:
                    self._flask_bind_err = str(_e)
                    logging.error(f'Flask 오류: {_e}')
                    logging.error(traceback.format_exc())
                    _dlog(f'[Flask] 기동 실패(포트 점유 추정): {_e}')
                    try:
                        self._status_lbl.text = 'Flask 실패'
                        self._status_lbl.color = (0.95, 0.35, 0.35, 1)
                    except Exception:
                        pass
            t = threading.Thread(target=_run, daemon=True, name='Flask')
            t.start()
            logging.info('Flask 스레드 시작')
            self._flask_thread = t

        def _schedule_browser(self):
            """Flask 서버가 준비되면 /autostart URL을 열어 서버사이드 리다이렉트.
            Kivy 스레드 타이밍 문제를 완전히 우회한다."""
            _AUTOSTART = 'http://127.0.0.1:5000/autostart'

            def _open_when_ready():
                # Flask가 준비될 때까지 최대 15초 대기 (0.5초 간격)
                for _ in range(30):
                    try:
                        import urllib.request
                        with urllib.request.urlopen(
                                'http://127.0.0.1:5000/', timeout=2):
                            pass
                        break
                    except Exception:
                        time.sleep(0.5)
                _dlog(f'[Browser] Flask 준비 → {_AUTOSTART}')
                _open_browser_android(_AUTOSTART, 0.3)
                # PiP 갱신 타이머도 같이 복원
                Clock.schedule_once(lambda _dt: self._try_restore_state(), 0)

            Clock.schedule_once(lambda dt: threading.Thread(
                target=_open_when_ready, daemon=True).start(), 2)
            logging.info('브라우저 오픈 예약 (Flask 준비 후 /autostart)')


    _write_crash('[4] Creating EmergencyApp...')
    try:
        _app = EmergencyApp()
        _write_crash('[4] EmergencyApp created')
    except Exception as _e:
        _write_crash(f'[4] EmergencyApp() FAILED: {_e}')
        import traceback as _tb
        _write_crash(_tb.format_exc())
        raise

    _write_crash('[4] Calling EmergencyApp.run()...')
    try:
        _app.run()
        _write_crash('[4] EmergencyApp.run() returned')
    except Exception as _e:
        _write_crash(f'[4] EmergencyApp.run() FAILED: {_e}')
        import traceback as _tb
        _write_crash(_tb.format_exc())
        raise

else:
    # ── PC 실행 ──────────────────────────────────────────────────
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s [%(levelname)s] %(message)s')
    if __name__ == '__main__':
        print('='*70)
        print('응급의료기관 정보조회 시스템')
        print('='*70)
        print('Flask: http://localhost:5000')
        print('Ctrl+C to quit')
        print('='*70)
        try:
            import webbrowser
            threading.Thread(
                target=lambda: (time.sleep(1.5),
                                webbrowser.open('http://localhost:5000')),
                daemon=True).start()
        except Exception:
            pass
        flask_app.run(host='0.0.0.0', port=5000, debug=False)