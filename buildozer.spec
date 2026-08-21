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
requirements = python3,kivy,pyjnius,android,plyer,setuptools,flask,jinja2,werkzeug,markupsafe==2.1.5,itsdangerous,click,blinker,requests,urllib3,idna,chardet,charset-normalizer,certifi

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
# buildozer 1.5.0 의 android.extra_manifest_xml /
# android.extra_manifest_application_arguments 는 셸 인용부호를 리터럴로
# p4a 에 넘기는 버그가 있어 AndroidManifest 파싱이 깨진다(사용 금지).
# uses-feature · application 속성 · PiP 는 전부
# tools/patch_p4a_manifest.sh 가 p4a 템플릿에 직접 주입한다.
android.manifest.launch_mode = singleTask

# ── p4a ────────────────────────────────────────────────────────────
p4a.bootstrap = sdl2
# 워크플로가 절대경로로 치환한다 (매니페스트 패치본 사용)
p4a.source_dir =

android.logcat_filters = *:S python:D SDL:D AndroidRuntime:E

[buildozer]
log_level = 2
warn_on_root = 0
