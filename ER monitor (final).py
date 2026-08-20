# ═══════════════════════════════════════════════════════════════════
#  STEP 0: 절대 최초 충돌 로그 (import 전, builtins만 사용)
#  APK에서 로그가 안 생기면 이 파일로 Python 실행 여부 확인
# ═══════════════════════════════════════════════════════════════════
import sys as _sys
import os as _os

_crash_paths = [
    '/sdcard/Download/emergency_crash.log',
    '/sdcard/emergency_crash.log', 
    '/data/local/tmp/emergency_crash.log',
]

def _write_crash(msg):
    """파일 쓰기만 (logging 없이)"""
    for p in _crash_paths:
        try:
            with open(p, 'a') as f:
                f.write(msg + '\n')
                f.flush()
            return p
        except:
            continue
    return None

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

def _http_get(url, **kwargs):
    """requests.get과 동일 시그니처. 스레드-로컬 Session으로 연결 재사용."""
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
    """전역 로거 + stdout 동시 기록"""
    try:
        if level == 'ERROR':
            logging.error(msg)
        elif level == 'DEBUG':
            logging.debug(msg)
        else:
            logging.info(msg)
    except Exception:
        print(msg)

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
    if len(_DEBUG_LINES) > 300:
        _DEBUG_LINES.pop(0)
    _log(msg)  # 파일/stdout에도 기록

SERVICE_KEY = 'ac084c52bdaee51ccc5d0beedacbed40db1995171f5b980ae3549de259b2db3e'
_write_crash('[2] Flask routes registering...')
API_URL = 'https://apis.data.go.kr/B552657/ErmctInfoInqireService/getEmrrmRltmUsefulSckbdInfoInqire'
MSG_API_URL = 'https://apis.data.go.kr/B552657/ErmctInfoInqireService/getEmrrmSrsillDissMsgInqire'
LIST_API_URL = 'https://apis.data.go.kr/B552657/ErmctInfoInqireService/getEgytListInfoInqire'

