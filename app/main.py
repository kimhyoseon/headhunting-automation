from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.models import AppSettingsUpdate, RunCreateRequest, SendSelectedRequest
from app.services.llm import LLMService
from app.services.privacy import redact_candidate_name
from app.services.remember import MockRememberAdapter
from app.services.remember_browser import BrowserRememberAdapter, RememberBrowserConfig
from app.services.runner import RunManager
from app.services.saramin_browser import BrowserSaraminAdapter, SaraminBrowserConfig
from app.services.settings import SettingsStore

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
STATIC_DIR = BASE_DIR / "static"
ENV_PATH = Path(os.getenv("HEADHUNTING_ENV_PATH", str(ROOT_DIR / ".env"))).expanduser()
PROMPTS_PATH = ROOT_DIR / "config" / "prompts.json"

load_dotenv(dotenv_path=ENV_PATH)

app = FastAPI(title="헤드헌팅 후보자 제안 자동화 MVP")
settings_store = SettingsStore(ENV_PATH, PROMPTS_PATH)
initial_settings = settings_store.load()
llm = LLMService()
llm.configure(
    os.getenv("OPENAI_API_KEY", ""),
    os.getenv("OPENAI_MODEL", "gpt-5.4-mini"),
    float(os.getenv("USD_KRW_RATE", "1507.2") or "1507.2"),
    initial_settings.prompts,
)
remember = MockRememberAdapter()
run_manager = RunManager(llm, remember)
selected_saramin_offer_position: dict[str, str] | None = None


class SaraminOfferPositionSelection(BaseModel):
    id: str = ""
    label: str = ""


@app.middleware("http")
async def prevent_stale_frontend_assets(request, call_next):
    response = await call_next(request)
    if request.url.path in {"/", "/static/app.js"}:
        response.headers["Cache-Control"] = "no-store"
    return response


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/settings")
async def get_settings():
    return settings_store.load()


def _provider() -> str:
    return str(os.getenv("HEADHUNTING_PROVIDER") or "remember").strip().lower() or "remember"


def _saramin_adapter(settings, offer_position: dict[str, str] | None = None) -> BrowserSaraminAdapter:
    return BrowserSaraminAdapter(
        SaraminBrowserConfig(
            cdp_url=settings.remember_cdp_url,
            saramin_url=settings.remember_url,
            locale=settings.browser_locale,
            accept_language=settings.browser_accept_language,
            timezone=settings.browser_timezone,
            skip_proposal_send=settings.remember_skip_proposal_send,
            offer_position=offer_position,
        )
    )


@app.get("/api/saramin/offer-positions")
async def get_saramin_offer_positions():
    if _provider() != "saramin":
        raise HTTPException(status_code=404, detail="Saramin provider is not active.")
    settings = settings_store.load()
    try:
        return await _saramin_adapter(settings).inspect_offer_positions()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/saramin/offer-position")
async def save_saramin_offer_position(request: SaraminOfferPositionSelection):
    global selected_saramin_offer_position
    position_id = request.id.strip()
    label = request.label.strip()
    if not position_id and not label:
        raise HTTPException(status_code=400, detail="Saramin offer position is required.")
    selected_saramin_offer_position = {"id": position_id, "label": label}
    return {"ok": True, "position": selected_saramin_offer_position}


@app.post("/api/settings")
async def save_settings(request: AppSettingsUpdate):
    if request.max_delay_seconds < request.min_delay_seconds:
        raise HTTPException(status_code=400, detail="최대 지연시간은 최소 지연시간보다 커야 합니다.")
    settings = settings_store.save(request)
    llm.configure(
        os.getenv("OPENAI_API_KEY", ""),
        os.getenv("OPENAI_MODEL", "gpt-5.4-mini"),
        float(os.getenv("USD_KRW_RATE", "1507.2") or "1507.2"),
        settings.prompts,
    )
    remember.configure_delays(os.getenv("MIN_DELAY_SECONDS", "1"), os.getenv("MAX_DELAY_SECONDS", "3"))
    return settings


