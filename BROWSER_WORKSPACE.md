# 사용자 브라우저 연결 구조

현재 리멤버 실제 접속 권한이 없는 상태라 크롤링은 아직 `MockRememberAdapter`로 동작합니다. 대신 실제 PC에서 이어서 개발할 수 있도록 전용 브라우저 실행 구조와 Playwright 연결 골격을 준비했습니다.

## 실행 흐름

1. 클라이언트는 `run_app.bat`을 실행합니다.
2. 앱 서버가 `http://127.0.0.1:8000`으로 실행됩니다.
3. Chrome 또는 Edge가 전용 프로필 폴더로 열립니다.
4. 같은 브라우저 창에 앱 탭과 리멤버 탭이 함께 열립니다.
5. 사용자는 리멤버 탭에서 직접 로그인하고 검색 조건을 설정합니다.
6. 나중에 실제 연동 시 앱의 시작 버튼이 현재 리멤버 탭을 대상으로 후보자 목록과 상세 이력을 수집합니다.

## 관련 설정

설정창의 `브라우저` 탭에서 아래 값을 변경할 수 있습니다.

- `CRAWLER_MODE`: `mock` 또는 `browser`
- `REMEMBER_URL`: 리멤버 탭으로 열 URL
- `REMEMBER_CDP_URL`: Playwright가 연결할 CDP 주소
- `REMEMBER_BROWSER_PORT`: Chrome/Edge 원격 디버깅 포트
- `REMEMBER_BROWSER_PROFILE_DIR`: 로그인 세션을 저장할 전용 브라우저 프로필 폴더

기본값은 다음과 같습니다.

```env
CRAWLER_MODE=mock
REMEMBER_URL=https://career.rememberapp.co.kr/
REMEMBER_CDP_URL=http://127.0.0.1:9222
REMEMBER_BROWSER_PORT=9222
REMEMBER_BROWSER_PROFILE_DIR=browser_profile
```

`browser_profile/`은 로그인 세션과 쿠키가 저장될 수 있으므로 `.gitignore`에 포함했습니다.

## 수동으로 브라우저만 다시 열기

서버가 이미 떠 있는 상태에서 앱 탭과 리멤버 탭만 다시 열고 싶으면 아래 파일을 실행합니다.

```text
open_browser_workspace.bat
```

## 실제 연동 시 이어서 할 작업

- `app/services/remember_browser.py`의 `BrowserRememberAdapter`에서 CDP 연결 후 현재 리멤버 탭을 찾습니다.
- 리멤버 접근 가능한 PC에서 검색 결과 목록, 후보자 상세, 제안 메시지 입력 영역의 DOM 셀렉터를 확인합니다.
- `search()`, `open_candidate()`, `send_proposal()`을 실제 DOM 동작으로 구현합니다.
- `app/main.py`에서 `CRAWLER_MODE=browser`일 때 `MockRememberAdapter` 대신 `BrowserRememberAdapter`를 연결합니다.

현재는 `CRAWLER_MODE=browser`로 바꾸고 실행을 시작하면 501 오류를 반환합니다. 실제 리멤버 DOM 검증 전까지 목 데이터 실행이 기본 동작입니다.
