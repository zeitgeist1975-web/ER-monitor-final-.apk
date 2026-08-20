# APK 빌드

## 저장소 배치
```
├─ main.py                      # 진입점(권한/로그/자원확보 → 원본 실행)
├─ ER_monitor__final_.py        # 원본 앱 (파일명 변경 시 워크플로가 자동 감지)
├─ buildozer.spec
├─ .gitattributes / .gitignore
├─ src/android/extra_manifest.xml
├─ src/android/extra_manifest_application_arguments.xml
├─ tools/patch_p4a_manifest.sh  # PiP 속성 주입
└─ .github/workflows/build-apk.yml
```

## 실행
- push(main/master) 또는 Actions → build-apk → Run workflow
- 산출물: Artifacts `ermonitor-apk`
- `git tag v1.0.0 && git push --tags` → Release 자동 첨부

## 로그 경로 (단말)
| 파일 | 내용 |
|---|---|
| `/sdcard/Download/ermon_boot.log` | 부트스트랩·권한·모듈점검·트레이스백 (라인마다 fsync) |
| `/sdcard/Download/ermon_fault.log` | SIGSEGV/SIGABRT 네이티브 스택 |
| `/sdcard/Download/emergency_crash.log` | 원본 코드 STEP 로그 |
| `<앱외부저장>/emergency_app.log` | 앱 로깅 |
| `adb logcat -s python:D SDL:D AndroidRuntime:E` | 실시간 |

## 주의
- 폰트: 시스템 CJK 폰트(NotoSansCJK/DroidSansFallback) 우선 사용. 전무할 때만 NanumGothic 자동 다운로드 → `ERMON_FONT` 환경변수로 전달.
- Kivy 라벨의 `★ → ↔` 는 BMP 문자로 시스템 폰트 커버됨. 컬러 이모지(U+1F300~)는 Kivy 렌더 불가 → 텍스트 기호로만 사용할 것.
- `/sdcard/Download` 쓰기는 API30+에서 전체 파일 접근 승인 필요. 최초 실행 시 설정화면이 자동으로 뜬다.
- PiP는 `minapi=26` 이상 + 매니페스트 패치가 적용되어야 동작.
