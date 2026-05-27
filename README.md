# 헤드헌팅 후보자 제안 자동화 MVP

로컬에서 실행하는 FastAPI 기반 MVP입니다. 현재 버전은 리멤버 접속 권한이 없는 개발 PC에서도 전체 제품 흐름을 확인할 수 있도록 더미 후보자와 시뮬레이션 발송을 사용합니다.

## 현재 구현 범위

- JD 입력
- JD와 후보자 이력 텍스트 기반 OpenAI 매칭 평가
- 더미 후보자 연결
- OpenAI API 키가 없거나 호출 실패 시 데모 폴백
- 더미 리멤버 후보자 검색/이력서 추출 시뮬레이션
- 매칭 점수와 사유 표시
- 테스트모드 ON: 통과 후보자 선택 발송 시뮬레이션
- 테스트모드 OFF: 통과 후보자 자동 발송 시뮬레이션
- 진행 상태 WebSocket 갱신
- 결과 리포트 표시

## 설치

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

화면 오른쪽 위 `설정`에서 OpenAI API 키를 저장하면 실제 GPT 호출을 사용합니다. 저장값은 프로젝트 루트의 `.env`에 기록됩니다.

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.4-mini
APP_HOST=127.0.0.1
APP_PORT=8000
MIN_DELAY_SECONDS=1
MAX_DELAY_SECONDS=3
USD_KRW_RATE=1507.2
```

## 실행

클라이언트에게 전달할 때는 아래 파일 중 하나를 실행하면 됩니다.

```text
run_app.bat
```

또는

```powershell
python run_app.py
```

`run_app.py`는 필요한 패키지가 없으면 `requirements.txt`를 설치하고, 같은 포트에 떠 있는 기존 서버를 종료한 뒤 서버를 다시 시작하고 기본 웹 브라우저를 자동으로 엽니다.

수동 실행은 아래 명령을 사용합니다.

```powershell
python -m app.main
```

브라우저에서 접속합니다.

```text
http://127.0.0.1:8000
```

## 운영 메모

- 현재 `MIN_DELAY_SECONDS`, `MAX_DELAY_SECONDS`는 데모 확인을 위해 1~3초입니다.
- 실제 리멤버 연동 후에는 PRD 기준인 30~90초로 변경하세요.
- 현재 앱에서 사용하는 더미 후보자는 30명입니다. 테스트 비용은 시작 화면의 `크롤링 · API 분석` 상한으로 조절하세요.
- 시작 화면의 크롤링/API 분석 상한은 브라우저 로컬 저장소에 자동 저장됩니다. 메시지 발송 수는 제한하지 않습니다.
- OpenAI API 키, Flagship 모델 프리셋/수동 모델명, 지연시간, 호스트, 포트, USD/KRW 환율은 화면의 `설정`에서 저장할 수 있습니다.
- 결과 화면에는 OpenAI 응답 토큰 사용량 기준의 예상 API 호출비용을 USD/KRW 환율로 환산해 표시합니다.
- 테스트모드 ON 상태에서 실행하면 OpenAI 요청/응답 디버그 로그가 `logs/openai/`에 JSON 파일로 저장됩니다.
- 디버그 로그에는 JD와 후보자 이력서 텍스트가 포함될 수 있으므로 외부 공유 전 반드시 확인하세요. `logs/`는 GitHub 업로드 제외 대상입니다.
- JD, 통과 기준, 실행 범위, 테스트모드는 브라우저 로컬 저장소에 자동 저장됩니다.
- 실행 결과와 후보자 이력서 원문은 서버 재시작 후 남지 않도록 메모리에만 보관합니다.
- 후보자 이력서 원문은 DB나 파일에 저장하지 않습니다.
- GitHub에는 `.env`, 로그인 세션, 실제 후보자 데이터가 올라가지 않도록 유지하세요.

## 후속 작업

실제 리멤버 접속 가능한 PC에서 이어 할 작업은 `IMPLEMENTATION_GAPS.md`에 정리했습니다.