DISTRICTS = {k: sorted(v) for k, v in {
    '서울특별시': ['강남구','강동구','강북구','강서구','관악구','광진구','구로구','금천구','노원구','도봉구','동대문구','동작구','마포구','서대문구','서초구','성동구','성북구','송파구','양천구','영등포구','용산구','은평구','종로구','중구','중랑구'],
    '부산광역시': ['강서구','금정구','남구','동구','동래구','부산진구','북구','사상구','사하구','서구','수영구','연제구','영도구','중구','해운대구','기장군'],
    '대구광역시': ['남구','달서구','동구','북구','서구','수성구','중구','달성군'],
    '인천광역시': ['계양구','남구','남동구','동구','부평구','서구','연수구','중구','강화군','옹진군'],
    '광주광역시': ['광산구','남구','동구','북구','서구'],
    '대전광역시': ['대덕구','동구','서구','유성구','중구'],
    '울산광역시': ['남구','동구','북구','중구','울주군'],
    '세종특별자치시': ['세종특별자치시'],
    '경기도': ['수원시','성남시','고양시','용인시','부천시','안산시','안양시','남양주시','화성시','평택시','의정부시','시흥시','파주시','광명시','김포시','군포시','광주시','이천시','양주시','오산시','구리시','안성시','포천시','의왕시','하남시','여주시','양평군','동두천시','과천시','가평군','연천군'],
    '강원특별자치도': ['춘천시','원주시','강릉시','동해시','태백시','속초시','삼척시','홍천군','횡성군','영월군','평창군','정선군','철원군','화천군','양구군','인제군','고성군','양양군'],
    '충청북도': ['청주시','충주시','제천시','보은군','옥천군','영동군','증평군','진천군','괴산군','음성군','단양군'],
    '충청남도': ['천안시','공주시','보령시','아산시','서산시','논산시','계룡시','당진시','금산군','부여군','서천군','청양군','홍성군','예산군','태안군'],
    '전북특별자치도': ['전주시','군산시','익산시','정읍시','남원시','김제시','완주군','진안군','무주군','장수군','임실군','순창군','고창군','부안군'],
    '전라남도': ['목포시','여수시','순천시','나주시','광양시','담양군','곡성군','구례군','고흥군','보성군','화순군','장흥군','강진군','해남군','영암군','무안군','함평군','영광군','장성군','완도군','진도군','신안군'],
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
    <title>🏥 응급의료기관 정보</title>
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
            padding: 14px;
            border: 2px solid #ddd;
            border-radius: 10px;
            font-size: clamp(1rem, 2.5vw, 1.1rem);
            background: white;
        }
        select:focus { outline: none; border-color: #667eea; }
        select:disabled { background: #f0f0f0; }
        .btn {
            width: 100%;
            padding: 16px;
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
        <h1>🏥 응급의료기관 정보</h1>
        <div class="form-group">
            <label for="sido">시/도 선택</label>
            <select id="sido"><option value="">시/도를 선택하세요</option></select>
        </div>
        <div class="form-group">
            <label for="gugun">시/군/구 선택</label>
            <select id="gugun" disabled><option value="">먼저 시/도를 선택하세요</option></select>
        </div>
        <button class="btn" id="searchBtn" disabled>🔍 병원 검색</button>
        <button class="btn" id="saveAppBtn" style="margin-top:8px;background:linear-gradient(135deg,#556b8d,#3a4d6b);">💾 저장 (단독 HTML — 선택+조회)</button>
        <button class="btn" id="secBtn" style="margin-top:8px;background:linear-gradient(135deg,#4a5f4a,#2f3f2f);">🧩 표시 항목 · 순서 설정</button>
        <div id="results"></div>
        <div class="selected-box" id="selectedBox">
            <div class="selected-title">
                <span>선택된 병원 (최대 5개)</span>
                <button class="btn" style="width:auto; padding:8px 16px; font-size:0.9rem;" id="compareBtn" disabled>
                    📋 정보보기
                </button>
            </div>
            <div id="selectedList"></div>
        </div>
    </div>

    <script>
        const districts = {{ districts|tojson }};

        // ── 🧩 표시 항목 · 순서 설정 (py/저장본 공용, localStorage 영구 기억) ──
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
                        + '<div style="font-weight:700;margin-bottom:6px;">🧩 표시 항목 · 순서</div>'
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
                        if (i > 0) { var x = c.order[i]; c.order[i] = c.order[i - 1]; c.order[i - 1] = x; }
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
            bar.textContent = '⚠️ 서버 연결 끊김 — Pydroid 3(파이썬 앱)를 다시 열어주세요. 자동 재접속 대기 중...';
            document.body.appendChild(bar);
            _reconT = setInterval(async () => {
                try {
                    const r = await fetch('/api/bed_notify_status', { cache: 'no-store' });
                    if (r.ok) {
                        clearInterval(_reconT); _reconT = null;
                        bar.style.background = '#2e7d32';
                        bar.textContent = '✅ 서버 재연결됨 — 다시 시도합니다';
                        setTimeout(() => { try { bar.remove(); } catch (e) {} }, 1500);
                        if (after) { try { after(); } catch (e) {} }
                    }
                } catch (e) {}
            }, 4000);
        }


        let hospitalsFullData = [];
        let selectedHospitals = [];
        let isSearching = false;

        // 레벨 뱃지 스타일 — 권역/센터/기관 색상을 조회화면 헤더와 동일 계열로
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
        try { document.getElementById('saveAppBtn').onclick = () => { location.href = '/export'; }; } catch(e) {}

        for (const sido in districts) {
            const opt = document.createElement('option');
            opt.value = sido; opt.textContent = sido;
            sidoSelect.appendChild(opt);
        }

        // ── 앱 시작 시: 시도/시군구는 초기화, 선택 병원만 복원 (수정7)
        window.addEventListener('load', function() {
            // 시도/시군구는 복원하지 않음 (앱 재실행 시 초기화)
            // 선택된 병원 목록만 복원
            try {
                const saved = localStorage.getItem('lastSelectedHospitals');
                if (saved) {
                    selectedHospitals = JSON.parse(saved);
                    updateSelectedBox();
                }
            } catch(e) { console.warn('선택병원 복원 실패:', e); }
        });

        function saveToLocalStorage() {
            // 선택 병원만 저장 (시도/시군구는 저장하지 않음)
            try {
                localStorage.setItem('lastSelectedHospitals', JSON.stringify(selectedHospitals));
            } catch(e) { console.warn('저장 실패:', e); }
        }

        sidoSelect.addEventListener('change', (e) => {
            const sido = e.target.value;
            gugunSelect.innerHTML = '<option value="">시/군/구를 선택하세요</option>';
            if (sido && districts[sido]) {
                districts[sido].forEach(gugun => {
                    const opt = document.createElement('option');
                    opt.value = gugun; opt.textContent = gugun;
                    gugunSelect.appendChild(opt);
                });
                gugunSelect.disabled = false;
            } else {
                gugunSelect.disabled = true;
                searchBtn.disabled = true;
            }
        });

        gugunSelect.addEventListener('change', (e) => {
            searchBtn.disabled = !e.target.value;
        });

        searchBtn.addEventListener('click', searchHospitals);

        function showDetailedError(error, context = '', responseText = '') {
            const errorInfo = {
                message: error.message || '알 수 없는 오류',
                type: error.name || 'Error',
                context: context,
                timestamp: new Date().toLocaleString('ko-KR'),
                stack: error.stack || '스택 정보 없음',
                response: responseText || '응답 없음'
            };
            const errorText = `오류 발생 시각: ${errorInfo.timestamp}\n오류 유형: ${errorInfo.type}\n오류 메시지: ${errorInfo.message}\n컨텍스트: ${errorInfo.context}\n\n서버 응답:\n${errorInfo.response}\n\n상세 스택:\n${errorInfo.stack}\n\n브라우저 정보: ${navigator.userAgent}`.trim();
            resultsDiv.innerHTML = `
                <div class="error">❌ 네트워크 오류가 발생했습니다</div>
                <div class="error-detail">
                    <h3>🔍 오류 상세 정보</h3>
                    <pre id="errorText">${errorText}</pre>
                    <div class="error-actions">
                        <button class="btn-copy" onclick="copyErrorToClipboard()">📋 오류 정보 복사</button>
                        <button class="btn-restart" onclick="restartApp()">🔄 앱 재시작</button>
                    </div>
                </div>`;
        }

        function copyErrorToClipboard() {
            const t = document.getElementById('errorText').textContent;
            navigator.clipboard.writeText(t).then(() => showToast('✅ 복사됨')).catch(() => {
                const ta = document.createElement('textarea');
                ta.value = t; document.body.appendChild(ta); ta.select();
                document.execCommand('copy'); document.body.removeChild(ta);
                showToast('✅ 복사됨');
            });
        }

        function restartApp() { location.reload(); }

        function showToast(message) {
            const toast = document.createElement('div');
            toast.className = 'copied-toast'; toast.textContent = message;
            document.body.appendChild(toast);
            setTimeout(() => toast.remove(), 3000);
        }

        async function searchHospitals() {
            if (isSearching) return;
            const sido  = sidoSelect.value;
            const gugun = gugunSelect.value;
            if (!sido || !gugun) { alert('시/도와 시/군/구를 모두 선택해주세요.'); return; }
            isSearching = true; searchBtn.disabled = true;
            resultsDiv.innerHTML = '<div class="loading"><div class="spinner"></div><p>병원 정보를 검색중입니다...</p></div>';
            let responseText = '';
            try {
                const response = await fetch(`/api/hospitals?sido=${encodeURIComponent(sido)}&gugun=${encodeURIComponent(gugun)}`);
                try { responseText = await response.text(); } catch(e) { responseText = '응답 읽기 실패'; }
                if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}\\n${responseText}`);
                let data;
                try { data = JSON.parse(responseText); } catch(e) { throw new Error(`JSON 파싱 오류: ${e.message}`); }
                if (data.success) { hospitalsFullData = data.hospitals; displayHospitals(data.hospitals); }
                else { resultsDiv.innerHTML = `<div class="error">❌ 오류: ${data.error}</div>`; }
            } catch (error) {
                console.error('검색 오류:', error);
                if (String((error && error.message) || error).match(/fetch|network/i))
                    startReconnect(() => { try { document.getElementById('searchBtn').click(); } catch (e) {} });
                showDetailedError(error, `병원 검색 (${sido} ${gugun})`, responseText || '');
            } finally { isSearching = false; searchBtn.disabled = false; }
        }

        function displayHospitals(hospitals) {
            if (hospitals.length === 0) {
                resultsDiv.innerHTML = '<div class="loading">😔 해당 지역에 응급의료기관이 없습니다.</div>';
                return;
            }
            // 레벨 뱃지 스타일 (전역 levelBadge 사용)
            let html = `<div style="margin:20px 0;font-weight:600;">총 ${hospitals.length}개 병원</div>`;
            hospitals.forEach((hospital, index) => {
                const isSelected = selectedHospitals.some(h => h.hpid === hospital.hpid);
                const badge = levelBadge[hospital.level] || levelBadge['기관'];
                html += `
                    <div class="hospital-item ${isSelected ? 'selected' : ''}" data-index="${index}">
                        <input type="checkbox" class="hospital-checkbox" data-index="${index}" ${isSelected ? 'checked' : ''}>
                        <div class="hospital-content">
                            <div class="hospital-name">${badge}${hospital.name}</div>
                            <div class="hospital-info">
                                📍 ${hospital.dutyAddr || '주소 정보 없음'}<br>
                                ☎️ 대표: ${hospital.dutyTel1 || '-'} | 응급실: ${hospital.dutyTel3 || '-'}
                            </div>
                        </div>
                    </div>`;
            });
            resultsDiv.innerHTML = html;
            document.querySelectorAll('.hospital-checkbox').forEach(checkbox => {
                checkbox.addEventListener('change', (e) => {
                    const index = parseInt(e.target.dataset.index);
                    const hospital = hospitalsFullData[index];
                    if (e.target.checked) {
                        if (selectedHospitals.length >= 5) {
                            alert('최대 5개 병원까지만 선택할 수 있습니다.');
                            e.target.checked = false; return;
                        }
                        selectedHospitals.push({
                            ...hospital,
                            sido:  sidoSelect.value,
                            gugun: gugunSelect.value
                        });
                    } else {
                        selectedHospitals = selectedHospitals.filter(h => h.hpid !== hospital.hpid);
                    }
                    updateSelectedBox(); updateCheckboxStates(); saveToLocalStorage();
                });
            });
        }

        function updateCheckboxStates() {
            document.querySelectorAll('.hospital-item').forEach(item => {
                const checkbox = item.querySelector('.hospital-checkbox');
                const hospital = hospitalsFullData[parseInt(checkbox.dataset.index)];
                const isSelected = selectedHospitals.some(h => h.hpid === hospital.hpid);
                item.classList.toggle('selected', isSelected);
                checkbox.checked = isSelected;
            });
        }

        function updateSelectedBox() {
            if (selectedHospitals.length === 0) { selectedBox.classList.remove('show'); return; }
            selectedBox.classList.add('show');
            selectedList.innerHTML = selectedHospitals.map(h => {
                const badge = levelBadge[h.level] || levelBadge['기관'];
                return `<div class="selected-item">
                    <input type="checkbox" checked onchange="removeHospital('${h.hpid}')">
                    <span>${badge}${h.name}</span>
                </div>`;
            }).join('');
            compareBtn.disabled = selectedHospitals.length < 1;
        }

        function removeHospital(hpid) {
            selectedHospitals = selectedHospitals.filter(h => h.hpid !== hpid);
            updateSelectedBox(); updateCheckboxStates(); saveToLocalStorage();
        }

        compareBtn.addEventListener('click', () => {
            if (selectedHospitals.length < 1) { alert('병원을 1개 이상 선택해주세요.'); return; }
            const currentSido  = sidoSelect.value  || '';
            const currentGugun = gugunSelect.value || '';
            const hParam = selectedHospitals.map(h => {
                const s = h.sido  || currentSido;
                const g = h.gugun || currentGugun;
                if (!s || !g) { return null; }
                return `${h.hpid}|${s}|${g}`;
            }).filter(Boolean).join(',');
            if (!hParam) { alert('지역 정보가 없습니다. 병원을 다시 검색하여 선택해주세요.'); return; }
            window.open('/compare?h=' + encodeURIComponent(hParam), '_blank');
        });
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
            background: white; padding: 15px; border-radius: 10px;
            margin-bottom: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); text-align: center;
        }
        .header h1 { color: #667eea; font-size: {{ title_font_size }}; margin-bottom: 5px; padding-left: 10%; }
        /* 응급의료상황판 폰트 +30% */
        .header h1 .h1-main { font-size: 0.91em; }
        .header h1 .h1-sub  { font-size: 0.55em; font-weight: normal; color: #555; }
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
        .comparison-table thead th.hospital-name {
            font-size: clamp(0.5rem, 1.5vw, 0.9rem);
            white-space: normal; word-break: keep-all; line-height: 1.3;
            max-width: 150px; overflow: visible;
        }
        .comparison-table thead th.hospital-name.long-name {
            font-size: clamp(0.35rem, 1.1vw, 0.65rem);
            white-space: normal; word-break: keep-all; line-height: 1.3;
            max-width: 150px; overflow: visible;
        }
        .comparison-table thead th.hospital-name.very-long-name {
            font-size: clamp(0.28rem, 0.85vw, 0.5rem);
            white-space: normal; word-break: keep-all; line-height: 1.3;
            max-width: 150px; overflow: visible;
        }
        .comparison-table thead th:first-child { width: 15%; min-width: 60px; }
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
        <h1><span class="h1-main">응급의료상황판</span><span class="h1-sub">&nbsp;(🕐&nbsp;<span id="queryTime">{{ current_time }}</span>&nbsp;기준)</span></h1>
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
            <button id="saveHtmlBtn">💾 저장</button>
            <button id="monitorBtn">🔔 모니터</button>
            <button id="secBtn">🧩 항목</button>
        </div>
        <!-- 갱신 진행바 -->
        <div style="margin: 0 0 0 0; padding-top: 0;">
            <div class="bed-cell" style="max-width: 400px; margin: 0 auto; padding: 0;">
                <div class="bed-info">
                    <div class="bar-container" style="height: 10px; overflow: visible;">
                        <div class="bar bar-green" id="globalRefreshBar" style="width: 100%"></div>
                        <div class="bed-text-overlay green-text" id="globalRefreshOverlay"
                             style="font-size:0.60em;white-space:nowrap;overflow:visible;
                                    top:50%;transform:translate(-50%,-50%);line-height:1;">
                             ⏰ <span id="globalRefreshText">3:00</span>
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

        // ── 병상 알림 모니터 (🔔) ──────────────────────────────
        function bedToast(msg, dur) {
            try {
                const t = document.createElement('div');
                t.textContent = msg;
                t.style.cssText = 'position:fixed;bottom:70px;left:50%;transform:translateX(-50%);'
                    + 'background:rgba(30,30,30,0.92);color:#fff;padding:9px 16px;border-radius:20px;'
                    + 'font-size:0.85rem;z-index:9999;max-width:86vw;text-align:center;';
                document.body.appendChild(t);
                setTimeout(() => t.remove(), dur || 2500);
            } catch(e) {}
        }
        // ── 🔔 병상 모니터 패널 (복수 병원 선택 · 주기 · 방식 · 카운트다운) ──
        function _monHospitalsFromPage() {
            const q = new URLSearchParams(location.search);
            let h = q.get('h') || '';
            if (!h) {
                const hp = q.get('hpids') || '', sd = q.get('sido') || '', gg = q.get('gugun') || '';
                if (hp && sd && gg) h = hp.split(',').map(x => x + '|' + sd + '|' + gg).join(',');
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
            const runSet = new Set((st.hospitals || []).map(x => x.hpid));
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
                + '<div style="font-weight:700;margin-bottom:8px;">🔔 병상 모니터 '
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
                + 'border:none;border-radius:10px;background:#ede7f6;font-weight:700;">⚙</button>'
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
            const sel = Array.from(document.querySelectorAll('.mon-hp:checked')).map(c => c.value);
            const hospitals = info.list.filter(hh => sel.includes(hh.hpid));
            const body = { action: action, hospitals: hospitals, h: info.h,
                           iv: parseInt(document.getElementById('monIv').value),
                           mode: document.getElementById('monMode').value };
            try {
                const r = await fetch('/api/bed_notify', { method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body) });
                const d = await r.json();
                bedToast(d.msg || (d.ok ? '완료' : '실패'));
                if (d.warn) setTimeout(() => bedToast('⚠️ ' + d.warn, 5500), 700);
            } catch (e) { bedToast('요청 실패: ' + e.message); }
            closeMonitorPanel();
        }
        try { document.getElementById('monitorBtn').onclick = openMonitorPanel; } catch (e) {}

        // ── 💾 저장: 현재 병원 구성 그대로 단독 HTML 다운로드 ─────
        try {
            document.getElementById('saveHtmlBtn').onclick = function () {
                let h = new URLSearchParams(location.search).get('h') || '';
                if (!h) {
                    const q = new URLSearchParams(location.search);
                    const hp = q.get('hpids') || '', sd = q.get('sido') || '', gg = q.get('gugun') || '';
                    if (hp && sd && gg) h = hp.split(',').map(x => x + '|' + sd + '|' + gg).join(',');
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
            bar.textContent = '⚠️ 서버 연결 끊김 — Pydroid 3(파이썬 앱)를 다시 열어주세요. 자동 재접속 대기 중...';
            document.body.appendChild(bar);
            _reconT = setInterval(async () => {
                try {
                    const r = await fetch('/api/bed_notify_status', { cache: 'no-store' });
                    if (r.ok) {
                        clearInterval(_reconT); _reconT = null;
                        bar.style.background = '#2e7d32';
                        bar.textContent = '✅ 서버 재연결됨 — 다시 시도합니다';
                        setTimeout(() => { try { bar.remove(); } catch (e) {} }, 1500);
                        if (after) { try { after(); } catch (e) {} }
                    }
                } catch (e) {}
            }, 4000);
        }

        // ── 📺 라이브 미니창 (저장본과 동일: PiP 그래프 · 메인 조회와 완전 동기) ──
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
                { name: '검정',   s: { radius: 0,  border: '1px solid #555', bg: 'rgba(0,0,0,0.85)', bgSolid: '#000000', color: '#ffffff', weight: '700', fontSize: 44, opacity: 85 } },
                { name: '화이트', s: { radius: 10, border: '1px solid #bbb', bg: 'rgba(255,255,255,0.92)', bgSolid: '#f2f2f2', color: '#111111', weight: '700', fontSize: 44, opacity: 92 } },
                { name: '유리',   s: { radius: 14, border: '1px solid #9ec1d9', bg: 'rgba(210,230,245,0.55)', bgSolid: '#d7e6f2', color: '#0b2b45', weight: '700', fontSize: 44, opacity: 55 } },
                { name: '고대비', s: { radius: 0,  border: '2px solid #ffffff', bg: 'rgba(0,0,0,0.95)', bgSolid: '#000000', color: '#ffee00', weight: '800', fontSize: 48, opacity: 95 } },
                { name: '녹색',   s: { radius: 6,  border: '1px solid #00aa55', bg: 'rgba(0,40,25,0.85)', bgSolid: '#002819', color: '#4dff9d', weight: '700', fontSize: 44, opacity: 85 } }
            ];
            var presetIdx = 0;
            function styleGet(k) { return STYLE[k]; }
            function _miniKick() { try { refreshPage(); } catch (e) {} }
            var IV_CYCLE = [60000, 180000, 300000, 600000, 0];
            function _ivLabel() {
                var v = (typeof currentInterval !== 'undefined') ? currentInterval : 0;
                if (!(v > 0)) return '수동';
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
                        return 'rgba(' + ((v >> 16) & 255) + ',' + ((v >> 8) & 255) + ',' + (v & 255) + ',' + a + ')';
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
                    if (hp && sd && gg) h = hp.split(',').map(x => x + '|' + sd + '|' + gg).join(',');
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
            function valTxt(a, t) { return (a < 0 ? '-' : a) + '/' + (t > 0 ? t : '-'); }
            async function fetchMetrics() {
                try {
                    const r = await fetch('/pip_data?h=' + encodeURIComponent(hParam()), { cache: 'no-store' });
                    const d = await r.json();
                    lastTs = String(d.fetched_at || '').slice(0, 5);
                    lastMs = (d.hospitals || []).map(function (x) {
                        var ga = (x.hvgc < 0 && x.hv36 < 0) ? -1 : Math.max(x.hvgc, 0) + Math.max(x.hv36, 0);
                        var gt = Math.max(x.hvgc_t || 0, 0) + Math.max(x.hv36_t || 0, 0);
                        return { name: x.name,
                                 m: [ { lbl: '응급', a: x.hvec, t: (x.hvec_t > 0 ? x.hvec_t : 0) },
                                      { lbl: '입원', a: ga, t: gt },
                                      { lbl: '중환', a: x.hicu, t: (x.hicu_t > 0 ? x.hicu_t : 0) } ] };
                    });
                    return;
                } catch (e) { /* 서버 끊김 → 직접조회 데이터로 폴백 */ }
                const hd = window.__lastHd;
                if (!hd || !hd.length) throw new Error('서버 끊김 · 폴백 데이터 없음');
                const now = new Date();
                lastTs = ('0' + now.getHours()).slice(-2) + ':' + ('0' + now.getMinutes()).slice(-2);
                lastMs = hd.map(function (h) {
                    function pv(p) { return { a: (p && p.avail !== undefined) ? p.avail : -1,
                                              t: (p && p.total > 0) ? p.total : 0 }; }
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
                if (!(currentInterval > 0) || !nextRefreshTime) {
                    bar.style.width = '100%'; cnt.textContent = '수동'; return;
                }
                var remain = Math.max(0, nextRefreshTime - Date.now());
                bar.style.width = Math.max(0, Math.min(100, remain / currentInterval * 100)) + '%';
                var s = Math.ceil(remain / 1000);
                cnt.textContent = Math.floor(s / 60) + ':' + ('0' + (s % 60)).slice(-2);
                var ib = d.getElementById('mnIv') || d.getElementById('lmIv');
                if (ib) ib.textContent = '⏱' + _ivLabel();
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
                        var p = (m.a >= 0 && m.t > 0) ? Math.min(1, m.a / m.t) : 0;
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
                 + 'background:#fff;color:#000;font-weight:800;padding:1px 6px;cursor:pointer;">⏱'
                 + _ivLabel() + '</button>'
                 + '<button id="lmRef" title="즉시 갱신" style="border:1px solid #000;background:#fff;'
                 + 'color:#000;font-weight:800;padding:1px 8px;cursor:pointer;">⟳</button>'
                 + '<button id="lmCls" title="닫기" style="border:1px solid #000;background:#fff;'
                 + 'color:#000;font-weight:800;padding:1px 8px;cursor:pointer;">✕</button></div>'
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
                    while (nm.length > 2 && g.measureText(nm).width > CW - padX * 2) nm = nm.slice(0, -1);
                    g.fillText(nm, padX, y0 + rowH * 0.04);   // 병원명 = 병상정보 위
                    var barH = Math.max(10, rowH * 0.15);
                    var barY = y0 + rowH * 0.44;
                    var labY = barY + barH + Math.max(4, rowH * 0.05);
                    ms.forEach(function (m, k) {              // 응급·입원·중환 = 고정 3열
                        var x = padX + colW * k, w = colW - gap;
                        var pr = ratioPair(m.a, m.t);
                        var p = (m.a >= 0 && m.t > 0) ? Math.min(1, m.a / m.t) : 0;
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
                var remainMs = (currentInterval > 0 && nextRefreshTime)
                    ? Math.max(0, nextRefreshTime - Date.now()) : 0;
                var pct = (currentInterval > 0)
                    ? Math.max(0, Math.min(1, remainMs / currentInterval)) : 1;
                var by = CH - hFoot + 8;
                g.fillStyle = 'rgba(255,255,255,0.22)';
                g.fillRect(26, by, CW - 52, 12);
                g.fillStyle = STYLE.color;
                g.fillRect(26, by, (CW - 52) * pct, 12);
                var sL = Math.ceil(remainMs / 1000);
                var cdt = (currentInterval > 0)
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
                            if (video.videoWidth > 0 || Date.now() - t0 > 800) res();
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
                                _miniKick();                        // ⏯ = 즉시 갱신
                            });
                            navigator.mediaSession.setActionHandler('nexttrack', function () { cyclePreset(1); });          // ⏭ = 디자인
                            navigator.mediaSession.setActionHandler('previoustrack', function () { cycleMainInterval(); }); // ⏮ = 주기
                            if (window.MediaMetadata)
                                navigator.mediaSession.metadata = new MediaMetadata(
                                    { title: '병상 미니창', artist: '⏯갱신 · ⏮주기 · ⏭디자인 · 크기=핀치' });
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
                    + '<div style="font-weight:700;margin-bottom:6px;">📺 미니창 스타일</div>'
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

        // ── 🧩 표시 항목 · 순서 설정 (py/저장본 공용, localStorage 영구 기억) ──
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
                        + '<div style="font-weight:700;margin-bottom:6px;">🧩 표시 항목 · 순서</div>'
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
                        if (i > 0) { var x = c.order[i]; c.order[i] = c.order[i - 1]; c.order[i - 1] = x; }
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
                if (bar) bar.textContent = '⚠️ 서버 끊김 — 직접조회 모드로 갱신 중 (Pydroid 재실행 시 자동 복귀)';
            } catch (e) {}
        }

        async function refreshPage() {
            if (isRefreshing) return;
            if (fallbackMode) {
                isRefreshing = true;
                try { await fallbackRefresh(); }
                catch (e) { console.error('직접조회 실패:', e); }
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
                if (txt) txt.textContent = '⚠️ 갱신 실패: ' + err.message;
                if (bar) { bar.className = 'bar bar-red'; bar.style.width = '100%'; }
                // 오류 메시지를 3초간 표시한 후 타이머 재시작
                await new Promise(r => setTimeout(r, 3000));
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
            if (currentInterval > 0) {
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
            if (pct > 60) {
                grad = 'linear-gradient(to bottom,#80B382,#507A52)';
            } else if (pct > 30) {
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
                    if (!isNaN(savedTime) && savedTime > nextRefreshTime) {
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
        (function() {
            document.getElementById('pipBtn').addEventListener('click', function() {
                var hParam = new URLSearchParams(location.search).get('h') || '';
                var ivMs   = document.getElementById('refreshInterval').value || '180000';
                var ivSec  = Math.round(parseInt(ivMs) / 1000);
                var btn    = document.getElementById('pipBtn');
                btn.disabled = true;
                btn.textContent = '📺 PiP 전환 중...';
                fetch('/api/enter_pip', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({h: hParam, iv: ivSec})
                })
                .then(function(r) { return r.json(); })
                .then(function(d) {
                    if (d.ok) {
                        btn.textContent = '📺 PiP 요청됨';
                        // Kivy가 PiP 진입하면 브라우저는 백그라운드로 전환됨
                        // 3초 후 버튼 복원 (Kivy PiP 진입 실패 시 대비)
                        setTimeout(function() {
                            btn.textContent = '📺 백그라운드';
                            btn.disabled = false;
                        }, 3000);
                    } else {
                        btn.textContent = '📺 백그라운드';
                        btn.disabled = false;
                    }
                })
                .catch(function(e) {
                    console.error('[PiP] 요청 실패:', e);
                    btn.textContent = '📺 백그라운드';
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

                if (textW > containerW - 2) {
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
                const cellW = tableW > 0 ? (tableW - firstColW) / numCols : 0;
                if (cellW > 0) {
                    document.querySelectorAll('.comparison-table td:not(.item-label):not(.category-header)').forEach(td => {
                        const inner = td.querySelector('.bed-numbers, .equipment-cell, .bed-cell');
                        const el = inner || td;
                        el.style.fontSize = '';
                        const curSz = parseFloat(window.getComputedStyle(el).fontSize);
                        if (el.scrollWidth > cellW + 4) {
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
                    if (!isNaN(savedTime) && savedTime > now) {
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
                    if (!isRefreshing && currentInterval > 0) {
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


@flask_app.route('/')
def index():
    return _render_cached(HTML, districts=DISTRICTS)

@flask_app.route('/api/hospitals')
def get_hospitals():
    sido  = request.args.get('sido', '')
    gugun = request.args.get('gugun', '')
    print(f"\n{'='*60}\n[API 요청] 시도: {sido}, 시군구: {gugun}\n{'='*60}")
    if not sido or not gugun:
        return jsonify({'success': False, 'error': '시/도와 시/군/구를 입력해주세요.'})
    try:
        # [최적화] 목록 API를 병상 API와 동시에 시작 (기존: 병상 완료 후 직렬 호출)
        # 결과 적용 순서·실패 처리 방식은 기존과 동일하다.
        list_params = {'serviceKey': SERVICE_KEY, 'Q0': sido, 'Q1': gugun,
                       'pageNo': '1', 'numOfRows': '100'}
        _list_future = _NET_POOL.submit(
            _http_get, LIST_API_URL, params=list_params, timeout=10)

        params = {'serviceKey': SERVICE_KEY, 'STAGE1': sido, 'STAGE2': gugun, 'pageNo': '1', 'numOfRows': '100'}
        response = _http_get(API_URL, params=params, timeout=15)
        print(f"[API 응답] 상태코드: {response.status_code}")
        response.raise_for_status()
        root = ET.fromstring(response.content)
        result_code = root.findtext('.//resultCode')
        result_msg  = root.findtext('.//resultMsg', '알 수 없는 메시지')
        print(f"[API 결과] 코드: {result_code}, 메시지: {result_msg}")
        if result_code != '00':
            return jsonify({'success': False, 'error': f'API 오류 ({result_code}): {result_msg}'})
        hospitals = [parse_hospital_data(item) for item in root.findall('.//item')]

        # 병원 분류 정보 조회 (목록 API) — 위에서 병렬로 시작한 결과를 수신
        try:
            list_resp = _list_future.result()
            if list_resp.status_code == 200:
                list_root = ET.fromstring(list_resp.content)
                emcls_map = {}
                for li in list_root.findall('.//item'):
                    hpid = (li.findtext('hpid') or '').strip()
                    emcls = (li.findtext('dutyEmcls') or '').strip()
                    emcls_name = (li.findtext('dutyEmclsName') or '').strip()
                    if hpid:
                        emcls_map[hpid] = (emcls, emcls_name)
                for h in hospitals:
                    info = emcls_map.get(h['hpid'], ('', ''))
                    h['emcls'] = info[0]
                    h['emclsName'] = info[1]
                    h['level'] = _get_hospital_level(h['emcls'], h['name'])
        except Exception as le:
            print(f"[경고] 목록 API 조회 실패 (무시): {le}")

        print(f"[API 성공] 병원 수: {len(hospitals)}개")
        return jsonify({'success': True, 'hospitals': hospitals})
    except Exception as e:
        print(f"[오류] {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'error': f'서버 오류: {str(e)}'})


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
    'D001': '내과',          'D002': '소아청소년과',   'D003': '신경과',
    'D004': '정신건강의학과', 'D005': '피부과',         'D006': '외과',
    'D007': '흉부외과',      'D008': '정형외과',       'D009': '신경외과',
    'D010': '성형외과',      'D011': '산부인과',       'D012': '안과',
    'D013': '이비인후과',    'D014': '비뇨기과',       'D016': '재활의학과',
    'D017': '마취통증의학과','D018': '영상의학과',     'D019': '치료방사선과',
    'D020': '임상병리과',    'D021': '해부병리과',     'D022': '가정의학과',
    'D023': '핵의학과',      'D024': '응급의학과',     'D026': '치과',
    'D034': '구강악안면외과',
}


# ══════════════════════════════════════════════════════════════════
#  중증질환 수용가능 키오스크 코드 → 항목명 매핑 (getSrsillDissAceptncPosblInfoInqire)
# ══════════════════════════════════════════════════════════════════
MKIOSK_MAP = {
    # 공식 매핑: Ty1=응급실, Ty2~Ty28 = 중증응급질환 27종 (메시지 API Y코드와 동일 순서)
    'MKioskTy1':  '응급실 수용',
    'MKioskTy2':  '[재관류중재술] 심근경색',
    'MKioskTy3':  '[재관류중재술] 뇌경색',
    'MKioskTy4':  '[뇌출혈수술] 거미막하출혈',
    'MKioskTy5':  '[뇌출혈수술] 거미막하출혈 외',
    'MKioskTy6':  '[대동맥응급] 흉부',
    'MKioskTy7':  '[대동맥응급] 복부',
    'MKioskTy8':  '[담낭담관질환] 담낭질환',
    'MKioskTy9':  '[담낭담관질환] 담도포함질환',
    'MKioskTy10': '[복부응급수술] 비외상',
    'MKioskTy11': '[장중첩/폐색] 영유아',
    'MKioskTy12': '[응급내시경] 성인 위장관',
    'MKioskTy13': '[응급내시경] 영유아 위장관',
    'MKioskTy14': '[응급내시경] 성인 기관지',
    'MKioskTy15': '[응급내시경] 영유아 기관지',
    'MKioskTy16': '[저출생체중아] 집중치료',
    'MKioskTy17': '[산부인과응급] 분만',
    'MKioskTy18': '[산부인과응급] 산과수술',
    'MKioskTy19': '[산부인과응급] 부인과수술',
    'MKioskTy20': '[중증화상] 전문치료',
    'MKioskTy21': '[사지접합] 수족지접합',
    'MKioskTy22': '[사지접합] 수족지접합 외',
    'MKioskTy23': '[응급투석] HD',
    'MKioskTy24': '[응급투석] CRRT',
    'MKioskTy25': '[정신과적응급] 폐쇄병동입원',
    'MKioskTy26': '[안과적수술] 응급',
    'MKioskTy27': '[영상의학혈관중재] 성인',
    'MKioskTy28': '[영상의학혈관중재] 영유아'
}

# ══════════════════════════════════════════════════════════════════
#  Y코드 → 한국어명 매핑 (API가 Y코드를 반환하는 경우 대비)
# ══════════════════════════════════════════════════════════════════
Y_CODE_MAP = {
    'Y000':  '응급실',
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


def _clean_msg(sym_blk_msg):
    """symBlkMsg에서 [응급] 접두사 제거 (이슈7)"""
    msg = (sym_blk_msg or '').strip()
    for prefix in ('[응급] ', '[응급]'):
        if msg.startswith(prefix):
            msg = msg[len(prefix):].strip()
            break
    return msg


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
                sym_blk_msg     = (item.findtext('symBlkMsg')     or '').strip()
                sym_typ_cod_mag = (item.findtext('symTypCodMag')  or '').strip()
                sym_typ_cod     = (item.findtext('symTypCod')     or '').strip()
                sym_blk_msg_typ = (item.findtext('symBlkMsgTyp')  or '').strip()
                sym_out_dsp_yon = (item.findtext('symOutDspYon')  or '').strip()

                # 모든 태그 덤프 (디버그)
                all_tags = {child.tag: (child.text or '') for child in item}
                print(f"[MSG_RAW] {hpid} p{page} | "
                      f"symTypCod={sym_typ_cod!r} symTypCodMag={sym_typ_cod_mag!r} "
                      f"symBlkMsg={sym_blk_msg!r} msgTyp={sym_blk_msg_typ!r} "
                      f"DspYon={sym_out_dsp_yon!r} | all_tags={all_tags}")

                # Y/D 코드 → 한국어명 변환
                label = _resolve_type_label(sym_typ_cod_mag, sym_typ_cod)
                # [응급] 접두사 제거 (이슈7)
                clean_msg = _clean_msg(sym_blk_msg)

                print(f"[MSG_PROC] label_raw={label!r} clean_msg={clean_msg!r}")

                # 응급실 타입이면 라벨 숨김 (이슈7)
                if label in ('응급실', ''):
                    label = ''

                if not clean_msg and not label:
                    print(f"[MSG_PROC] -> SKIPPED (both empty)")
                    continue

                # 최종 content 구성
                if label and clean_msg:
                    content = f"{label}: {clean_msg}"
                elif clean_msg:
                    content = clean_msg
                else:
                    content = label

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
            return hpid, '✅ 정상'
    except Exception as e:
        print(f"예외상황 조회 오류 (hpid={hpid}): {e}")
        return hpid, '✅ 정상'


def _fetch_messages_direct(hpids):
    """
    병렬로 예외상황 메시지 조회 (HTTP 자기호출 없이 직접).
    반환값: {hpid: '✅ 정상' | '[수용불가] ...\n[문의필요] ...' }
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
                messages[hpid] = '✅ 정상'
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
        params = {
            'serviceKey': SERVICE_KEY,
            'STAGE1': sido, 'STAGE2': gugun,
            'numOfRows': '100', 'pageNo': '1',
        }
        resp = _http_get(
            'https://apis.data.go.kr/B552657/ErmctInfoInqireService/getSrsillDissAceptncPosblInfoInqire',
            params=params, timeout=10)
        if resp.status_code != 200:
            return {}
        root = ET.fromstring(resp.content)
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
#  - 비교화면에서 병원 1곳 선택(🔔) → Flask 백그라운드 스레드가
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
    'hospitals': [],          # [{'hpid','name','sido','gugun'}, ...] (복수 선택)
    'iv_sec': 180, 'h_param': '', 'thread': None,
    'stop_event': None, 'kick_event': None,   # kick: '지금 갱신' 버튼용
    'line_map': {},           # hpid → 마지막 표시줄
    'last_line': '', 'last_ts': '', 'next_epoch': 0.0,
    'mode': 'notify',         # 'notify'(알림) | 'overlay'(상단 오버레이)
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
                               '알림 허용 후 🔔을 다시 눌러주세요.')
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
            try:   # ★ 프로세스가 강제종료돼도 OS가 만료 시 알림을 자동 제거 (API 26+)
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
                    b.addAction(ctx.getApplicationInfo().icon, '✕ 닫기', cpi)
                    _dlog('[알림] 액션 부착: ⟳ 지금갱신 / ✕ 닫기')
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
                                   '방금 열린 설정에서 허용한 뒤 📌을 다시 눌러주세요.')
        return 'not_declared', (f'{pkg} 앱이 오버레이 권한(SYSTEM_ALERT_WINDOW)을 '
                                '선언하지 않아 일반 설정으로는 허용할 수 없습니다. '
                                '알림(🔔) 방식을 사용하거나 ADB로 권한을 부여해야 합니다. '
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
        params = {'serviceKey': SERVICE_KEY, 'STAGE1': sido, 'STAGE2': gugun,
                  'pageNo': '1', 'numOfRows': '100'}
        resp = _http_get(API_URL, params=params, timeout=10)
        if resp.status_code != 200:
            return None
        root = ET.fromstring(resp.content)
        for item in root.findall('.//item'):
            if (item.findtext('hpid') or '').strip() != hpid:
                continue
            name  = (item.findtext('dutyName') or '').strip() or name_hint or hpid
            avail = safe_int(item.findtext('hvec'))
            t_raw = get_hvs(item, 'HVS01')
            prev  = _pip_bed_total_cache.get(hpid, {})
            if t_raw > 0:
                prev['hvec_t'] = t_raw
                total = t_raw
            else:
                total = prev.get('hvec_t', 0)
            _pip_bed_total_cache[hpid] = prev
            if avail < 0:
                return f'{name} 정보없음'
            if total > 0:
                pct = round(avail / total * 100)
                return f'{name} {avail}/{total}({pct}%)'
            return f'{name} {avail}/-'
        return None  # 응답에 해당 병원 없음
    except Exception as e:
        _dlog(f'[알림] 병상 조회 오류: {e}')
        return None


_MONITOR_CFG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'bed_monitor.json')


def _save_monitor_cfg():
    try:
        st = _bed_notify_state
        with open(_MONITOR_CFG_PATH, 'w', encoding='utf-8') as f:
            json.dump({'hospitals': st['hospitals'], 'iv': st['iv_sec'],
                       'mode': st['mode'], 'h': st.get('h_param', ''),
                       'ts': time.time()}, f, ensure_ascii=False)
    except Exception as e:
        _dlog(f'[알림] 설정 저장 실패(무시): {e}')


def _clear_monitor_cfg():
    try:
        if os.path.exists(_MONITOR_CFG_PATH):
            os.remove(_MONITOR_CFG_PATH)
    except Exception:
        pass


def _resume_monitor_if_saved():
    """앱 재시작(강제종료 포함) 후 이전 모니터 자동 복원 — 6시간 이내 설정만."""
    try:
        if not os.path.exists(_MONITOR_CFG_PATH):
            return
        with open(_MONITOR_CFG_PATH, encoding='utf-8') as f:
            cfg = json.load(f)
        if time.time() - float(cfg.get('ts', 0)) > 21600:
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
            b.setContentTitle('⚠️ 병상 모니터 중단')
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
            if not ce or age > max_age:
                all_ok = False
                continue
            name = ce.get('name') or h.get('name') or h['hpid']
            avail = ce.get('hvec', -1)
            avail = -1 if avail is None else avail
            total = ce.get('hvec_t', 0) or 0
            if avail < 0:
                out[h['hpid']] = f'{name} 정보없음'
            elif total > 0:
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
    url = 'http://127.0.0.1:5000/pip?h=' + quote(h_param, safe='')

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
        flags = (LayoutParams.FLAG_NOT_FOCUSABLE
                 | LayoutParams.FLAG_NOT_TOUCHABLE)
        p = LayoutParams(dp(300), dp(190), wtype, flags, PixelFormat.TRANSLUCENT)
        p.gravity = Gravity.TOP | Gravity.CENTER_HORIZONTAL
        p.x = 0
        p.y = dp(2)
        wm = ctx.getSystemService(Context.WINDOW_SERVICE)
        wm.addView(wv, p)
        _overlay_refs.update({'view': wv, 'wm': wm, 'params': p, 'web': True})

    ok, err = _run_on_ui(_create)
    if err or not ok:
        _dlog(f'[오버레이] pip 웹뷰 실패: {err or "UI 응답 없음"} → 텍스트 방식 폴백')
        _overlay_refs.update({'view': None, 'wm': None, 'params': None, 'web': False})
        return False
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
            params = {'serviceKey': SERVICE_KEY, 'STAGE1': sido, 'STAGE2': gugun,
                      'pageNo': '1', 'numOfRows': '100'}
            resp = _http_get(API_URL, params=params, timeout=10)
            if resp.status_code != 200:
                continue
            root = ET.fromstring(resp.content)
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
            avail = safe_int(item.findtext('hvec'))
            t_raw = get_hvs(item, 'HVS01')
            prev  = _pip_bed_total_cache.get(h['hpid'], {})
            if t_raw > 0:
                prev['hvec_t'] = t_raw
                total = t_raw
            else:
                total = prev.get('hvec_t', 0)
            _pip_bed_total_cache[h['hpid']] = prev
            if avail < 0:
                out[h['hpid']] = f'{name} 정보없음'
            elif total > 0:
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
                    big_text=('\n'.join(lines) + f'\n⏱ {ts} 갱신') if n > 1 else None,
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
            if (not kick.is_set()) and (time.time() - _t0) > wait + 90:
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
        hpid  = (h.get('hpid')  or '').strip()
        sido  = (h.get('sido')  or '').strip()
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
    nm = ', '.join(h['name'] for h in clean[:2]) + (' 외' if len(clean) > 2 else '')
    return jsonify({'ok': True, 'running': True,
                    'msg': f'{nm} {label} 시작 ({iv}초 주기)',
                    'warn': warn})


@flask_app.route('/api/bed_notify_close')
def api_bed_notify_close():
    """알림의 '✕ 닫기' 버튼 → 모니터 종료 + 알림 제거"""
    with _bed_notify_lock:
        was = _bed_notify_state['running']
        _stop_bed_notify()
        _clear_monitor_cfg()
    body = '✅ 병상 모니터를 종료했습니다.' if was else '실행 중인 병상 모니터가 없습니다.'
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
        body = '✅ 지금 갱신합니다. 이 창은 닫으셔도 됩니다.'
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
    hpid  = (request.args.get('hpid')  or '').strip()
    sido  = (request.args.get('sido')  or '').strip()
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
            return '<div style="padding:20px;color:red;">POST 처리 오류: 병원 목록을 다시 선택해주세요.</div>', 400

    # ─── GET ──────────────────────────────────────────────────────
    try:
        h_param = request.args.get('h', '').strip()

        if not h_param:
            old_hpids = request.args.get('hpids', '').strip()
            old_sido  = request.args.get('sido',  '').strip()
            old_gugun = request.args.get('gugun', '').strip()
            if old_hpids and old_sido and old_gugun:
                h_param = ','.join(
                    f'{hpid.strip()}|{old_sido}|{old_gugun}'
                    for hpid in old_hpids.split(',') if hpid.strip()
                )
                _log(f'[compare] 구버전 URL 변환: {h_param[:80]}')
            else:
                return ('<div style="padding:20px;color:#d32f2f;">❌ 오류: 병원 정보가 없습니다.'
                        ' 메인 페이지에서 병원을 선택해주세요.</div>'), 400

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
            return ('<div style="padding:20px;color:#d32f2f;">❌ 오류: 파싱 가능한 병원 정보가 없습니다.'
                    ' 메인 페이지에서 다시 선택해주세요.</div>'), 400
        if len(entries) > 5:
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
                params = {
                    'serviceKey': SERVICE_KEY,
                    'STAGE1': sido, 'STAGE2': gugun,
                    'pageNo': '1', 'numOfRows': '100'
                }
                api_resp = _http_get(API_URL, params=params, timeout=15)
                api_resp.raise_for_status()
                root = ET.fromstring(api_resp.content)
                result_code = root.findtext('.//resultCode')
                result_msg  = root.findtext('.//resultMsg', '')
                _log(f'[compare] [{sido} {gugun}] API: code={result_code}')

                if result_code != '00':
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
                                  if h['icu'][k]['total'] > 0)
                        _ce = any(h['icu'][k]['avail'] >= 0 for k in _icu_ks)
                        _now_str = datetime.now().strftime('%H:%M:%S')
                        with _compare_bed_cache_lock:
                            _compare_bed_cache[hpid] = {
                                # ★ FIX(2025-B1): name 추가 → pip_data 캐시 히트 시
                                #   HPID 코드 대신 한글 병원명 표시
                                'name':   h['name'],
                                'hvec':   h['emergency']['hvec']['avail'],
                                # ★ FIX(2025-B2): get_hvs가 태그 부재 시 -1 반환.
                                #   total 필드에 -1이 저장되면 pip_data의
                                #   양쪽합산 분기에서 hv36_t=-1이 0으로 취급되어
                                #   합계 총량이 실제보다 낮게(hvgc_t만) 표시됨.
                                #   max(0, v)로 "데이터 없음"을 0으로 정규화.
                                'hvec_t': max(0, h['emergency']['hvec']['total']),
                                'hvgc':   h['general']['hvgc']['avail'],
                                'hvgc_t': max(0, h['general']['hvgc']['total']),
                                'hv36':   h['general']['hv36']['avail'],
                                'hv36_t': max(0, h['general']['hv36']['total']),
                                'hicu':   _ca if _ce else -1,
                                'hicu_t': _ct,
                                'fetched_at': _now_str,
                            }
                        # ─────────────────────────────────────────────────────────

                # 목록 API로 분류 보완
                try:
                    list_params = {'serviceKey': SERVICE_KEY, 'Q0': sido, 'Q1': gugun,
                                   'pageNo': '1', 'numOfRows': '100'}
                    lr = _http_get(LIST_API_URL, params=list_params, timeout=8)
                    if lr.status_code == 200:
                        lr_root = ET.fromstring(lr.content)
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
                <div style="color:#d32f2f;background:#ffe0e0;padding:15px;border-radius:8px;margin-bottom:15px;">
                ❌ 데이터 로드 실패:<br>{err_detail}</div>
                <button onclick="location.reload()"
                  style="padding:10px 20px;background:#667eea;color:white;border:none;
                         border-radius:6px;cursor:pointer;font-size:1rem;">🔄 다시 시도</button>
                <button onclick="history.back()"
                  style="padding:10px 20px;background:#888;color:white;border:none;
                         border-radius:6px;cursor:pointer;font-size:1rem;margin-left:10px;">◀ 뒤로</button>
                </body></html>'''), 502

        # ── 예외상황 메시지 직접 조회 (수정1: HTTP 자기호출 제거) ──
        # [최적화] 지역 조회와 동시에 시작해 둔 future 결과를 수집한다.
        # 개별 실패 → '✅ 정상' 폴백은 기존 _fetch_messages_direct와 동일.
        try:
            msgs = {}
            for _hp, _fu in _msg_futures.items():
                try:
                    _hpid_res, _msg_res = _fu.result()
                    msgs[_hpid_res] = _msg_res
                except Exception as _fe:
                    print(f"병렬 메시지 조회 실패 ({_hp}): {_fe}")
                    msgs[_hp] = '✅ 정상'
            for h in hospitals_data:
                h['exception'] = msgs.get(h.get('hpid'), '✅ 정상')
            _log(f'[compare] 예외상황 메시지 수신 완료')
        except Exception as msg_err:
            _log(f'[compare] 예외상황 조회 실패 (무시): {msg_err}', 'ERROR')
            for h in hospitals_data:
                h.setdefault('exception', '✅ 정상')

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
            'title_font_size':      f'{lerp(1.95, 1.105, n):.2f}rem',  # +30% from original lerp(1.50, 0.85)
            'base_font_size':       f'{lerp(0.90, 0.58, n):.2f}rem',
            'table_font_size':      f'{lerp(0.92, 0.52, n):.2f}rem',
            'category_font_size':   f'{lerp(0.88, 0.52, n):.2f}rem',
            'label_font_size':      f'{lerp(0.88, 0.52, n):.2f}rem',
            'bed_number_font_size': f'{lerp(0.92, 0.56, n):.2f}em',
            'pct_font_size_large':  f'{lerp(0.88, 0.58, n):.2f}em',
            'exception_font_size':  f'{lerp(0.78, 0.48, n):.2f}em',
            'cell_padding':         '4px 2px',
            'bed_cell_padding':     '3px 2px',
            'bar_height':           '5px',
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
        # ★ 서버 사망 대비: 직접조회 폴백 엔진을 페이지에 내장
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
        'ct':                eq('hvctayn',     'hvs27'),
        'mri':               eq('hvmriayn',    'hvs28'),
        'angio':             eq('hvangioayn',  'hvs29'),
        'ventilator':        eq('hvventiayn',  'hvs30'),
        'ventilator_preemie':eq('hvventisoayn','hvs31'),
        'incubator':         eq('hvincuayn',   'hvs32'),
        'crrt':              eq('hvcrrtayn',   'hvs33'),
        'ecmo':              eq('hvecmoayn',   'hvs34'),
        'hypothermia':       eq('hvhypoayn',   'hvs35'),
        'hyperbaric':        eq('hvoxyayn',    'hvs37'),
    }
    # hv42(분만실)은 Y/N 또는 숫자/분수 형태 → raw 저장 (이슈4)
    hv42_raw = (item.findtext('hv42') or '').strip()
    return {
        'hpid':       item.findtext('hpid') or '',
        'name':       item.findtext('dutyName') or '알 수 없음',
        'dutyAddr':   item.findtext('dutyAddr') or '',
        'dutyTel1':   item.findtext('dutyTel1') or '',
        'dutyTel3':   item.findtext('dutyTel3') or '',
        'update_time':item.findtext('hvidate') or '',
        'emcls':      '',       # /api/hospitals 에서 채워짐
        'emclsName':  '',
        'level':      '기관',  # 기본값
        'emergency': {
            'hvec': {'avail': safe_int(item.findtext('hvec')), 'total': get_hvs(item, 'HVS01')},
            'hv28': {'avail': safe_int(item.findtext('hv28')), 'total': get_hvs(item, 'HVS02')},
            'hv29': {'avail': safe_int(item.findtext('hv29')), 'total': get_hvs(item, 'HVS03')},
            'hv30': {'avail': safe_int(item.findtext('hv30')), 'total': get_hvs(item, 'HVS04')},
        },
        'icu': {
            'hvicc': {'avail': safe_int(item.findtext('hvicc')), 'total': get_hvs(item, 'HVS17')},
            'hv2':   {'avail': safe_int(item.findtext('hv2')),   'total': get_hvs(item, 'HVS06')},
            'hv3':   {'avail': safe_int(item.findtext('hv3')),   'total': get_hvs(item, 'HVS07')},
            'hvncc': {'avail': safe_int(item.findtext('hvncc')), 'total': get_hvs(item, 'HVS08')},
            'hv32':  {'avail': safe_int(item.findtext('hv32')),  'total': get_hvs(item, 'HVS09')},
            'hvcc':  {'avail': safe_int(item.findtext('hvcc')),  'total': get_hvs(item, 'HVS11')},
            'hv6':   {'avail': safe_int(item.findtext('hv6')),   'total': get_hvs(item, 'HVS12')},
            'hv34':  {'avail': safe_int(item.findtext('hv34')),  'total': get_hvs(item, 'HVS15')},
            'hvccc': {'avail': safe_int(item.findtext('hvccc')), 'total': get_hvs(item, 'HVS16')},
            'hv35':  {'avail': safe_int(item.findtext('hv35')),  'total': get_hvs(item, 'HVS18')},
            'hv31':  {'avail': safe_int(item.findtext('hv31')),  'total': get_hvs(item, 'HVS05')},
            'hv33':  {'avail': safe_int(item.findtext('hv33')),  'total': get_hvs(item, 'HVS10')},
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
        'exception': '✅ 정상',
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
        nc = 'hospital-name very-long-name' if name_length > 20 else ('hospital-name long-name' if name_length > 12 else 'hospital-name')
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
                    html += f'<td class="equipment-cell equipment-available">{ec if ec > 0 else 1}</td>'
                else:
                    html += '<td class="equipment-cell equipment-unavailable" style="font-size:1.03em;">X</td>'
            html += '</tr>'

    # ── 중증질환 수용가능 (MKioskTy) ─────────────────────────────
    # 하나라도 데이터가 있는 항목만 행 표시; Y/정보미제공/불가능 + Msg 툴팁
    def _kiosk_cell_val(val_str):
        v = (val_str or '').strip()
        # '0' 포함: API가 0을 수용가능으로 반환하는 경우 → 굵은 O 표시
        if v in ('Y', 'Y         ', '0'):
            return 'ok', '<span style="font-weight:900;font-size:1.34em;color:#4CAF50;line-height:1;display:inline-block;">O</span>'
        if '불가' in v:
            # X: -20% (1.29em → 1.03em)
            return 'ng', '<span style="font-size:1.03em;color:#C62828;font-weight:700;line-height:1;">X</span>'
        if v == '정보미제공':
            return 'na', '<span style="color:#888;font-size:1.0em;">–</span>'
        if v:
            return 'partial', '<span style="color:#e65100;font-size:1.1em;">⚠️</span>'
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
    html += f'<tr><td colspan="{num_hospitals+1}" class="category-header">예외상황</td></tr>'
    html += '<tr><td class="item-label">예외상황</td>'
    for h in hospitals_data:
        exc = h.get('exception', '정보 없음')
        if exc.startswith('✅'):
            duty_inf_raw = (h.get('duty_inf') or '').strip()
            if duty_inf_raw:
                duty_lines = [s.strip() for s in duty_inf_raw.replace('，',',').split(',') if s.strip()]
                duty_html = '<div style="color:#5a6a7e;font-weight:700;margin-left:5px;">🏥 상시 운영 제한:</div>'
                for di in duty_lines:
                    duty_html += f'<div style="margin-left:10px;color:#5a6a7e;line-height:1.3;">{di}</div>'
                html += (f'<td class="exception-cell exception-warning" '
                         f'style="text-align:left;padding:6px;vertical-align:top;">{duty_html}</td>')
            else:
                html += ('<td class="exception-cell exception-ok">'
                         '✅ <span style="color:#000;font-weight:normal;">없음</span></td>')
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

            result = []
            if un_lines:
                result.append('<div style="color:#dc3545;font-weight:700;margin-left:5px;">★ 수용불가:</div>')
                for item in un_lines:
                    result.append(f'<div style="margin-left:10px;color:#dc3545;line-height:1.3;">{item}</div>')
            if av_lines:
                result.append('<div style="color:#28a745;font-weight:700;margin-left:5px;margin-top:5px;">★ 수용가능:</div>')
                for item in av_lines:
                    result.append(f'<div style="margin-left:10px;color:#28a745;line-height:1.3;">{item}</div>')
            if inq_lines:
                result.append('<div style="color:#e67e00;font-weight:700;margin-left:5px;margin-top:5px;">※ 문의 필요:</div>')
                for item in inq_lines:
                    result.append(f'<div style="margin-left:10px;color:#e67e00;line-height:1.3;">{item}</div>')
            if duty_inf_lines:
                result.append('<div style="color:#5a6a7e;font-weight:700;margin-left:5px;margin-top:5px;">🏥 상시 운영 제한:</div>')
                for item in duty_inf_lines:
                    result.append(f'<div style="margin-left:10px;color:#5a6a7e;line-height:1.3;">{item}</div>')

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
                f'        <div class="bar {bar_class}" style="width:100%"></div>\n'
                f'        <div class="bed-text-overlay {text_class}">{display}</div>\n'
                f'    </div></div></td>')

    # Y 계열 → 가능 (100%)
    if raw_up.startswith('Y'):
        total_str = f'/{total}' if total > 0 else ''
        display = f'가능{total_str}'
        return (f'<td class="bed-cell"><div class="bed-info"><div class="bar-container">\n'
                f'        <div class="bar bar-green" style="width:100%"></div>\n'
                f'        <div class="bed-text-overlay green-text">{display}</div>\n'
                f'    </div></div></td>')

    # N 계열 → 불가 (0%)
    if raw_up.startswith('N'):
        return (f'<td class="bed-cell"><div class="bed-info"><div class="bar-container">\n'
                f'        <div class="bar bar-red" style="width:0%"></div>\n'
                f'        <div class="bed-text-overlay red-text">불가</div>\n'
                f'    </div></div></td>')

    # 그 외 문자열 그대로 표시 (예: "가능")
    return (f'<td class="bed-cell"><div class="bed-info"><div class="bar-container">\n'
            f'        <div class="bar bar-green" style="width:100%"></div>\n'
            f'        <div class="bed-text-overlay green-text">{raw}</div>\n'
            f'    </div></div></td>')


def format_bed_cell(bed_data):
    avail = bed_data['avail']; total = bed_data['total']
    if avail == -1 and total <= 0:
        return '<td class="bed-cell"><div class="bed-info"><div class="bed-numbers" style="color:#000;">-</div></div></td>'
    if avail < 0:
        display_text = f"{avail}/{total} ({round(avail/total*100)}%)" if total > 0 else str(avail)
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
    if pct >= 50: bar_class, text_class = 'bar-green',  'green-text'
    elif pct >= 20: bar_class, text_class = 'bar-yellow', 'yellow-text'
    else:           bar_class, text_class = 'bar-red',    'red-text'
    display_text = f"{avail}/{total} ({pct}%)"
    return (f'<td class="bed-cell"><div class="bed-info"><div class="bar-container">\n'
            f'        <div class="bar {bar_class}" style="width:{bar_width}%"></div>\n'
            f'        <div class="bed-text-overlay {text_class}">{display_text}</div>\n'
            f'    </div></div></td>')




# ══════════════════════════════════════════════════════════════════
#  /pip  — 백그라운드 팝업용 미니 대시보드 (서버 렌더링 방식)
#  canvas/video PIP API 불필요; window.open() 팝업으로 표시됨.
# ══════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════
#  /pip  — 백그라운드 팝업용 미니 대시보드 (개선판)
#  변경: 열 정렬, 숫자/분모 표시, 가는 색상 바, 카운트다운 프로그레스
# ══════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════
#  💾 HTML 저장 (내보내기) — 서버(파이썬) 없이 단독 동작하는 조회화면
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
        <h1><span class="h1-main">응급의료상황판</span><span class="h1-sub">&nbsp;(🕐&nbsp;<span id="queryTime">--:--:--</span>&nbsp;기준)</span></h1>
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
            <label style="font-weight:normal;"><input type="checkbox" id="allowProxy" checked> 프록시 허용</label>
            <button id="miniBtn" title="항상 위 미니창">📺 미니창</button>
            <button id="miniStyleBtn" title="미니창 스타일">⚙</button>
            <button id="secBtn" title="표시 항목 설정">🧩</button>
            <span class="ex-status" id="exStatus">대기</span>
        </div>
        <div style="margin: 0 0 0 0; padding-top: 0;">
            <div class="bed-cell" style="max-width: 400px; margin: 0 auto; padding: 0;">
                <div class="bed-info">
                    <div class="bar-container" style="height: 10px; overflow: visible;">
                        <div class="bar bar-green" id="globalRefreshBar" style="width: 100%"></div>
                        <div class="bed-text-overlay green-text" id="globalRefreshOverlay"
                             style="font-size:0.60em;white-space:nowrap;overflow:visible;
                                    top:50%;transform:translate(-50%,-50%);line-height:1;">
                             ⏰ <span id="globalRefreshText">--:--</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <div id="exBody"><div class="ex-err" style="color:#667eea;">⏳ 병상 정보를 불러오는 중...</div></div>
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
        if (list && list.length > 0) return safeInt(list[0].textContent || '');
        return safeInt(txt(item, tagName.toLowerCase()) || nullIfMissing(item, tagName.toLowerCase()));
    }
    function nullIfMissing(item, tag) {            // 소문자 태그 부재 시 null → safeInt(-1)
        var l = item.getElementsByTagName(tag);
        return (l && l.length > 0) ? (l[0].textContent || '') : null;
    }
    function pyRound(x) {                          // 파이썬 round (은행가 반올림)
        var f = Math.floor(x), d = x - f;
        if (d < 0.5) return f;
        if (d > 0.5) return f + 1;
        return (f % 2 === 0) ? f : f + 1;
    }

    // ── 코드맵 (원본과 동일) ─────────────────────────────────────
    var D_CODE_MAP = {
        'D001': '내과',          'D002': '소아청소년과',   'D003': '신경과',
        'D004': '정신건강의학과', 'D005': '피부과',         'D006': '외과',
        'D007': '흉부외과',      'D008': '정형외과',       'D009': '신경외과',
        'D010': '성형외과',      'D011': '산부인과',       'D012': '안과',
        'D013': '이비인후과',    'D014': '비뇨기과',       'D016': '재활의학과',
        'D017': '마취통증의학과','D018': '영상의학과',     'D019': '치료방사선과',
        'D020': '임상병리과',    'D021': '해부병리과',     'D022': '가정의학과',
        'D023': '핵의학과',      'D024': '응급의학과',     'D026': '치과',
        'D034': '구강악안면외과'
    };
    var MKIOSK_MAP = {
        'MKioskTy1':  '응급실 수용',
        'MKioskTy2':  '[재관류중재술] 심근경색',
        'MKioskTy3':  '[재관류중재술] 뇌경색',
        'MKioskTy4':  '[뇌출혈수술] 거미막하출혈',
        'MKioskTy5':  '[뇌출혈수술] 거미막하출혈 외',
        'MKioskTy6':  '[대동맥응급] 흉부',
        'MKioskTy7':  '[대동맥응급] 복부',
        'MKioskTy8':  '[담낭담관질환] 담낭질환',
        'MKioskTy9':  '[담낭담관질환] 담도포함질환',
        'MKioskTy10': '[복부응급수술] 비외상',
        'MKioskTy11': '[장중첩/폐색] 영유아',
        'MKioskTy12': '[응급내시경] 성인 위장관',
        'MKioskTy13': '[응급내시경] 영유아 위장관',
        'MKioskTy14': '[응급내시경] 성인 기관지',
        'MKioskTy15': '[응급내시경] 영유아 기관지',
        'MKioskTy16': '[저출생체중아] 집중치료',
        'MKioskTy17': '[산부인과응급] 분만',
        'MKioskTy18': '[산부인과응급] 산과수술',
        'MKioskTy19': '[산부인과응급] 부인과수술',
        'MKioskTy20': '[중증화상] 전문치료',
        'MKioskTy21': '[사지접합] 수족지접합',
        'MKioskTy22': '[사지접합] 수족지접합 외',
        'MKioskTy23': '[응급투석] HD',
        'MKioskTy24': '[응급투석] CRRT',
        'MKioskTy25': '[정신과적응급] 폐쇄병동입원',
        'MKioskTy26': '[안과적수술] 응급',
        'MKioskTy27': '[영상의학혈관중재] 성인',
        'MKioskTy28': '[영상의학혈관중재] 영유아'
    };
    var Y_CODE_MAP = {
        'Y000':  '응급실',
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
        { name: '프록시(allorigins)',  wrap: function (u) { return 'https://api.allorigins.win/raw?url=' + encodeURIComponent(u); } },
        { name: '프록시(corsproxy)',   wrap: function (u) { return 'https://corsproxy.io/?url=' + encodeURIComponent(u); } }
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
            if (doc.getElementsByTagName('parsererror').length > 0) throw new Error('XML 파싱 실패');
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
    function setStatus(t) {
        try { document.getElementById('exStatus').textContent = t; } catch (e) {}
    }

    // ── 병원 데이터 파싱 (parse_hospital_data 이식) ──────────────
    function bedPair(item, availTag, totalTag) {
        return { avail: safeInt(txt2(item, availTag)), total: getHvs(item, totalTag) };
    }
    function txt2(item, tag) {                     // findtext: 부재 시 null 취급
        var l = item.getElementsByTagName(tag);
        return (l && l.length > 0) ? (l[0].textContent || '') : null;
    }
    function parseHospitalData(item) {
        function eq(ayn, cnt) {
            var avail = ((txt2(item, ayn) || 'N').toUpperCase()).indexOf('Y') === 0;
            return { available: avail, count: avail ? safeInt(txt2(item, cnt)) : 0 };
        }
        var equipment = {
            'ct':                 eq('hvctayn',     'hvs27'),
            'mri':                eq('hvmriayn',    'hvs28'),
            'angio':              eq('hvangioayn',  'hvs29'),
            'ventilator':         eq('hvventiayn',  'hvs30'),
            'ventilator_preemie': eq('hvventisoayn','hvs31'),
            'incubator':          eq('hvincuayn',   'hvs32'),
            'crrt':               eq('hvcrrtayn',   'hvs33'),
            'ecmo':               eq('hvecmoayn',   'hvs34'),
            'hypothermia':        eq('hvhypoayn',   'hvs35'),
            'hyperbaric':         eq('hvoxyayn',    'hvs37')
        };
        var hv42raw = (txt2(item, 'hv42') || '').trim();
        return {
            'hpid':        txt2(item, 'hpid') || '',
            'name':        txt2(item, 'dutyName') || '알 수 없음',
            'dutyAddr':    txt2(item, 'dutyAddr') || '',
            'dutyTel1':    txt2(item, 'dutyTel1') || '',
            'dutyTel3':    txt2(item, 'dutyTel3') || '',
            'update_time': txt2(item, 'hvidate') || '',
            'emcls':       '',
            'emclsName':   '',
            'level':       '기관',
            'emergency': {
                'hvec': bedPair(item, 'hvec', 'HVS01'),
                'hv28': bedPair(item, 'hv28', 'HVS02'),
                'hv29': bedPair(item, 'hv29', 'HVS03'),
                'hv30': bedPair(item, 'hv30', 'HVS04')
            },
            'icu': {
                'hvicc': bedPair(item, 'hvicc', 'HVS17'),
                'hv2':   bedPair(item, 'hv2',   'HVS06'),
                'hv3':   bedPair(item, 'hv3',   'HVS07'),
                'hvncc': bedPair(item, 'hvncc', 'HVS08'),
                'hv32':  bedPair(item, 'hv32',  'HVS09'),
                'hvcc':  bedPair(item, 'hvcc',  'HVS11'),
                'hv6':   bedPair(item, 'hv6',   'HVS12'),
                'hv34':  bedPair(item, 'hv34',  'HVS15'),
                'hvccc': bedPair(item, 'hvccc', 'HVS16'),
                'hv35':  bedPair(item, 'hv35',  'HVS18'),
                'hv31':  bedPair(item, 'hv31',  'HVS05'),
                'hv33':  bedPair(item, 'hv33',  'HVS10')
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
            'exception': '✅ 정상'
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
                    var label = resolveTypeLabel(mag, cod);
                    var cm = cleanMsg(blk);
                    if (label === '응급실' || label === '') label = '';
                    if (!cm && !label) continue;
                    var content = (label && cm) ? (label + ': ' + cm) : (cm ? cm : label);
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
            return '✅ 정상';
        } catch (e) { return '✅ 정상'; }
    }
    async function fetchBasicInfo(hpid) {
        try {
            var doc = await apiGet('getEgytBassInfoInqire', { HPID: hpid.trim(), numOfRows: '1' });
            return (txt(doc, 'dutyInf') || '').trim();
        } catch (e) { return ''; }
    }
    async function fetchKiosk(sido, gugun) {
        try {
            var doc = await apiGet('getSrsillDissAceptncPosblInfoInqire',
                                   { STAGE1: sido, STAGE2: gugun, numOfRows: '100', pageNo: '1' });
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
        return (l && l.length > 0) ? (l[0].textContent || '') : '';
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
            var dt = total > 0 ? (avail + '/' + total + ' (' + pyRound(avail / total * 100) + '%)') : String(avail);
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
        if (pct >= 50)      { bc = 'bar-green';  tc = 'green-text'; }
        else if (pct >= 20) { bc = 'bar-yellow'; tc = 'yellow-text'; }
        else                { bc = 'bar-red';    tc = 'red-text'; }
        var dtext = avail + '/' + total + ' (' + pct + '%)';
        return '<td class="bed-cell"><div class="bed-info"><div class="bar-container">\n'
             + '        <div class="bar ' + bc + '" style="width:' + bw + '%"></div>\n'
             + '        <div class="bed-text-overlay ' + tc + '">' + dtext + '</div>\n'
             + '    </div></div></td>';
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
                 + '        <div class="bar bar-green" style="width:100%"></div>\n'
                 + '        <div class="bed-text-overlay green-text">' + raw + '</div>\n'
                 + '    </div></div></td>';
        }
        if (rawUp.indexOf('Y') === 0) {
            var ts = total > 0 ? ('/' + total) : '';
            return '<td class="bed-cell"><div class="bed-info"><div class="bar-container">\n'
                 + '        <div class="bar bar-green" style="width:100%"></div>\n'
                 + '        <div class="bed-text-overlay green-text">가능' + ts + '</div>\n'
                 + '    </div></div></td>';
        }
        if (rawUp.indexOf('N') === 0) {
            return '<td class="bed-cell"><div class="bed-info"><div class="bar-container">\n'
                 + '        <div class="bar bar-red" style="width:0%"></div>\n'
                 + '        <div class="bed-text-overlay red-text">불가</div>\n'
                 + '    </div></div></td>';
        }
        return '<td class="bed-cell"><div class="bed-info"><div class="bar-container">\n'
             + '        <div class="bar bar-green" style="width:100%"></div>\n'
             + '        <div class="bed-text-overlay green-text">' + raw + '</div>\n'
             + '    </div></div></td>';
    }
    function kioskCellVal(valStr) {
        var v = (valStr || '').trim();
        if (v === 'Y' || v === 'Y         ' || v === '0')
            return ['ok', '<span style="font-weight:900;font-size:1.34em;color:#4CAF50;line-height:1;display:inline-block;">O</span>'];
        if (v.indexOf('불가') !== -1)
            return ['ng', '<span style="font-size:1.03em;color:#C62828;font-weight:700;line-height:1;">X</span>'];
        if (v === '정보미제공')
            return ['na', '<span style="color:#888;font-size:1.0em;">–</span>'];
        if (v) return ['partial', '<span style="color:#e65100;font-size:1.1em;">⚠️</span>'];
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
            var nc = nameLength > 20 ? 'hospital-name very-long-name'
                   : (nameLength > 12 ? 'hospital-name long-name' : 'hospital-name');
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
                    if (ea) html += '<td class="equipment-cell equipment-available">' + (ec > 0 ? ec : 1) + '</td>';
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
            var exc = h.exception === undefined ? '정보 없음' : h.exception;
            if (exc.indexOf('✅') === 0) {
                var dutyRaw = (h.duty_inf || '').trim();
                if (dutyRaw) {
                    var dl = dutyRaw.replace(/，/g, ',').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
                    var dh = '<div style="color:#5a6a7e;font-weight:700;margin-left:5px;">🏥 상시 운영 제한:</div>';
                    dl.forEach(function (di) { dh += '<div style="margin-left:10px;color:#5a6a7e;line-height:1.3;">' + di + '</div>'; });
                    html += '<td class="exception-cell exception-warning" style="text-align:left;padding:6px;vertical-align:top;">' + dh + '</td>';
                } else {
                    html += '<td class="exception-cell exception-ok">✅ <span style="color:#000;font-weight:normal;">없음</span></td>';
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
                if (un.length) {
                    res.push('<div style="color:#dc3545;font-weight:700;margin-left:5px;">★ 수용불가:</div>');
                    un.forEach(function (it) { res.push('<div style="margin-left:10px;color:#dc3545;line-height:1.3;">' + it + '</div>'); });
                }
                if (av.length) {
                    res.push('<div style="color:#28a745;font-weight:700;margin-left:5px;margin-top:5px;">★ 수용가능:</div>');
                    av.forEach(function (it) { res.push('<div style="margin-left:10px;color:#28a745;line-height:1.3;">' + it + '</div>'); });
                }
                if (inq.length) {
                    res.push('<div style="color:#e67e00;font-weight:700;margin-left:5px;margin-top:5px;">※ 문의 필요:</div>');
                    inq.forEach(function (it) { res.push('<div style="margin-left:10px;color:#e67e00;line-height:1.3;">' + it + '</div>'); });
                }
                if (dl2.length) {
                    res.push('<div style="color:#5a6a7e;font-weight:700;margin-left:5px;margin-top:5px;">🏥 상시 운영 제한:</div>');
                    dl2.forEach(function (it) { res.push('<div style="margin-left:10px;color:#5a6a7e;line-height:1.3;">' + it + '</div>'); });
                }
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
                var doc = await apiGet('getEmrrmRltmUsefulSckbdInfoInqire',
                                       { STAGE1: r.sido, STAGE2: r.gugun, pageNo: '1', numOfRows: '100' });
                var rc = txt(doc, 'resultCode');
                if (rc !== '00')
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
                    var ld = await apiGet('getEgytListInfoInqire',
                                          { Q0: r.sido, Q1: r.gugun, pageNo: '1', numOfRows: '100' });
                    items(ld).forEach(function (li) {
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
            try { msgs[hpids[i]] = await msgP[hpids[i]]; } catch (e) { msgs[hpids[i]] = '✅ 정상'; }
        }
        hd.forEach(function (h) { h.exception = msgs.hasOwnProperty(h.hpid) ? msgs[h.hpid] : '✅ 정상'; });
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

    // ── 지역 병원 목록 (/api/hospitals 동일 응답 형식) ────────────
    async function fetchRegionHospitals(sido, gugun) {
        if (!sido || !gugun) return { success: false, error: '시/도와 시/군/구를 입력해주세요.' };
        try {
            var doc = await apiGet('getEmrrmRltmUsefulSckbdInfoInqire',
                                   { STAGE1: sido, STAGE2: gugun, pageNo: '1', numOfRows: '100' });
            var rc = txt(doc, 'resultCode');
            var rm = txt(doc, 'resultMsg') || '알 수 없는 메시지';
            if (rc !== '00') return { success: false, error: 'API 오류 (' + rc + '): ' + rm };
            var hospitals = items(doc).map(parseHospitalData);
            try {
                var ld = await apiGet('getEgytListInfoInqire',
                                      { Q0: sido, Q1: gugun, pageNo: '1', numOfRows: '100' });
                var emap = {};
                items(ld).forEach(function (li) {
                    var hp = (txt(li, 'hpid') || '').trim();
                    if (hp) emap[hp] = [(txt(li, 'dutyEmcls') || '').trim(),
                                        (txt(li, 'dutyEmclsName') || '').trim()];
                });
                hospitals.forEach(function (h) {
                    var info = emap.hasOwnProperty(h.hpid) ? emap[h.hpid] : ['', ''];
                    h.emcls = info[0];
                    h.emclsName = info[1];
                    h.level = hospitalLevel(h.emcls, h.name);
                });
            } catch (le) { /* 목록 API 실패는 무시 (원본 동일) */ }
            return { success: true, hospitals: hospitals };
        } catch (e) {
            return { success: false, error: '서버 오류: ' + (e && e.message ? e.message : e) };
        }
    }

    return {
        cfg: cfg, parseXml: parseXml, setParseXml: function (f) { parseXml = f; },
        safeInt: safeInt, pyRound: pyRound,
        parseHospitalData: parseHospitalData, hospitalLevel: hospitalLevel,
        fetchHospitalMsgs: fetchHospitalMsgs, fetchBasicInfo: fetchBasicInfo, fetchKiosk: fetchKiosk,
        fetchRegionHospitals: fetchRegionHospitals,
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
            if (currentInterval > 0) {
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
                    document.getElementById('exBody').innerHTML = '<div class="ex-err">⚠️ ' + msg + '</div>';
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
                            var t = (b.total > 0) ? b.total : '?';
                            var nm = h.name.length > 5 ? h.name.slice(0, 5) : h.name;
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
                    '<div class="ex-err">⚠️ 갱신 실패: ' + (e && e.message ? e.message : e) + '</div>';
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
                if (textW > containerW - 2) {
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
                var cellW = tableW > 0 ? (tableW - firstColW) / numCols : 0;
                if (cellW > 0) {
                    document.querySelectorAll('.comparison-table td:not(.item-label):not(.category-header)').forEach(function (td) {
                        var inner = td.querySelector('.bed-numbers, .equipment-cell, .bed-cell');
                        var el = inner || td;
                        el.style.fontSize = '';
                        var curSz = parseFloat(window.getComputedStyle(el).fontSize);
                        if (el.scrollWidth > cellW + 4) {
                            var ratio = cellW / el.scrollWidth;
                            el.style.fontSize = Math.max(curSz * ratio * 0.95, 6) + 'px';
                        }
                    });
                }
            }
        }
        window.addEventListener('resize', function () { try { fitBedTexts(); } catch (e) {} });

        // ── 🧩 표시 항목 · 순서 설정 (py/저장본 공용, localStorage 영구 기억) ──
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
                        + '<div style="font-weight:700;margin-bottom:6px;">🧩 표시 항목 · 순서</div>'
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
                        if (i > 0) { var x = c.order[i]; c.order[i] = c.order[i - 1]; c.order[i - 1] = x; }
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

        // ── 📺 미니창(항상 위): PC=Document PiP, Android=canvas→video PiP ──
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
                { name: '검정',   s: { radius: 0,  border: '1px solid #555', bg: 'rgba(0,0,0,0.85)', bgSolid: '#000000', color: '#ffffff', weight: '700', fontSize: 44, opacity: 85 } },
                { name: '화이트', s: { radius: 10, border: '1px solid #bbb', bg: 'rgba(255,255,255,0.92)', bgSolid: '#f2f2f2', color: '#111111', weight: '700', fontSize: 44, opacity: 92 } },
                { name: '유리',   s: { radius: 14, border: '1px solid #9ec1d9', bg: 'rgba(210,230,245,0.55)', bgSolid: '#d7e6f2', color: '#0b2b45', weight: '700', fontSize: 44, opacity: 55 } },
                { name: '고대비', s: { radius: 0,  border: '2px solid #ffffff', bg: 'rgba(0,0,0,0.95)', bgSolid: '#000000', color: '#ffee00', weight: '800', fontSize: 48, opacity: 95 } },
                { name: '녹색',   s: { radius: 6,  border: '1px solid #00aa55', bg: 'rgba(0,40,25,0.85)', bgSolid: '#002819', color: '#4dff9d', weight: '700', fontSize: 44, opacity: 85 } }
            ];
            var presetIdx = 0;
            function styleGet(k) { return MINI_STYLE[k]; }
            function _miniKick() { try { doRefresh(); } catch (e) {} }
            var IV_CYCLE = [60000, 180000, 300000, 600000, 0];
            function _ivLabel() {
                var v = currentInterval;
                if (!(v > 0)) return '수동';
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
                        return 'rgba(' + ((v >> 16) & 255) + ',' + ((v >> 8) & 255) + ',' + (v & 255) + ',' + a + ')';
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
                                          t: (p && p.total > 0) ? p.total : 0 }; }
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
            function valTxt(a, t) { return (a < 0 ? '-' : a) + '/' + (t > 0 ? t : '-'); }
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
                if (!(currentInterval > 0) || !nextRefreshTime) {
                    bar.style.width = '100%'; cnt.textContent = '수동'; return;
                }
                var remain = Math.max(0, nextRefreshTime - Date.now());
                bar.style.width = Math.max(0, Math.min(100, remain / currentInterval * 100)) + '%';
                var s = Math.ceil(remain / 1000);
                cnt.textContent = Math.floor(s / 60) + ':' + ('0' + (s % 60)).slice(-2);
                var ib = d.getElementById('mnIv') || d.getElementById('lmIv');
                if (ib) ib.textContent = '⏱' + _ivLabel();
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
                        var p = (m.a >= 0 && m.t > 0) ? Math.min(1, m.a / m.t) : 0;
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
                 + 'background:#fff;color:#000;font-weight:800;padding:1px 6px;cursor:pointer;">⏱'
                 + _ivLabel() + '</button>'
                 + '<button id="mnRef" title="즉시 갱신" style="border:1px solid #000;background:#fff;'
                 + 'color:#000;font-weight:800;padding:1px 8px;cursor:pointer;">⟳</button>'
                 + '<button id="mnCls" title="닫기" style="border:1px solid #000;background:#fff;'
                 + 'color:#000;font-weight:800;padding:1px 8px;cursor:pointer;">✕</button></div>'
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
                    while (nm.length > 2 && g.measureText(nm).width > CW - padX * 2) nm = nm.slice(0, -1);
                    g.fillText(nm, padX, y0 + rowH * 0.04);   // 병원명 = 병상정보 위
                    var barH = Math.max(10, rowH * 0.15);
                    var barY = y0 + rowH * 0.44;
                    var labY = barY + barH + Math.max(4, rowH * 0.05);
                    ms.forEach(function (m, k) {              // 응급·입원·중환 = 고정 3열
                        var x = padX + colW * k, w = colW - gap;
                        var pr = ratioPair(m.a, m.t);
                        var p = (m.a >= 0 && m.t > 0) ? Math.min(1, m.a / m.t) : 0;
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
                var remainMs = (currentInterval > 0 && nextRefreshTime)
                    ? Math.max(0, nextRefreshTime - Date.now()) : 0;
                var pct = (currentInterval > 0)
                    ? Math.max(0, Math.min(1, remainMs / currentInterval)) : 1;
                var by = CH - hFoot + 8;
                g.fillStyle = 'rgba(255,255,255,0.22)';
                g.fillRect(26, by, CW - 52, 12);
                g.fillStyle = MINI_STYLE.color;
                g.fillRect(26, by, (CW - 52) * pct, 12);
                var sL = Math.ceil(remainMs / 1000);
                var cdt = (currentInterval > 0)
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
                            if (video.videoWidth > 0 || Date.now() - t0 > 800) res();
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
                                _miniKick();                        // ⏯ = 즉시 갱신
                            });
                            navigator.mediaSession.setActionHandler('nexttrack', function () { cyclePreset(1); });          // ⏭ = 디자인
                            navigator.mediaSession.setActionHandler('previoustrack', function () { cycleMainInterval(); }); // ⏮ = 주기
                            if (window.MediaMetadata)
                                navigator.mediaSession.metadata = new MediaMetadata(
                                    { title: '병상 미니창', artist: '⏯갱신 · ⏮주기 · ⏭디자인 · 크기=핀치' });
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
                    + '<div style="font-weight:700;margin-bottom:6px;">📺 미니창 스타일</div>'
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
        'title_font_size':      f'{_export_lerp(1.95, 1.105, n):.2f}rem',
        'base_font_size':       f'{_export_lerp(0.90, 0.58, n):.2f}rem',
        'table_font_size':      f'{_export_lerp(0.92, 0.52, n):.2f}rem',
        'category_font_size':   f'{_export_lerp(0.88, 0.52, n):.2f}rem',
        'label_font_size':      f'{_export_lerp(0.88, 0.52, n):.2f}rem',
        'bed_number_font_size': f'{_export_lerp(0.92, 0.56, n):.2f}em',
        'pct_font_size_large':  f'{_export_lerp(0.88, 0.58, n):.2f}em',
        'exception_font_size':  f'{_export_lerp(0.78, 0.48, n):.2f}em',
        'cell_padding':         '4px 2px',
        'bed_cell_padding':     '3px 2px',
        'bar_height':           '5px',
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


def _export_engine_js(parent_cfg):
    """마커 사이 엔진 코드 추출 + 부모 페이지용 설정 주입.
    (JSON 이스케이프 사본과 구분하기 위해 마커 뒤 실제 개행을 요구)"""
    import re as _re
    _pat = _re.escape('/*EX-ENGINE-START*/') + '\n(.*?)' + _re.escape('/*EX-ENGINE-END*/')
    m = _re.search(_pat, EXPORT_HTML_SHELL, _re.S)
    if not m:
        raise RuntimeError('export: 엔진 마커 추출 실패')
    return m.group(1).replace('__CONFIG__', json.dumps(parent_cfg, ensure_ascii=False))


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


def _build_full_export(auto_entries, iv_ms):
    """완전판 저장본: 병원 선택 + 비교 조회를 단일 HTML로.
    선택 화면은 실제 서비스 페이지를 렌더링한 뒤 서버 접점 2곳만 교체하고,
    비교 화면은 검증된 저장본 문서를 iframe(srcdoc)으로 띄운다.
    auto_entries 가 있으면 열자마자 해당 구성으로 비교 화면 자동 진입."""
    gen = datetime.now().strftime('%Y-%m-%d %H:%M')

    # ① 비교 문서 템플릿 (폰트/설정은 선택 시점에 JS가 채움)
    compare_tpl = EXPORT_HTML_SHELL
    compare_tpl = compare_tpl.replace('__CSS__', _export_css_template())
    compare_tpl = compare_tpl.replace('__CONFIG__', '"__EXCFG__"')
    compare_tpl = compare_tpl.replace('__GENERATED__', gen)

    # ② 선택 화면 렌더링 + 서버 접점 수술
    sel = _render_cached(HTML, districts=DISTRICTS)
    _srv_fetch = (
        "                const response = await fetch(`/api/hospitals?sido=${encodeURIComponent(sido)}&gugun=${encodeURIComponent(gugun)}`);\n"
        "                try { responseText = await response.text(); } catch(e) { responseText = '응답 읽기 실패'; }\n"
        "                if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}\\n${responseText}`);\n"
        "                let data;\n"
        "                try { data = JSON.parse(responseText); } catch(e) { throw new Error(`JSON 파싱 오류: ${e.message}`); }"
    )
    assert sel.count(_srv_fetch) == 1, 'export: fetch 접점 불일치'
    sel = sel.replace(_srv_fetch,
        "                const data = await EXAPP.getHospitals(sido, gugun);")

    _srv_open = "window.open('/compare?h=' + encodeURIComponent(hParam), '_blank');"
    assert sel.count(_srv_open) == 1, 'export: compare 이동 접점 불일치'
    sel = sel.replace(_srv_open, "EXAPP.openCompare(hParam);")

    # 저장본 내부의 저장 버튼은 제거
    sel = sel.replace('\n        <button class="btn" id="saveAppBtn" style="margin-top:8px;background:linear-gradient(135deg,#556b8d,#3a4d6b);">💾 저장 (단독 HTML — 선택+조회)</button>', '')
    sel = sel.replace("\n        try { document.getElementById('saveAppBtn').onclick = () => { location.href = '/export'; }; } catch(e) {}", '')

    # ③ 부모 글루 (엔진 + 비교문서 빌더 + 오버레이 iframe + 제목 릴레이)
    engine_js = _export_engine_js({'entries': [], 'serviceKey': SERVICE_KEY, 'iv': 0})
    size_sets = {str(n): _export_sizes(n) for n in (1, 2, 3, 4)}
    tpl_json = json.dumps(compare_tpl, ensure_ascii=False).replace('</', '<\\/')
    glue = (
        '\n<script>\n/*EX-ENGINE-START*/\n' + engine_js + '/*EX-ENGINE-END*/\n</script>\n'
        '<script>\n'
        'const EXAPP = (function () {\n'
        "    'use strict';\n"
        '    const COMPARE_TPL = ' + tpl_json + ';\n'
        '    const SIZE_SETS = ' + json.dumps(size_sets, ensure_ascii=False) + ';\n'
        '    const AUTO = ' + json.dumps(auto_entries if auto_entries else None, ensure_ascii=False) + ';\n'
        '    const AUTO_IV = ' + str(int(iv_ms)) + ';\n'
        '    const GEN = ' + json.dumps(gen, ensure_ascii=False) + ';\n'
        '    const BASE_TITLE = document.title;\n'
        '    let overlay = null;\n'
        '    function buildDoc(entries, iv) {\n'
        '        let doc = COMPARE_TPL;\n'
        "        const s = SIZE_SETS[String(Math.min(4, Math.max(1, entries.length)))];\n"
        "        Object.keys(s).forEach(k => { doc = doc.split('{{ ' + k + ' }}').join(s[k]); });\n"
        '        const cfg = { entries: entries, serviceKey: EX.cfg.serviceKey, iv: iv, generated: GEN };\n'
        '        doc = doc.replace(\'"__EXCFG__"\', JSON.stringify(cfg).replace(/</g, \'\\\\u003c\'));\n'
        '        return doc;\n'
        '    }\n'
        '    function openCompare(hParam) {\n'
        '        const entries = [];\n'
        "        String(hParam || '').split(',').forEach(t => {\n"
        "            const p = t.split('|');\n"
        '            if (p.length >= 3 && p[0].trim())\n'
        '                entries.push({ hpid: p[0].trim(), sido: p[1].trim(), gugun: p[2].trim() });\n'
        '        });\n'
        "        if (!entries.length) { alert('병원 구성이 없습니다.'); return; }\n"
        '        closeCompare();\n'
        "        overlay = document.createElement('div');\n"
        "        overlay.style.cssText = 'position:fixed;inset:0;z-index:99998;background:#f5f7fa;';\n"
        "        const back = document.createElement('button');\n"
        "        back.textContent = '↩ 병원선택';\n"
        "        back.style.cssText = 'position:fixed;top:6px;left:6px;z-index:99999;padding:6px 12px;border:none;border-radius:14px;background:rgba(30,30,30,0.75);color:#fff;font-size:0.8rem;cursor:pointer;';\n"
        '        back.onclick = closeCompare;\n'
        "        const fr = document.createElement('iframe');\n"
        "        fr.style.cssText = 'width:100%;height:100%;border:none;display:block;';\n"
        "        fr.setAttribute('allow', 'picture-in-picture');\n"
        '        fr.srcdoc = buildDoc(entries, AUTO_IV);\n'
        '        overlay.appendChild(fr);\n'
        '        overlay.appendChild(back);\n'
        '        document.body.appendChild(overlay);\n'
        '    }\n'
        '    function closeCompare() {\n'
        '        if (overlay) { overlay.remove(); overlay = null; }\n'
        '        try { document.title = BASE_TITLE; } catch (e) {}\n'
        '    }\n'
        "    window.addEventListener('message', function (e) {\n"
        '        if (e && e.data && e.data.exTitle) { try { document.title = e.data.exTitle; } catch (err) {} }\n'
        '    });\n'
        '    if (AUTO && AUTO.length) setTimeout(function () {\n'
        "        openCompare(AUTO.map(e => e.hpid + '|' + e.sido + '|' + e.gugun).join(','));\n"
        '    }, 50);\n'
        '    return { getHospitals: function (s, g) { return EX.fetchRegionHospitals(s, g); },\n'
        '             openCompare: openCompare, closeCompare: closeCompare, buildDoc: buildDoc };\n'
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
    'body{background:#1a1a2e;color:#e0e0e0;'
    '     font-family:"Malgun Gothic","Apple SD Gothic Neo",sans-serif;'
    '     font-size:13px;padding:6px;}'
    '.hdr{background:linear-gradient(135deg,#5b21b6,#4c1d95);padding:7px 10px;'
    '     border-radius:6px;margin-bottom:4px;display:flex;'
    '     justify-content:space-between;align-items:center;}'
    '.hdr-title{font-weight:700;font-size:14px;}'
    '.hdr-time{font-size:11px;color:#a78bfa;}'
    '.cbar-wrap{height:5px;background:#1e3a1e;border-radius:3px;margin-bottom:5px;overflow:hidden;}'
    '.cbar-fill{height:100%;background:#2d6a2d;border-radius:3px;transition:width 1s linear;}'
    'table{width:100%;border-collapse:collapse;table-layout:fixed;}'
    'th{background:#0f3460;color:#a78bfa;padding:5px 1px;font-size:11px;'
    '   font-weight:600;text-align:left;position:sticky;top:0;}'
    'th.nc{text-align:left;padding-left:4px;width:34%;}'
    'th.vc{width:22%;}'
    'td{padding:3px 1px;border-bottom:1px solid #16213e;vertical-align:middle;}'
    'td.n{font-size:11px;padding-left:4px;word-break:keep-all;}'
    'td.v{text-align:left;padding-left:3px;width:22%;}'
    'td.ok .vn{color:#4CAF50;}td.wn .vn{color:#FFA726;}td.bd .vn{color:#E53935;}'
    'td.no .vn{color:#555;}'
    '.vn{font-weight:700;font-size:12px;display:block;white-space:nowrap;}'
    '.vb{height:5px;border-radius:2px;overflow:hidden;margin-top:2px;display:flex;width:100%;}'
    '.bfa{height:100%;}'
    '.bfu{height:100%;opacity:0.25;}'
    '.ctrl{display:flex;gap:5px;margin-bottom:5px;flex-wrap:wrap;align-items:center;}'
    '.ctrl label{font-size:11px;color:#a78bfa;}'
    '.ctrl select{background:#16213e;color:#e0e0e0;border:1px solid #5b21b6;'
    '             border-radius:4px;padding:2px 4px;font-size:11px;}'
    '.ctrl button{background:#5b21b6;color:white;border:none;border-radius:4px;'
    '             padding:3px 8px;font-size:11px;cursor:pointer;}'
    '.ctrl button:hover{background:#6d28d9;}'
    '.st{font-size:10px;color:#a78bfa;margin-left:auto;}'
    '</style>'
    '</head>'
    '<body>'
    '<div class="hdr">'
    '<span class="hdr-title">&#9197; 응급실 모니터</span>'
    '<span class="hdr-time" id="ut">로드 중...</span>'
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
    '  var s=Math.round(IVI/1000),sel=document.getElementById("iv");'
    '  var best=null,bd=Infinity;'
    '  for(var i=0;i<sel.options.length;i++){'
    '    var d=Math.abs(parseInt(sel.options[i].value)-s);'
    '    if(d<bd){bd=d;best=i;}'
    '  }'
    '  if(best!==null)sel.selectedIndex=best;'
    '})();'
    'var _t=null,_ct=null,_na=0,_iv=0;'
    'function vc(a,t){if(a<0)return "bd";if(t<=0)return a>0?"ok":a===0?"bd":"no";'
    '  var p=a/t;return p>=0.5?"ok":p>=0.2?"wn":"bd";}'
    'function cell(a,t){'
    '  if(a===-1&&t<=0)return "<td class=\'v no\'><span class=vn>-</span><div class=vb></div></td>";'
    '  var l=t>0?a+"/"+t:String(a);'
    '  var st=vc(a,t);'
    '  var bc=st==="ok"?"#4CAF50":st==="wn"?"#FFA726":"#E53935";'
    '  var wA=t>0?Math.min(100,Math.round(a/t*100)):(a>0?100:0);'
    '  var wU=100-wA;'
    '  return "<td class=\'v "+st+"\'><span class=vn>"+l+"</span>"'
    '       +"<div class=vb><div class=bfa style=\'width:"+wA+"%;background:"+bc+"\'></div>"'
    '       +"<div class=bfu style=\'width:"+wU+"%;background:"+bc+"\'></div></div></td>";'
    '}'
    'function go(){'
    '  if(!HP){'
    '    document.getElementById("tw").innerHTML="<p style=\'color:#f55;padding:8px\'>h 파라미터 없음</p>";'
    '    return;'
    '  }'
    '  fetch("/pip_data?h="+encodeURIComponent(HP)+"&_t="+Date.now(),{cache:"no-cache"})'
    '    .then(function(r){return r.json();})'
    '    .then(function(d){'
    '      document.getElementById("ut").textContent=d.fetched_at||"";'
    '      var rs=d.hospitals||[];'
    '      if(!rs.length){'
    '        document.getElementById("tw").innerHTML="<p style=\'color:#aaa;padding:8px\'>데이터 없음</p>";'
    '        return;'
    '      }'
    '      var h="<table><thead><tr>"'
    '           +"<th class=nc>병원</th>"'
    '           +"<th class=vc>응급실</th>"'
    '           +"<th class=vc>중환자</th>"'
    '           +"<th class=vc>입원</th>"'
    '           +"</tr></thead><tbody>";'
    '      rs.forEach(function(r){'
    '        h+="<tr><td class=n>"+r.name+"</td>"'
    '          +cell(r.hvec,r.hvec_t)+cell(r.hvicc,r.hvicc_t)+cell(r.hvgc,r.hvgc_t)+"</tr>";'
    '      });'
    '      document.getElementById("tw").innerHTML=h+"</tbody></table>";'
    '    })'
    '    .catch(function(e){'
    '      document.getElementById("tw").innerHTML="<p style=\'color:#f55;padding:8px\'>오류: "+e.message+"</p>";'
    '    });'
    '}'
    'function updateBar(){'
    '  if(_iv<=0)return;'
    '  var rem=Math.max(0,_na-Date.now());'
    '  var pct=Math.min(100,Math.round((_iv-Math.max(0,_na-Date.now()))/_iv*100));'
    '  document.getElementById("cbf").style.width=pct+"%";'
    '  var s=Math.round(rem/1000);'
    '  document.getElementById("ct").textContent='
    '    "다음 "+Math.floor(s/60)+":"+(("0"+s%60).slice(-2));'
    '}'
    'function st(){'
    '  clearInterval(_t);clearInterval(_ct);'
    '  _iv=parseInt(document.getElementById("iv").value)*1000;'
    '  document.getElementById("ct").textContent="";'
    '  document.getElementById("cbf").style.width="0%";'
    '  if(_iv<=0)return;'
    '  _na=Date.now()+_iv;'
    '  _ct=setInterval(updateBar,1000);'
    '  _t=setInterval(function(){go();_na=Date.now()+_iv;},_iv);'
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
                            # ★ FIX: entries는 hpid|sido|gugun 만 가짐 → e2['name'] → KeyError.
                            # KeyError는 next()의 default를 우회하고 외부 except로 전달되어
                            # hospitals=[] 를 반환하는 silent 오류를 일으킨다.
                            # 캐시 엔트리에 저장된 name을 우선 사용, 없으면 hpid로 대체.
                            'name':   _ce.get('name', _hpid),
                            'hvec':   _ce['hvec'],   'hvec_t': _ce['hvec_t'],
                            'hvgc':   _ce['hvgc'],   'hvgc_t': _ce['hvgc_t'],
                            'hv36':   _ce['hv36'],   'hv36_t': _ce['hv36_t'],
                            'hicu':   _ce['hicu'],   'hicu_t': _ce['hicu_t'],
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
                params = {
                    'serviceKey': SERVICE_KEY,
                    'STAGE1': sido, 'STAGE2': gugun,
                    'pageNo': '1', 'numOfRows': '100',
                }
                resp = _http_get(API_URL, params=params, timeout=10)
                if resp.status_code != 200:
                    return local_results
                root_el = ET.fromstring(resp.content)
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
                        if _hvec_t_raw > 0:
                            _prev['hvec_t'] = _hvec_t_raw
                            _hvec_t = _hvec_t_raw
                        else:
                            _hvec_t = _prev.get('hvec_t', 0)
                        _dlog(f'[pip_data][HVS] {hpid} HVS01(응급합계): '
                              f'raw={_hvec_t_raw} → 사용={_hvec_t} '
                              f'({"신규" if _hvec_t_raw > 0 else "캐시폴백" if _hvec_t > 0 else "캐시없음"})')

                        _hvgc_t_raw = get_hvs(item, 'HVS38')
                        if _hvgc_t_raw > 0:
                            _prev['hvgc_t'] = _hvgc_t_raw
                            _hvgc_t = _hvgc_t_raw
                        else:
                            _hvgc_t = _prev.get('hvgc_t', 0)
                        _dlog(f'[pip_data][HVS] {hpid} HVS38(일반입원합계): '
                              f'raw={_hvgc_t_raw} → 사용={_hvgc_t} '
                              f'({"신규" if _hvgc_t_raw > 0 else "캐시폴백" if _hvgc_t > 0 else "캐시없음"})')

                        _hv36_t_raw = get_hvs(item, 'HVS19')
                        if _hv36_t_raw > 0:
                            _prev['hv36_t'] = _hv36_t_raw
                            _hv36_t = _hv36_t_raw
                        else:
                            _hv36_t = _prev.get('hv36_t', 0)
                        _dlog(f'[pip_data][HVS] {hpid} HVS19(응급전용입원합계): '
                              f'raw={_hv36_t_raw} → 사용={_hv36_t} '
                              f'({"신규" if _hv36_t_raw > 0 else "캐시폴백" if _hv36_t > 0 else "캐시없음"})')

                        if _icu_total > 0:
                            _prev['hicu_t'] = _icu_total
                        else:
                            _icu_total = _prev.get('hicu_t', 0)
                        _dlog(f'[pip_data][ICU] {hpid} '
                              f'avail={_icu_avail} total={_icu_total} any={_icu_any} '
                              f'({"신규" if _icu_total > 0 else "캐시폴백" if _prev.get("hicu_t", 0) > 0 else "캐시없음"})')

                        _pip_bed_total_cache[hpid] = _prev

                        _raw_hvgc = safe_int(item.findtext('hvgc'))
                        _raw_hv36 = safe_int(item.findtext('hv36'))
                        _dlog(f'[pip_data] {hpid} hvgc={_raw_hvgc}(t={_hvgc_t}) '
                              f'hv36={_raw_hv36}(t={_hv36_t}) '
                              f'hvec={safe_int(item.findtext("hvec"))}(t={_hvec_t})')
                        local_results[hpid] = {
                            'name':   (item.findtext('dutyName') or '').strip(),
                            'hvec':   safe_int(item.findtext('hvec')),
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
                        # ★ FIX(2025): pip_data API 결과를 _compare_bed_cache에 역기록
                        # /compare 방문 없이도 다음 pip_data 호출에서 캐시 히트 가능.
                        # name도 함께 저장해 캐시 읽기 경로의 KeyError를 방지한다.
                        _now_str = datetime.now().strftime('%H:%M:%S')
                        with _compare_bed_cache_lock:
                            _compare_bed_cache[hpid] = {
                                'name':   (item.findtext('dutyName') or '').strip(),
                                'hvec':   safe_int(item.findtext('hvec')),
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
        # ★ FIX(2025): 전체 예외를 HTTP 500 대신 200+빈 결과로 반환.
        # _dlog 로 Kivy 디버그 패널에도 표시 → 원인 추적 가능.
        _tb = traceback.format_exc()
        _log(f'[pip_data] 전체 예외: {_ex}\n{_tb}', 'ERROR')
        _dlog(f'[pip_data] ★예외★ {_ex}')   # 디버그 패널에 즉시 표시
        return jsonify({
            'hospitals':  [],
            'fetched_at': datetime.now().strftime('%H:%M:%S'),
            'error':      str(_ex),
            'ts':         _refresh_notify_ts[0],
        }), 200


@flask_app.route('/api/enter_pip', methods=['POST'])
def api_enter_pip():
    """브라우저 📺 버튼 → Flask → Kivy PiP 요청."""
    data = request.get_json(silent=True) or {}
    _pip_state['h_param'] = data.get('h', '')
    try:
        _pip_state['iv_sec'] = max(30, int(data.get('iv', 180)))
    except (ValueError, TypeError):
        _pip_state['iv_sec'] = 180
    _pip_state['pending'] = True
    _dlog(f'[api/enter_pip] 수신: h={_pip_state["h_param"][:30]} iv={_pip_state["iv_sec"]}s')

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
            _dlog('[api/enter_pip] startActivity 성공 → on_resume 대기')
        except Exception as _je:
            _dlog(f'[api/enter_pip] startActivity 실패: {_je}')
            _dlog('[api/enter_pip] → Clock._check_pip_request 폴백으로 처리')

    return jsonify({'ok': True})


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
<h3 style="color:#9cdcfe;margin:0 0 8px 0;font-size:0.9rem;">🔗 Compare 라우트 테스트</h3>
<p style="color:#aaa;font-size:0.8rem;margin-bottom:8px;">
아래 URL로 compare 페이지를 직접 테스트할 수 있습니다 (GET 방식):<br>
<code style="color:#4ec9b0;">/compare?h=HPID1|시도|시군구,HPID2|시도|시군구</code>
</p>
</div>'''

    return render_template_string('''
<!DOCTYPE html><html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>디버그 로그</title>
<style>
body{font-family:monospace;background:#1e1e1e;color:#d4d4d4;margin:0;padding:0;}
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
  <h2>🔍 디버그 로그 — {{ log_file }}</h2>
  <div style="display:flex;gap:8px;">
    <a class="btn" href="/debug">🔄 새로고침</a>
    <a class="btn" href="/">🏠 홈</a>
  </div>
</div>
<div style="padding:12px;">{{ diag|safe }}</div>
<pre id="log">{{ content }}</pre>
<script>
const pre = document.getElementById('log');
pre.innerHTML = pre.textContent
  .split('\\n')
  .map(l => l.includes('[ERROR]') || l.includes('ERROR') ? `<span class="err">${l}</span>`
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

                sym_blk_msg     = all_tags.get('symBlkMsg',     '').strip()
                sym_typ_cod_mag = all_tags.get('symTypCodMag',  '').strip()
                sym_typ_cod     = all_tags.get('symTypCod',     '').strip()
                sym_out_dsp_yon = all_tags.get('symOutDspYon',  '').strip()
                sym_blk_msg_typ = all_tags.get('symBlkMsgTyp',  '').strip()

                # 처리 결과 시뮬레이션
                label_raw   = _resolve_type_label(sym_typ_cod_mag, sym_typ_cod)
                clean_msg   = _clean_msg(sym_blk_msg)
                label_final = '' if label_raw in ('응급실', '') else label_raw
                skipped     = (not clean_msg and not label_final)
                cat         = _categorize_exception(label_final, clean_msg) if not skipped else '(건너뜀)'
                if skipped:
                    row_bg = 'background:#3d1f1f'
                    status = '⛔ SKIP'
                else:
                    row_bg = 'background:#1e2a1e'
                    status = '✅ OK'

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
<style>
body{{font-family:monospace;background:#1e1e1e;color:#d4d4d4;margin:0;padding:0;}}
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
  <h2>🔬 메시지 API 원본 덤프 — HPID: {_html.escape(hpid)} ({total_items}건)</h2>
  <a class="btn" href="/debug/msgs/{_html.escape(hpid)}">🔄 갱신</a>
  <a class="btn" href="/debug">📋 일반로그</a>
  <a class="btn" href="/">🏠 홈</a>
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
<title>🔬 API 디버그 도구</title>
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

<h2>🔬 API 디버그 도구 — 병원 검색 + 메시지 원본 추출</h2>

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
  <button class="btn" onclick="searchHospital()">🔍 병원 검색</button>
  <div id="result"></div>
</div>

<div style="background:#252526;padding:14px;border-radius:8px;">
  <h3>② HPID로 메시지 API 원본 직접 조회</h3>
  <div class="row">
    <input id="direct_hpid" placeholder="HPID 직접 입력 (예: C1300020)" style="flex:1;">
    <button class="btn btn-green" onclick="fetchMsgs()">📡 API 원본 가져오기</button>
    <button class="btn btn-copy" onclick="copyAll()">📋 전체 복사</button>
  </div>
  <div id="fetchStatus"></div>
  <textarea id="output" placeholder="여기에 원본 데이터가 출력됩니다..."></textarea>
</div>

<script>
async function searchHospital() {
  const sido  = document.getElementById('sido').value.trim();
  const gugun = document.getElementById('gugun').value.trim();
  const name  = document.getElementById('hname').value.trim().toLowerCase();
  const res   = document.getElementById('result');
  if (!sido) { res.innerHTML='<div class="status err">시/도를 입력하세요</div>'; return; }
  res.innerHTML = '<div class="status loading">🔄 검색 중...</div>';
  try {
    const r = await fetch(`/api/hospitals?sido=${encodeURIComponent(sido)}&gugun=${encodeURIComponent(gugun)}`);
    const data = await r.json();
    if (!data.success) { res.innerHTML=`<div class="status err">오류: ${data.error}</div>`; return; }
    const filtered = name
      ? data.hospitals.filter(h => h.name.toLowerCase().includes(name))
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
  stat.innerHTML = '<div class="status loading">🔄 API 조회 중...</div>';
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
    stat.innerHTML = `<div class="status ok">✅ ${data.total}건 로드 완료 — 아래 내용을 복사하세요</div>`;
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
      .then(()=>{ document.getElementById('fetchStatus').innerHTML='<div class="status ok">✅ 클립보드에 복사됨</div>'; })
      .catch(()=>{ document.execCommand('copy'); document.getElementById('fetchStatus').innerHTML='<div class="status ok">✅ 복사됨</div>'; });
  } catch(e) {
    document.execCommand('copy');
    document.getElementById('fetchStatus').innerHTML='<div class="status ok">✅ 복사됨</div>';
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
    'getEmrrmRltmUsefulSckbdInfoInqire':    _BASE + 'getEmrrmRltmUsefulSckbdInfoInqire',
    'getSrsillDissAceptncPosblInfoInqire':  _BASE + 'getSrsillDissAceptncPosblInfoInqire',
    'getEgytListInfoInqire':               _BASE + 'getEgytListInfoInqire',
    'getEgytLcinfoInqire':                 _BASE + 'getEgytLcinfoInqire',
    'getEgytBassInfoInqire':               _BASE + 'getEgytBassInfoInqire',
    'getStrmListInfoInqire':               _BASE + 'getStrmListInfoInqire',
    'getStrmLcinfoInqire':                 _BASE + 'getStrmLcinfoInqire',
    'getStrmBassInfoInqire':               _BASE + 'getStrmBassInfoInqire',
    'getEmrrmSrsillDissMsgInqire':         _BASE + 'getEmrrmSrsillDissMsgInqire',
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
<title>🔬 전체 API 디버그</title>
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
<h2>🔬 매뉴얼 9개 API 전체 조회 디버그</h2>
<p class="note" style="margin-bottom:18px;">특정 병원 HPID를 입력하면 매뉴얼의 모든 API를 호출하여 원본 응답 전체를 표시합니다.</p>

<form method="get" action="/debug/full/result">
  <label>HPID *</label>
  <input name="hpid" placeholder="예: A2100025 (분당제생병원)" required>
  <label>시/도 (STAGE1/Q0 파라미터용)</label>
  <input name="sido" placeholder="예: 경기도">
  <label>시/군/구 (STAGE2/Q1 파라미터용)</label>
  <input name="gugun" placeholder="예: 성남시">
  <button class="btn" type="submit">📡 전체 API 조회 시작</button>
</form>
<p class="note">시/도·시/군/구를 입력하지 않으면 기본정보 API에서 자동으로 추출을 시도합니다.</p>
<p style="margin-top:14px;"><a href="/">← 홈으로</a></p>
</body></html>""")


@flask_app.route('/debug/full/result')
def debug_full_result():
    """9개 API 전체 호출 후 결과를 복사 가능한 텍스트로 출력"""
    import html as _html

    hpid  = request.args.get('hpid',  '').strip()
    sido  = request.args.get('sido',  '').strip()
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
    lines.append(f'  매뉴얼 9개 API 전체 조회 결과')
    lines.append(f'  HPID: {hpid}  병원명: {name_from_basic}')
    lines.append(f'  시도: {sido}  시군구: {gugun}')
    lines.append(f'  위도: {lat}  경도: {lon}')
    lines.append(f'  조회시각: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append(SEP)

    api_labels = {
        'getEgytBassInfoInqire':               '① 응급의료기관 기본정보 조회',
        'getEmrrmRltmUsefulSckbdInfoInqire':    '② 응급실 실시간 가용병상정보 조회',
        'getSrsillDissAceptncPosblInfoInqire':  '③ 중증질환자 수용가능정보 조회',
        'getEgytListInfoInqire':               '④ 응급의료기관 목록정보 조회',
        'getEgytLcinfoInqire':                 '⑤ 응급의료기관 위치정보 조회',
        'getStrmListInfoInqire':               '⑥ 외상센터 목록정보 조회',
        'getStrmLcinfoInqire':                 '⑦ 외상센터 위치정보 조회',
        'getStrmBassInfoInqire':               '⑧ 외상센터 기본정보 조회',
        'getEmrrmSrsillDissMsgInqire':         '⑨ 응급실 및 중증질환 메시지 조회',
    }

    for api_name, label in api_labels.items():
        r = results.get(api_name, {})
        lines.append('')
        lines.append(SSEP)
        lines.append(f'  {label}')
        lines.append(f'  엔드포인트: {api_name}')
        if r.get('note'):
            lines.append(f'  [{r["note"]}]')
        if r.get('error'):
            lines.append(f'  ⚠️ 오류: {r["error"]}')
        lines.append(SSEP)
        items_list = r.get('items', [])
        if items_list:
            lines.append(f'  파싱 결과: {len(items_list)}건')
            for i, item in enumerate(items_list, 1):
                lines.append(f'  ── 레코드 {i} ──')
                for k, v in sorted(item.items()):
                    if v:
                        lines.append(f'    {k}: {v}')
        else:
            lines.append('  파싱 결과: 없음 (해당 병원 데이터 미존재 또는 오류)')
        lines.append('')
        lines.append('  [원시 XML]')
        raw_text = r.get('raw', '').strip()
        lines.append(raw_text if raw_text else '  (없음)')

    lines.append('')
    lines.append(SEP)
    lines.append('  END')
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
  <h2>🔬 전체 API 덤프 — {{ hpid }} ({{ name }})</h2>
  <button class="btn btn-green" onclick="copyAll()">📋 전체 복사</button>
  <a class="btn" href="/debug/full">🔄 다른 병원</a>
  <a class="btn" href="/">🏠 홈</a>
  <span class="status" id="copyStatus">✅ 클립보드에 복사됨</span>
</div>
<textarea id="out" readonly>{{ text }}</textarea>
<script>
function copyAll() {
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

_EARLY_LOG_PATHS = [
    '/sdcard/Download/emergency_crash.log',
    '/sdcard/emergency_crash.log',
    '/data/local/tmp/emergency_crash.log',
]
def _early_write(msg):
    for _p in _EARLY_LOG_PATHS:
        try:
            with open(_p, 'a') as _f:
                _f.write(msg + '\n')
            return _p
        except Exception:
            continue
    return None

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

_EARLY_LOG_PATHS = [
    '/sdcard/Download/emergency_crash.log',
    '/sdcard/emergency_crash.log',
    '/data/local/tmp/emergency_crash.log',
]
def _early_write(msg):
    for _p in _EARLY_LOG_PATHS:
        try:
            with open(_p, 'a') as _f:
                _f.write(msg + '\n')
            return _p
        except Exception:
            continue
    return None

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
        if len(name) > max_chars:
            name = name[:max_chars] + '…'
        return name

    # ─── PiP 선호 저장/복원 ─────────────────────────────────────
    _PIP_PREFS_FILE  = '/sdcard/Download/pip_prefs.json'
    _STATE_FILE      = '/sdcard/Download/emergency_state.json'

    def _load_pip_prefs():
        try:
            with open(_PIP_PREFS_FILE, 'r') as _f:
                return json.loads(_f.read())
        except Exception:
            return {'aspect_w': 16, 'aspect_h': 9}  # 기본 가로화면

    def _save_pip_prefs(prefs):
        try:
            with open(_PIP_PREFS_FILE, 'w') as _f:
                _f.write(json.dumps(prefs))
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
        def on_start(self):
            _early_write('[STEP2] on_start()')
            _dlog('[Lifecycle] on_start')
            try:
                self._pip_busy  = False   # ★ FIX(2026-C3): _enter_pip_mode 중복호출 방지 플래그
                self._pip_prefs = _load_pip_prefs()
                self._setup_logging()
                _dlog('[Lifecycle] 로깅 설정 완료')
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
                    # ★ FIX(2026-C2): Pydroid3 대응 — 테스트 환경은 Ongoing=False
                    # APK(정식빌드)에서만 영구 알림으로 동작시켜
                    # Pydroid3 종료 후 알림이 남는 문제를 방지한다.
                    _is_apk = not any('pydroid' in _p.lower() for _p in sys.path)
                    nb.setOngoing(_is_apk)
                    nb.setPriority(-2)   # PRIORITY_MIN
                    nb.setCategory('service')
                    nm.notify(9001, nb.build())
                    _dlog(f'[Kill방지] 알림 등록 완료 (ongoing={_is_apk})')

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
                ok = False
                try:
                    import urllib.request
                    with urllib.request.urlopen('http://127.0.0.1:5000/', timeout=3) as r:
                        ok = (r.status == 200)
                except Exception as _we:
                    _dlog(f'[Watchdog] Flask 무응답: {_we}')

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
        def on_stop(self):
            """앱 완전 종료 시: 상태바 알림 제거 (이슈7)"""
            _dlog('[Lifecycle] on_stop — 알림 제거')
            if _IS_ANDROID:
                try:
                    from jnius import autoclass
                    PA  = autoclass('org.kivy.android.PythonActivity')
                    ctx = PA.mActivity.getApplicationContext()
                    NM  = autoclass('android.app.NotificationManager')
                    nm  = PA.mActivity.getSystemService(ctx.NOTIFICATION_SERVICE)
                    nm.cancel(9001)
                    nm.cancelAll()
                    _dlog('[Lifecycle] 알림 제거 완료')
                except Exception as _se:
                    _dlog(f'[Lifecycle] 알림 제거 실패 (무시): {_se}')
            # WakeLock 해제
            try:
                if hasattr(self, '_wakelock') and self._wakelock.isHeld():
                    self._wakelock.release()
            except Exception:
                pass

        def on_pause(self):
            """
            True 반환 → 프로세스 유지.
            ★ 개선: pending 플래그 없이도 h_param이 세팅된 상태라면
              홈/백 버튼 즉시 PiP 진입 (초기화면 통과 불필요).
            """
            _dlog('[Lifecycle] on_pause')
            logging.info('[Lifecycle] on_pause')
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
            elif self._h_param:
                # 이전에 선택된 병원 있음 → 즉시 PiP
                _dlog('[PiP] on_pause: h_param 보존 → 즉시 PiP 진입')
                self._enter_pip_mode()
            return True  # 절대 kill하지 않음

        def on_resume(self):
            """포그라운드 복귀 시 pending 확인"""
            _dlog('[Lifecycle] on_resume')
            logging.info('[Lifecycle] on_resume')
            _dlog(f'[Lifecycle] on_resume _pip_state={_pip_state}')
            if _pip_state.get('pending'):
                _pip_state['pending'] = False
                self._h_param = _pip_state.get('h_param', '')
                self._iv_sec  = _pip_state.get('iv_sec', 180)
                _dlog(f'[PiP] on_resume pending: h={self._h_param[:50]}')
                # ★ FIX(2025): 타이머는 유지하고 즉시 한 번만 fetch
                # _start_pip_refresh()는 타이머 cancel+재등록 → Sync/on_pause와
                # 중복 호출 시 3중 fetch thread → apis.data.go.kr 폭주 → HTTP 500.
                # 타이머가 없을 때만 등록, 있을 때는 _do_pip_fetch 단독 실행.
                if self._pip_refresh_ev is None:
                    self._start_pip_refresh()
                else:
                    Clock.schedule_once(lambda dt: self._do_pip_fetch(0), 0)
                # ★ 즉시 PiP (이슈3: 백그라운드 버튼 → 즉시 최소화)
                Clock.schedule_once(lambda dt: self._enter_pip_mode(), 0.1)
            else:
                _dlog('[Lifecycle] on_resume: pending 없음')
                # ★ FIX(2025-B3): pending 없이도 last_pip_data가 있으면 재렌더링
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
                _timer_row = _BL2(orientation='horizontal',
                                  size_hint_y=None, height=22, spacing=2)
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
                    self._tbg_rem = _TC(0.42, 0.79, 0.43, 1)   # 남은시간 녹색
                    self._tb_rem  = _TR(pos=(0,0), size=(0,22))
                    self._tbg_ela = _TC(0.12, 0.26, 0.12, 1)    # 경과 어두운 녹
                    self._tb_ela  = _TR(pos=(0,0), size=(0,22))
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
                # ★ FIX: 고정 URL(_TARGET_URL) 대신 최근 비교화면(/last)으로 복귀
                #   → PiP에서 돌아올 때 엉뚱한 병원이 표시되던 문제 해결
                browser_btn.bind(on_press=lambda _btn: threading.Thread(
                    target=_open_browser_android,
                    args=('http://127.0.0.1:5000/last', 0.05), daemon=True).start())
                btn_row.add_widget(browser_btn)

                root.add_widget(btn_row)  # 항상 맨 아래 고정

                # 스케줄
                Clock.schedule_interval(self._poll, 3)
                Clock.schedule_once(self._poll, 2)
                Clock.schedule_interval(self._tick_timer, 1)
                Clock.schedule_interval(self._update_debug_panel, 2)

                # ★ 이슈4: 탭/더블탭 감지 — root 전체 터치 바인딩
                self._tap_count   = 0
                self._tap_timer   = None
                self._last_tap_t  = 0.0
                root.bind(on_touch_down=self._on_root_touch)

                # ★ 이슈4: 흔들기 감지 (Android 가속도계)
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
                is_landscape = (width > height * 1.2)
                _dlog(f'[Resize] {width}x{height} → {"가로" if is_landscape else "세로"} | data:{getattr(self,"_pip_base_sp","-")}sp bar:{getattr(self,"_pip_bar_sp","-")}sp')

                # 가로화면 감지: 로그패널 축소, 버튼행 확보
                if hasattr(self, '_log_area'):
                    self._log_area.size_hint_y = 0.12 if is_landscape else 0.33
                if hasattr(self, '_orient_btn'):
                    self._orient_btn.text = ('→세로' if is_landscape else '가로↔세로')
                if hasattr(self, '_btn_row'):
                    self._btn_row.height = max(34, min(50, int(height * 0.07)))

                # ★ 갱신막대 행 높이 동적 조절 (가로/세로 전환 시 최대화)
                if hasattr(self, '_timer_row'):
                    self._timer_row.height = max(18, min(30, int(height * 0.045)))

                # ★ 이슈9: PiP 창 크기 조절 시 폰트/레이아웃 동적 재계산
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
                if delta > 15:
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
                self._timer_lbl.text = (
                    f'[size={bar_sp + 1}sp][color=#888888]갱신 {m}:{s:02d}[/color][/size]'
                )

                # Canvas 타이머 바: 남은비율로 직접 업데이트 (픽셀 완벽)
                rem_ratio = (remaining / self._iv_sec) if self._iv_sec > 0 else 0.0
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
                # ★ Flask 준비 완료 → 진행바 숨김 (그래픽 깨짐 방지)
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
                # ★ 즉시 PiP 진입 (이슈3: 백그라운드 버튼 시 즉시 최소화)
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
                # ★ FIX(2025): _start_pip_refresh → _do_pip_fetch(0) 단독 호출로 변경
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
            if sys_val > 21:
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
                if real > 0:
                    _dlog(f'[API] SystemProperties={real}')
                    return real
            except Exception as _e2:
                _dlog(f'[API] SystemProperties 실패: {_e2}')
            _dlog('[API] 전부 실패 → 99 강행')
            return 99

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
                params = (PIPB()
                          .setAspectRatio(Rational(prefs['aspect_w'], prefs['aspect_h']))
                          .setAutoEnterEnabled(True)
                          .build())
                activity.setPictureInPictureParams(params)
                _dlog('[PiP] setAutoEnterEnabled(True) 완료')
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
                with open(_STATE_FILE, 'w', encoding='utf-8') as _sf:
                    _sf.write(json.dumps(state))
                _dlog(f'[State] 저장: h={self._h_param[:30]} iv={self._iv_sec}s')
            except Exception as _se:
                _dlog(f'[State] 저장 실패: {_se}')

        def _restore_state(self):
            """저장된 상태를 복원. 성공 시 True 반환"""
            for path in [_STATE_FILE,
                         '/data/local/tmp/emergency_state.json',
                         os.path.join(os.path.expanduser('~'), 'emergency_state.json')]:
                try:
                    with open(path, encoding='utf-8') as _sf:
                        state = json.loads(_sf.read())
                    h = state.get('h_param', '')
                    if h:
                        self._h_param = h
                        self._iv_sec  = int(state.get('iv_sec', 180))
                        _dlog(f'[State] 복원 성공: h={h[:40]} iv={self._iv_sec}s')
                        return True
                except Exception:
                    pass
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
            if self._iv_sec > 0:
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

            # ★ FIX(2025): 빈 결과 수신 시 마지막 성공 데이터 유지
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

            # ★ 최신 데이터 캐시 저장 — resize 시 _on_window_resize가 재빌드에 사용
            # hospitals 변수는 이미 0개 폴백 처리가 완료된 값을 사용한다.
            self._last_pip_data = {**data, 'hospitals': hospitals}

            # ── 폰트 자동조절: 병원수·PiP창 크기 기반 ─────────────
            # Window.on_resize 이벤트 → PiP 진입/크기조절 시 실제 창 크기 전달됨.
            # Window.width/height 직접 사용으로 가로/세로 전환 시 최대화 유지.
            try:
                w = Window.width
                h = Window.height
                n = max(1, len(hospitals))
                # ★ 이슈8: 시인성 최대 폰트 — 병원수 2개 기준 14sp, 많을수록 축소
                # 최소값 상향(9→11) + 기본값 상향(14→16)
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
            # sp = dp * fontScale이므로 fontScale > 1 환경(접근성 설정 등)에서는
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

                # ★ FIX(2026-C1): format_bed_cell()과 동일한 sentinel 조건으로 통일.
                # 구 코드: a == -1 → total 무관하게 회색 '-' 처리
                #   → hvec=-1 이지만 total=39인 경우(초과운용 데이터)를
                #     "정보없음"으로 잘못 숨기는 버그 (세브란스 응급 미표시).
                # 신 코드: a == -1 AND t <= 0 인 경우만 "정보없음" 처리.
                #   → a < 0 이지만 t > 0 (예: -1/39)은 빨간색으로 정상 표시.
                #   브라우저 format_bed_cell()의 'avail == -1 and total <= 0' 조건과 동일.
                if a == -1 and t <= 0:
                    return ('[color=#444444]-[/color]',
                            0.0, '#333333', '#222222', '#444444')

                _a_display = a  # 표시용 원본 (음수 그대로 표시)
                if a < 0:
                    a = 0

                label = f'{_a_display}/{t}' if t > 0 else str(_a_display)
                p     = (a / t) if t > 0 else (1.0 if a > 0 else 0.0)
                p     = max(0.0, min(1.0, p))

                c  = _C_GREEN  if p >= 0.5 else _C_YELLOW if p >= 0.2 else _C_RED
                cu = _C_GUSED  if p >= 0.5 else _C_YUSED  if p >= 0.2 else _C_RUSED

                return f'[color={c}][b]{label}[/b][/color]', p, c, cu, c

            # ★ FIX(2025-B3): 원자적 위젯 교체 — 사라짐 방지
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

                hvec  = h.get('hvec',  -1); hvec_t  = h.get('hvec_t',  0)
                hvgc  = h.get('hvgc',  -1); hvgc_t  = h.get('hvgc_t',  0)
                hv36  = h.get('hv36',  -1); hv36_t  = h.get('hv36_t',  0)
                hicu  = h.get('hicu',  -1); hicu_t  = h.get('hicu_t',  0)

                # ★ 입원 표시: hvgc(일반) + hv36(응급전용) 합산
                # 비교화면의 "입원실 일반 + 응급전용" 합계와 동일하게 표시
                # 한쪽만 데이터 있는 경우(-1 제외)도 올바르게 합산
                if hvgc >= 0 and hv36 >= 0:
                    _gc_combined   = hvgc + hv36
                    _gc_t_combined = (hvgc_t if hvgc_t > 0 else 0) + (hv36_t if hv36_t > 0 else 0)
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
                    # ★ FIX(2025): 글자 수에 따라 폰트 크기와 숫자 열 폭을 동시 조정.
                    # 고DPI 기기(1440px 폭) 기준:
                    #   num_lbl 폭 ≈ num_sx × 0.333 × 0.58 × screen_w ≈ 153px (num_sx=0.55 시)
                    #   입원 "351/1268"(8자) → ~22px/자 × 8 = 176px > 153px → 오버플로우
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
        def _enter_pip_mode(self):
            _dlog('[PiP] _enter_pip_mode 진입')
            if not _IS_ANDROID:
                _dlog('[PiP] PC 환경 → 스킵')
                return

            # ★ FIX(2026-C3): 중복 진입 차단
            # moveTaskToBack() 폴백이 on_pause()를 재트리거하면서
            # _enter_pip_mode()가 연속으로 두 번 호출되는 패턴을 방지.
            # _pip_busy가 True인 동안은 즉시 반환.
            if getattr(self, '_pip_busy', False):
                _dlog('[PiP] _enter_pip_mode 이미 실행 중 → 중복 호출 무시')
                return
            self._pip_busy = True

            api_level = EmergencyApp._get_real_api_level()
            _dlog(f'[PiP] API={api_level}')

            if api_level not in (0, 99) and api_level < 26:
                _dlog(f'[PiP] API {api_level} < 26 → PiP 미지원')
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
                        _dlog('[PiP] Activity 획득 실패 → PiP 중단')
                        return

                    try:
                        PIPB     = autoclass('android.app.PictureInPictureParams$Builder')
                        Rational = autoclass('android.util.Rational')
                        _dlog('[PiP] PIPBuilder 로드 성공')

                        builder = PIPB().setAspectRatio(
                            Rational(prefs['aspect_w'], prefs['aspect_h']))
                        if api_level >= 31:
                            try:
                                builder = builder.setAutoEnterEnabled(True)
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
                        _dlog(f'[PiP] 진입 성공 result={result}')
                        logging.info('[PiP] PIP 진입 성공')

                        # 사용된 aspect ratio 저장
                        _save_pip_prefs(prefs)

                    except Exception as _be:
                        _dlog(f'[PiP] PIPBuilder 실패: {_be}')
                        _dlog('[PiP] 파라미터 없이 재시도...')
                        try:
                            activity.enterPictureInPictureMode()
                            _dlog('[PiP] 파라미터 없이 성공')
                        except Exception as _e2:
                            _dlog(f'[PiP] 모든 시도 실패: {_e2}')
                            # ★ PiP 불가 폴백: moveTaskToBack으로 백그라운드 전환
                            # (PiP 창은 안 뜨지만 앱이 백그라운드에서 타이머 계속 작동)
                            try:
                                activity.moveTaskToBack(True)
                                _dlog('[PiP] moveTaskToBack(True) 실행 (PiP 대체 백그라운드)')
                            except Exception as _mbe:
                                _dlog(f'[PiP] moveTaskToBack 실패: {_mbe}')
                finally:
                    # ★ FIX(2026-C3): _do_pip 완료(성공/실패/예외 불문) 후 플래그 해제
                    # 1.5초 지연: on_pause가 _do_pip 완료 직후 재트리거되더라도
                    # 해당 on_pause 내 _enter_pip_mode 호출까지 차단 후 해제.
                    Clock.schedule_once(
                        lambda _dt: setattr(self, '_pip_busy', False), 1.5)

            try:
                from android.runnable import run_on_ui_thread as _rut
                _rut(_do_pip)()
                _dlog('[PiP] run_on_ui_thread 예약됨')
            except ImportError:
                Clock.schedule_once(lambda dt: _do_pip(), 0)
                _dlog('[PiP] android.runnable 없음 → Clock 대체')
            except Exception as _rue:
                _dlog(f'[PiP] run_on_ui_thread 예외 → 직접 호출: {_rue}')
                _do_pip()

        # ── 로깅 설정 ────────────────────────────────────────
        def _setup_logging(self):
            log_dir = None
            try:
                from jnius import autoclass as _ac
                _PA = _ac('org.kivy.android.PythonActivity')
                _ext = _PA.mActivity.getExternalFilesDir(None)
                if _ext: log_dir = _ext.getAbsolutePath()
            except Exception: pass
            if not log_dir:
                try:
                    from android.storage import app_storage_path as _asp
                    log_dir = _asp()
                except Exception: pass
            if not log_dir:
                for _d in ['/sdcard/Download', '/sdcard', '/data/local/tmp']:
                    try:
                        os.makedirs(_d, exist_ok=True)
                        _tp = os.path.join(_d, '.wt')
                        open(_tp,'w').close(); os.remove(_tp)
                        log_dir = _d; break
                    except Exception: continue
            if not log_dir:
                log_dir = '/data/local/tmp'

            os.makedirs(log_dir, exist_ok=True)
            LOG_FILE = os.path.join(log_dir, 'emergency_app.log')
            _LOG_FILE_REF[0] = LOG_FILE

            try: _fh = logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8')
            except Exception: _fh = logging.NullHandler()
            _sh = logging.StreamHandler(sys.stdout)
            _fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s',
                                     datefmt='%H:%M:%S')
            _fh.setFormatter(_fmt); _sh.setFormatter(_fmt)
            _root = logging.getLogger()
            _root.setLevel(logging.DEBUG)
            _root.handlers.clear()
            _root.addHandler(_fh); _root.addHandler(_sh)

            def _exc_hook(t, v, tb):
                logging.critical('미처리 예외!', exc_info=(t,v,tb))
                for _h in logging.getLogger().handlers:
                    try: _h.flush()
                    except: pass
            sys.excepthook = _exc_hook
            logging.info('='*60)
            logging.info('응급의료기관 앱 시작')
            logging.info(f'로그: {LOG_FILE}')
            logging.info('='*60)
            _early_write(f'[STEP2] log: {LOG_FILE}')

        def _start_flask(self):
            def _run():
                try:
                    logging.info('Flask 시작 (127.0.0.1:5000)...')
                    flask_app.run(host='127.0.0.1', port=5000,
                                  debug=False, use_reloader=False, threaded=True)
                except Exception as _e:
                    logging.error(f'Flask 오류: {_e}')
                    logging.error(traceback.format_exc())
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