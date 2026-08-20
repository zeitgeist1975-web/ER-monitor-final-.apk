[app]

# ──────────────────────────────────────────────────────────
# 기본 정보
# ──────────────────────────────────────────────────────────
title = ER stat
package.name = gcmcstat
package.domain = org.gcmcer1
version = 1.0

# 진입점은 main.py 여야 함 (build.yml 에서 gcmc_stat.py -> main.py 로 복사)
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json

# ──────────────────────────────────────────────────────────
# 앱 아이콘 / 로딩 스플래시
#  - icon.png / presplash.png 는 리포 루트(source.dir)에 위치.
#  - %(source.dir)s 는 위 source.dir(=.) 로 치환됨.
#  - 아이콘: 정사각 512x512 권장 / 스플래시: 정사각 고해상도 권장.
#  - android.presplash_color: 스플래시 이미지 여백을 채우는 배경색.
#  ※ 새 아이콘은 캐시가 강해 기존 APK 삭제 후 재설치해야 반영됨.
# ──────────────────────────────────────────────────────────
icon.filename = %(source.dir)s/icon.png
presplash.filename = %(source.dir)s/presplash.png
android.presplash_color = #000000

# ──────────────────────────────────────────────────────────
# 의존성
#  - python3      : 버전 고정 필수! 고정 안 하면 p4a 공식 레시피 기본값
#    (현재 master 기준 3.14.2)이 그대로 잡힘. build.yml이 도커 이미지를
#    kivy/buildozer:latest(플로팅 태그)로 받아오는데, 이미지가 갱신될
#    때마다 내부 p4a 체크아웃도 달라져 python3 기본 버전이 바뀔 수 있음
#    → numpy/pandas 설정은 그대로인데 Python 버전만 바뀌며 파일읽기/
#    날짜변환 등 여러 지점에서 네이티브 abort 재현(runlog 실측:
#    3.13.13=17회 중 6/6 정상, 3.14.2=11/11 크래시). 3.13.13으로 고정.
#  - hostpython3  : python3와 반드시 "동일 버전"으로 함께 고정!
#    p4a가 dist 생성 직후 둘의 일치를 하드체크함 ("python3 should have
#    same version as hostpython3" 에러가 그것). python3만 고정하고
#    이걸 빼먹으면 hostpython3는 레시피 기본값(3.14.2)으로 남아
#    컴파일 시작도 전에 즉시 실패. (버전 표기에 v 접두사 없음 주의 —
#    python3/hostpython3는 python.org 소스라 v 없이, numpy/pandas는
#    git 태그라 v 붙여서.)
#  - pandas/numpy : 버전 고정 필수! 고정 안 하면 빌드 시점마다 PyPI 최신판이
#    잡혀서 빌드가 됐다 안됐다 함 (예: "PyDataType_TYPEOBJ" undeclared 에러).
#    ※(정정) pandas는 2.1.0부터 이미 meson+Cython3 체계라 numpy를 1.x로
#    내려도 meson 자체는 못 피함. p4a 공식 PandasRecipe의
#    hostpython_prerequisites=["numpy>=2.0",...]가 "빌드 시점" 호스트 numpy를
#    항상 2.0+로 고정하므로, 타겟 numpy도 2.0대여야 컴파일-런타임 ABI가
#    맞음. numpy를 1.26.4로 내렸다가 take/maybe_promote 네이티브 크래시
#    재현됨(runlog 실측) → 2.2.3 고정으로 복귀.
#    (numpy는 2.0.0부터, pandas는 2.1.0부터 meson+Cython3 체계로 전환됨)
#  - openpyxl     : pure python, 자동 설치
#  - pyjnius      : 코드 내 jnius.autoclass 사용 (파일열기/진동/권한 인텐트)
#  - android      : android.permissions 모듈 사용
# ──────────────────────────────────────────────────────────
#  - charset_normalizer : 반드시 "언더스코어" 표기 + 2.1.1 고정!
#    kivy 레시피의 python_depends 에 requests 가 있어 charset-normalizer 가
#    전이 의존성으로 딸려옴(우리 앱은 네트워크 미사용이나 제거하려면
#    kivy 로컬레시피+패치3개 복사가 필요해 부작용이 더 큼).
#    ★ 문제: charset-normalizer 3.5.0 부터 PyPI 에 Android wheel 이
#      올라오기 시작함. p4a 는 의존성 해석을 --platform=android_24_arm64_v8a
#      로 수행해 그 android wheel 의 URL 을 requirements.txt 에 박아넣는데,
#      정작 설치는 `pip install --target ... -r requirements.txt` 로
#      --platform 없이 실행 → "is not a supported wheel on this platform"
#      으로 빌드 실패. (p4a build.py is_wheel_compatible 의 버그)
#    ★ 우회 원리: p4a 는 해석된 패키지명(charset-normalizer→charset_normalizer)
#      이 requirements 에 이미 있으면 URL 을 추가하지 않고 건너뜀. 따라서
#      "언더스코어" 표기로 직접 명시하면 URL 삽입 경로를 타지 않음.
#      (하이픈으로 쓰면 이름이 안 맞아 URL 이 그대로 박히므로 반드시 언더스코어)
#    ★ 2.1.1 인 이유: 순수 파이썬 wheel(py3-none-any) "만" 제공하는 마지막
#      버전. 3.4.x 는 manylinux x86_64 바이너리 wheel 이 있어 그게 설치되어
#      arm64 APK 에 x86_64 .so 가 섞임. 2.1.1 은 우리 앱이 import 하지
#      않으므로(네트워크 미사용) 구버전이어도 무해함.
requirements = python3==3.13.13,hostpython3==3.13.13,kivy==2.3.1,numpy==v2.2.3,pandas==v2.2.3,openpyxl,charset_normalizer==2.1.1,pyjnius,android