@app.post("/api/runs")
async def create_run(request: RunCreateRequest):
    if not request.jd_text.strip():
        raise HTTPException(status_code=400, detail="JD 본문을 입력하세요.")
    settings = settings_store.load()
    request = request.model_copy(update={"test_mode": settings.confirm_before_proposal_send})
    remember_adapter = None
    if settings.crawler_mode == "browser":
        if _provider() == "saramin":
            if not selected_saramin_offer_position:
                raise HTTPException(status_code=400, detail="Saramin offer position is not selected.")
            remember_adapter = _saramin_adapter(settings, selected_saramin_offer_position)
            try:
                search_count = await remember_adapter.inspect_search_result_count()
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Saramin search result check failed: {exc}") from exc
            if not search_count.get("ok"):
                raise HTTPException(
                    status_code=400,
                    detail=str(search_count.get("reason") or "Saramin search candidates were not found."),
                )
            return run_manager.create_run(request, remember_adapter=remember_adapter)
        remember_adapter = BrowserRememberAdapter(
            RememberBrowserConfig(
                cdp_url=settings.remember_cdp_url,
                remember_url=settings.remember_url,
                locale=settings.browser_locale,
                accept_language=settings.browser_accept_language,
                timezone=settings.browser_timezone,
                skip_proposal_send=settings.remember_skip_proposal_send,
            )
        )
        try:
            search_count = await remember_adapter.inspect_search_result_count()
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"리멤버 검색 결과 수 확인에 실패했습니다: {exc}",
            ) from exc
        if not search_count.get("ok"):
            raise HTTPException(
                status_code=400,
                detail=str(search_count.get("reason") or "리멤버 검색 결과 수를 확인하지 못했습니다."),
            )
        available_count = search_count.get("count")
        if type(available_count) is int:
            requested_count = request.max_candidate_count
            effective_count = available_count if requested_count is None else min(requested_count, available_count)
            request = request.model_copy(update={"max_candidate_count": max(effective_count, 1)})
    return run_manager.create_run(request, remember_adapter=remember_adapter)


@app.post("/api/remember/html-test")
async def remember_html_test():
    settings = settings_store.load()
    adapter = BrowserRememberAdapter(
        RememberBrowserConfig(
            cdp_url=settings.remember_cdp_url,
            remember_url=settings.remember_url,
            locale=settings.browser_locale,
            accept_language=settings.browser_accept_language,
            timezone=settings.browser_timezone,
            skip_proposal_send=settings.remember_skip_proposal_send,
        )
    )
    try:
        return await adapter.inspect_active_tab()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    state = run_manager.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="실행을 찾을 수 없습니다.")
    return state


@app.get("/api/runs/{run_id}/summary")
async def download_run_summary(run_id: str):
    state = run_manager.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="실행을 찾을 수 없습니다.")
    content = _build_run_summary(state)
    filename = f"headhunting-summary-{run_id}.md"
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    state = await run_manager.cancel(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="실행을 찾을 수 없습니다.")
    return state


@app.post("/api/runs/{run_id}/send-selected")
async def send_selected(run_id: str, request: SendSelectedRequest):
    state = await run_manager.send_selected(run_id, request)
    if not state:
        raise HTTPException(status_code=404, detail="실행을 찾을 수 없습니다.")
    return state


@app.websocket("/ws/runs/{run_id}")
async def run_socket(websocket: WebSocket, run_id: str):
    await websocket.accept()
    queue = await run_manager.subscribe(run_id)
    try:
        while True:
            payload = await queue.get()
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        run_manager.unsubscribe(run_id, queue)


def _md_cell(value) -> str:
    value = getattr(value, "value", value)
    return str(value if value is not None else "").replace("\n", " ").replace("|", "\\|")


def _md_block(value) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        return "-"
    return "```text\n" + text.replace("```", "'''") + "\n```"


def _md_heading(value) -> str:
    text = str(value if value is not None else "").strip() or "이름 없음"
    return text.replace("#", "").replace("\n", " ")


def _krw(value: float) -> str:
    return f"₩{round(float(value or 0)):,}"


