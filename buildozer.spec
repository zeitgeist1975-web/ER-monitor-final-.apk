[app]

# 앱 라벨은 ASCII 고정(빌드 안정성). 한글 라벨 필요 시 strings.xml 오버레이 사용.
title = ERMonitor
package.name = ermonitor
package.domain = org.ermon

source.dir = .
source.include_exts = py,png,jpg,jpeg,ttf,ttc,otf,json,txt,html,css,js
source.include_patterns = fonts/*,assets/*
source.exclude_dirs = tests,bin,.git,.github,.buildozer,p4a,tools,__pycache__
source.exclude_patterns = *.log,*.apk,*.aab,README*.md

version = 1.0.0

# Flask/Requests 전체 의존 트리 명시.
# markupsafe 는 C확장 포함 → 2.1.5 로 고정 (순수파이썬 폴백이 있는 마지막 계열).
# 3.x 또는 미고정 시 p4a 최신판의 --only-binary 정책과 충돌하여 설치 실패.
requirements = python3,kivy,pyjnius,android,plyer,setuptools,flask,jinja2,werkzeug==2.2.3,markupsafe==2.1.5,itsdangerous,click,blinker,requests,urllib3,idna,chardet,charset-normalizer,certifi

orientation = portrait
fullscreen = 0
android.allow_backup = True

# ── Android SDK/NDK ────────────────────────────────────────────────
android.api = 34
android.minapi = 26
android.ndk = 25b
android.accept_sdk_license = True
android.archs = arm64-v8a,armeabi-v7a
android.release_artifact = apk
android.debug_artifact = apk

# ── 권한 ───────────────────────────────────────────────────────────
android.permissions = INTERNET,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE,WAKE_LOCK,VIBRATE,POST_NOTIFICATIONS,FOREGROUND_SERVICE,FOREGROUND_SERVICE_SPECIAL_USE,SYSTEM_ALERT_WINDOW,REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,RECEIVE_BOOT_COMPLETED,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,MANAGE_EXTERNAL_STORAGE

# ── 매니페스트 보강 ────────────────────────────────────────────────
# PiP / uses-feature / application 속성 전부 p4a 템플릿 패치로 주입
# (tools/patch_p4a_manifest.sh — 워크플로에서 자동 실행)
android.manifest.launch_mode = singleTask

# ── p4a ────────────────────────────────────────────────────────────
# 아이콘/프리스플래시 (없으면 워크플로가 자동 생성)
icon.filename = %(source.dir)s/icon.png
presplash.filename = %(source.dir)s/presplash.png
android.presplash_color = #0E131B

p4a.bootstrap = sdl2
# 워크플로가 절대경로로 치환한다 (매니페스트 패치본 사용)
p4a.source_dir =

android.logcat_filters = *:S python:D SDL:D AndroidRuntime:E

[buildozer]
log_level = 2
warn_on_root = 0