# ──────────────────────────────────────────────────────────
# 화면/표시
# ──────────────────────────────────────────────────────────
orientation = portrait
fullscreen = 0

# ──────────────────────────────────────────────────────────
# 권한
#  - READ/WRITE_EXTERNAL_STORAGE : Android 10 이하
#  - MANAGE_EXTERNAL_STORAGE     : Android 11+ 전체 파일 접근
#                                  (코드가 직접 설정화면으로 유도하는 인텐트를 띄움)
#  - VIBRATE                     : 코드 내 Vibrator 사용
# ──────────────────────────────────────────────────────────
android.permissions = READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,MANAGE_EXTERNAL_STORAGE,VIBRATE

# ──────────────────────────────────────────────────────────
# 빌드 타겟
#  - 단말 1대(아키텍처 하나)만 타겟해서 컴파일 시간/실패 가능성을 줄임.
#    오래된 32비트 기기도 지원해야 하면 armeabi-v7a 를 추가.
# ──────────────────────────────────────────────────────────
android.api = 34
android.minapi = 24
android.ndk_api = 24
android.archs = arm64-v8a

# CI(비대화형 환경)에서 SDK 라이선스 자동 동의 — 반드시 True
android.accept_sdk_license = True

p4a.bootstrap = sdl2
p4a.local_recipes = ./p4a_recipes

# ──────────────────────────────────────────────────────────
# p4a 자체 버전 고정 (이번 수정의 핵심!)
#  - 미지정 시 buildozer 기본값이 branch=master → 빌드할 때마다
#    python-for-android "그 시점 최신 커밋"을 clone해 옴.
#    즉 python3/hostpython3/numpy/... 전체 레시피 기본값이 예고 없이
#    드리프트함. 지금까지 "하나 고치면 다른 데서 또 터짐" 연쇄
#    (numpy→python3→hostpython3)의 근본 원인.
#  - v2026.05.09 = p4a 공식 릴리즈 태그(= PyPI 2026.5.9, 커밋 58d2114).
#    buildozer는 git clone -b <값> --single-branch 로 받으므로
#    브랜치명뿐 아니라 태그명도 유효함(clone 실측 확인).
#  - 이후 p4a를 올리고 싶으면 이 값만 다음 릴리즈 태그로 바꾸고
#    requirements의 버전 핀들과의 궁합을 다시 검증할 것.
# ──────────────────────────────────────────────────────────
p4a.branch = v2026.05.09

[buildozer]

# 실패 시 원인 파악을 위해 로그를 자세히 남김
log_level = 2
warn_on_root = 1