def _build_run_summary(state) -> str:
    stats = state.stats
    usage = state.usage
    config = state.config
    active_threshold = state.send_threshold if state.send_threshold is not None else config.threshold
    max_candidates = "무제한" if config.max_candidate_count is None else f"{config.max_candidate_count}명"
    pricing = "등록됨" if usage.pricing_known else "미등록"
    lines = [
        "# 헤드헌팅 실행 상세 리포트",
        "",
        "## 실행 정보",
        "",
        f"- 실행 ID: `{state.run_id}`",
        f"- 상태: `{getattr(state.status, 'value', state.status)}`",
        f"- 시작: {state.started_at or '-'}",
        f"- 완료: {state.completed_at or '-'}",
        f"- 중단/완료 사유: {state.stop_reason or '-'}",
        f"- 확인 후 제안서 발송: {'ON' if config.test_mode else 'OFF'}",
        f"- 통과 기준 점수: {config.threshold}점",
        f"- 발송 기준 점수: {active_threshold}점",
        f"- 처리 상한: {max_candidates}",
        "",
        "## 처리 요약",
        "",
        f"- 검색 결과: {stats.total}명",
        f"- 분석 완료: {stats.processed}명",
        f"- 발송 기준 통과: {stats.passed}명",
        f"- 발송 대상 선택: {stats.selected}명",
        f"- 발송 완료: {stats.sent}명",
        f"- 발송 제외: {stats.excluded}명",
        f"- 발송 스킵: {stats.skipped}명",
        f"- 발송 실패: {stats.failed}명",
        f"- JD 글자 수: {len(config.jd_text):,}자",
        "",
        "## API 사용량",
        "",
        f"- 모델: {usage.model or '-'}",
        f"- 입력 토큰: {usage.input_tokens:,}",
        f"- 캐시 입력 토큰: {usage.cached_input_tokens:,}",
        f"- 출력 토큰: {usage.output_tokens:,}",
        f"- 전체 토큰: {usage.total_tokens:,}",
        f"- 예상 비용: ${usage.total_cost_usd:.6f} / {_krw(usage.total_cost_krw)}",
        f"- 환율: {usage.usd_krw_rate:,.2f}원",
        f"- 단가 정보: {pricing}",
        "",
        "## JD 원문",
        "",
        _md_block(config.jd_text),
        "",
        "## 후보자 결과 한눈에 보기",
        "",
        "| 후보자 | 회사 | 직무 | 경력 | 지역 | 점수 | 통과 | 발송 상태 | 사유 |",
        "| --- | --- | --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for result in state.results:
        candidate = result.candidate
        match = result.match
        lines.append(
            "| "
            + " | ".join(
                [
                    _md_cell(candidate.name),
                    _md_cell(candidate.company),
                    _md_cell(candidate.role),
                    _md_cell(candidate.experience),
                    _md_cell(candidate.location),
                    _md_cell(match.total_score if match else "-"),
                    "Y" if match and match.total_score >= active_threshold else "N",
                    _md_cell(result.send_status),
                    _md_cell((match.reason if match else "") or result.send_reason or ""),
                ]
            )
            + " |"
        )
    lines.extend(["", "## 후보자별 상세 내용", ""])
    for index, result in enumerate(state.results, start=1):
        candidate = result.candidate
        match = result.match
        score = match.total_score if match else "-"
        passed = "Y" if match and match.total_score >= active_threshold else "N"
        send_status = getattr(result.send_status, "value", result.send_status)
        match_source = match.source if match else "-"
        match_reason = match.reason if match else "-"
        send_reason = result.send_reason or "-"
        skills = ", ".join(candidate.skills) if candidate.skills else "-"

        lines.extend(
            [
                "",
                f"### {index}. {_md_heading(candidate.name)}",
                "",
                "#### 크롤링 데이터",
                "",
                "| 항목 | 내용 |",
                "| --- | --- |",
                f"| 후보자 ID | {_md_cell(candidate.id)} |",
                f"| 원본 페이지 | {_md_cell(getattr(candidate, 'remember_page_number', None) or '-')} |",
                f"| 원본 카드 ID | {_md_cell(getattr(candidate, 'remember_profile_card_id', None) or '-')} |",
                f"| 원본 카드 순번 | {_md_cell(getattr(candidate, 'remember_card_index', None) if getattr(candidate, 'remember_card_index', None) is not None else '-')} |",
                f"| 이름 | {_md_cell(candidate.name)} |",
                f"| 회사 | {_md_cell(candidate.company)} |",
                f"| 직무 | {_md_cell(candidate.role)} |",
                f"| 경력 | {_md_cell(candidate.experience)} |",
                f"| 지역 | {_md_cell(candidate.location)} |",
                f"| 스킬 | {_md_cell(skills)} |",
                "",
                "#### 매칭 결과",
                "",
                "| 항목 | 내용 |",
                "| --- | --- |",
                f"| 점수 | {_md_cell(score)} |",
                f"| 통과 여부 | {passed} |",
                f"| 분석 출처 | {_md_cell(match_source)} |",
                f"| 매칭 사유 | {_md_cell(match_reason)} |",
                f"| 발송 상태 | {_md_cell(send_status)} |",
                f"| 발송 사유 | {_md_cell(send_reason)} |",
            ]
        )
        if match and match.item_scores:
            lines.extend(
                [
                    "",
                    "#### 항목별 점수",
                    "",
                    "| 항목 | 가중치 | 점수 | 메모 |",
                    "| --- | ---: | ---: | --- |",
                ]
            )
            for item in match.item_scores:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _md_cell(item.name),
                            _md_cell(item.weight),
                            _md_cell(item.score),
                            _md_cell(item.note),
                        ]
                    )
                    + " |"
                )
        lines.extend(
            [
                "",
                "#### API 전송 인재 text",
                "",
                _md_block(redact_candidate_name(candidate.resume_text, candidate.name)),
            ]
        )
    lines.extend(
        [
            "",
            "## 실행 로그",
            "",
            *(f"- {log}" for log in state.logs),
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "8000")),
        reload=True,
    )
