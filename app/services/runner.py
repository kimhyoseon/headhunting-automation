from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import uuid4

from app.models import (
    CandidateResult,
    RunCreateRequest,
    RunState,
    RunStats,
    RunStatus,
    SendSelectedRequest,
    SendStatus,
    LLMUsage,
)
from app.services.llm import LLMService
from app.services.privacy import redact_candidate_name
from app.services.remember import MockRememberAdapter


class RunManager:
    def __init__(self, llm: LLMService, remember: MockRememberAdapter) -> None:
        self.llm = llm
        self.remember = remember
        self.remember_adapters: dict[str, object] = {}
        self.runs: dict[str, RunState] = {}
        self.subscribers: dict[str, set[asyncio.Queue[dict]]] = {}
        self.tasks: dict[str, asyncio.Task] = {}

    def create_run(self, config: RunCreateRequest, remember_adapter: object | None = None) -> RunState:
        run_id = f"R-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:5]}"
        state = RunState(run_id=run_id, status=RunStatus.queued, config=config)
        adapter = remember_adapter or self.remember
        self.remember_adapters[run_id] = adapter
        self.runs[run_id] = state
        task = asyncio.create_task(self._run(state, adapter))
        self.tasks[run_id] = task
        return state

    def get(self, run_id: str) -> RunState | None:
        return self.runs.get(run_id)

    async def pause(self, run_id: str) -> RunState | None:
        state = self.get(run_id)
        if state and state.status == RunStatus.running:
            state.status = RunStatus.paused
            self._log(state, "사용자 요청으로 일시정지되었습니다.")
            await self._publish_state(state)
        elif state and state.status == RunStatus.paused:
            state.status = RunStatus.running
            self._log(state, "사용자 요청으로 실행을 재개했습니다.")
            await self._publish_state(state)
        return state

    async def cancel(self, run_id: str) -> RunState | None:
        state = self.get(run_id)
        if state and state.status in {RunStatus.queued, RunStatus.running, RunStatus.paused, RunStatus.ready_to_send, RunStatus.sending}:
            state.status = RunStatus.cancelled
            state.stop_reason = "사용자 중단"
            self._log(state, "사용자 요청으로 실행을 중단했습니다.")
            await self._publish_state(state)
        return state

    async def send_selected(self, run_id: str, request: SendSelectedRequest) -> RunState | None:
        state = self.get(run_id)
        if not state:
            return None
        if state.status not in {RunStatus.ready_to_send, RunStatus.completed}:
            return state
        legacy_test_hold = state.config.test_mode and state.status == RunStatus.completed and state.send_threshold is None
        if state.config.test_mode and state.status != RunStatus.ready_to_send and not legacy_test_hold:
            self._log(state, "테스트모드 실행은 발송 대기 단계에서만 수동 발송할 수 있습니다.")
            await self._publish_state(state)
            return state
        state.status = RunStatus.sending
        state.send_threshold = request.threshold
        state.stop_reason = None
        self._log(state, f"발송을 시작합니다. 기준 {request.threshold}점, 선택 {len(request.candidate_ids)}명.")
        await self._publish_state(state)

        ids = set(request.candidate_ids)
        consecutive_send_failures = 0
        for result in state.results:
            if result.send_status == SendStatus.sent:
                continue
            if not result.match:
                result.send_status = SendStatus.skipped
                result.send_reason = "매칭 결과 없음"
                continue
            if result.candidate.id not in ids:
                result.send_status = SendStatus.skipped
                result.send_reason = "선택 제외"
                continue
            if result.match.total_score < request.threshold:
                result.send_status = SendStatus.skipped
                result.send_reason = "발송 기준 미달"
                continue
            remember_adapter = self.remember_adapters.get(run_id, self.remember)
            ok, reason = await remember_adapter.send_proposal(result.candidate)
            if ok:
                if str(reason or "").startswith("DRY RUN"):
                    result.send_status = SendStatus.skipped
                else:
                    result.send_status = SendStatus.sent
                result.send_reason = reason
                consecutive_send_failures = 0
                self._log(state, f"제안 발송 성공 - {result.candidate.id} ({result.candidate.name})")
            else:
                result.send_status = SendStatus.failed
                result.send_reason = reason
                consecutive_send_failures += 1
                self._log(state, f"제안 발송 실패 - {result.candidate.id}: {reason}")
                if consecutive_send_failures >= 3:
                    state.status = RunStatus.failed
                    state.stop_reason = "연속 발송 실패 3건"
                    state.completed_at = datetime.now().isoformat(timespec="seconds")
                    self._refresh_stats(state, request.threshold)
                    self._log(state, "이상 감지로 실행을 자동 정지했습니다.")
                    await self._publish_state(state)
                    return state
            self._refresh_stats(state, request.threshold)
            await self._publish_state(state)
        self._refresh_stats(state, request.threshold)
        state.status = RunStatus.completed
        state.completed_at = datetime.now().isoformat(timespec="seconds")
        state.stop_reason = "발송 완료"
        self._log(state, "선택 후보자 발송 처리가 완료되었습니다.")
        await self._publish_state(state)
        return state

    async def subscribe(self, run_id: str) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue()
        self.subscribers.setdefault(run_id, set()).add(queue)
        state = self.get(run_id)
        if state:
            await queue.put({"type": "state", "state": state.model_dump(mode="json")})
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[dict]) -> None:
        self.subscribers.get(run_id, set()).discard(queue)

    async def _run(self, state: RunState, remember_adapter: object) -> None:
        state.status = RunStatus.running
        state.stage = "crawling"
        state.started_at = datetime.now().isoformat(timespec="seconds")
        if state.config.max_candidate_count is not None:
            state.stats.total = state.config.max_candidate_count
        adapter_name = "브라우저 리멤버 어댑터" if remember_adapter is not self.remember else "더미 리멤버 어댑터"
        self._log(state, f"{adapter_name}로 검색을 시작합니다.")
        await self._publish_state(state)

        previous_progress_callback = getattr(remember_adapter, "progress_callback", None)

        async def on_crawl_progress(progress: dict) -> None:
            state.stage = "crawling"
            requested = progress.get("requestedLimit")
            collected = int(progress.get("crawledCount") or progress.get("totalCollected") or 0)
            if requested:
                state.stats.total = int(requested)
            elif state.config.max_candidate_count is not None:
                state.stats.total = state.config.max_candidate_count
            else:
                state.stats.total = max(state.stats.total, collected)
            if state.stats.total:
                collected = min(collected, state.stats.total)
            state.stats.crawled = collected
            await self._publish_state(state)

        if hasattr(remember_adapter, "progress_callback"):
            remember_adapter.progress_callback = on_crawl_progress

        try:
            candidates = await remember_adapter.search(state.config.max_candidate_count)
        except Exception as exc:
            state.status = RunStatus.failed
            state.stage = "failed"
            state.stop_reason = str(exc)
            state.completed_at = datetime.now().isoformat(timespec="seconds")
            self._log(state, f"리멤버 검색 실패 - {exc}")
            await self._publish_state(state)
            return
        finally:
            if hasattr(remember_adapter, "progress_callback"):
                remember_adapter.progress_callback = previous_progress_callback
        state.stats = RunStats(total=len(candidates))
        state.stats.crawled = len(candidates)
        state.stage = "matching"
        process_label = "무제한" if state.config.max_candidate_count is None else f"{state.config.max_candidate_count}명"
        self._log(state, f"검색 결과 {len(candidates)}명을 불러왔습니다. 처리 상한 {process_label}.")
        crawl_result = getattr(remember_adapter, "last_crawl_result", None)
        if isinstance(crawl_result, dict):
            pages = crawl_result.get("pages") or []
            if pages:
                page_summary = ", ".join(
                    f"{page.get('pageNumber')}p {page.get('collected')}명"
                    for page in pages[:10]
                )
                self._log(state, f"리멤버 페이지 수집 내역: {page_summary}")
        await self._publish_state(state)

        for index, candidate in enumerate(candidates, start=1):
            if state.status == RunStatus.cancelled:
                break
            while state.status == RunStatus.paused:
                await asyncio.sleep(0.5)

            state.current_candidate = candidate
            self._log(state, f"후보자 {index}/{len(candidates)} 상세 페이지 열람 - {candidate.name} ({candidate.company})")
            await self._publish_state(state)

            opened = await remember_adapter.open_candidate(candidate)
            api_resume_text = self._candidate_text_for_api(opened)
            match = await self.llm.match_candidate(
                state.config.jd_text,
                api_resume_text,
                state.config.threshold,
                debug_log=state.config.test_mode,
                run_id=state.run_id,
                candidate_id=opened.id,
            )
            state.usage = self._combine_usage(state.usage, match.usage)
            result = CandidateResult(
                candidate=opened,
                match=match,
                send_status=SendStatus.pending,
            )
            state.results.append(result)
            state.stats.processed += 1
            if match.passed:
                state.stats.passed += 1
                self._log(state, f"매칭 통과 - {candidate.id} {match.total_score}점")
            else:
                self._log(state, f"매칭 탈락 - {candidate.id} {match.total_score}점")
            await self._publish_state(state)

        if state.status != RunStatus.cancelled:
            state.status = RunStatus.ready_to_send
            state.stage = "ready_to_send"
            state.current_candidate = None
            state.stop_reason = "분석 완료 - 발송 대기"
            self._log(state, "모든 후보자 분석이 완료되었습니다. 발송 단계로 이동합니다.")
            await self._publish_state(state)

    def _candidate_text_for_api(self, candidate) -> str:
        return redact_candidate_name(
            str(getattr(candidate, "resume_text", "") or ""),
            str(getattr(candidate, "name", "") or ""),
        )

    def _combine_usage(self, current: LLMUsage, incoming: LLMUsage) -> LLMUsage:
        if not incoming.total_tokens:
            return current
        had_usage = current.total_tokens > 0
        rate = incoming.usd_krw_rate or current.usd_krw_rate
        total_cost_usd = current.total_cost_usd + incoming.total_cost_usd
        return LLMUsage(
            model=incoming.model or current.model,
            input_tokens=current.input_tokens + incoming.input_tokens,
            cached_input_tokens=current.cached_input_tokens + incoming.cached_input_tokens,
            output_tokens=current.output_tokens + incoming.output_tokens,
            total_tokens=current.total_tokens + incoming.total_tokens,
            input_cost_usd=round(current.input_cost_usd + incoming.input_cost_usd, 8),
            cached_input_cost_usd=round(current.cached_input_cost_usd + incoming.cached_input_cost_usd, 8),
            output_cost_usd=round(current.output_cost_usd + incoming.output_cost_usd, 8),
            total_cost_usd=round(total_cost_usd, 8),
            total_cost_krw=round(total_cost_usd * rate, 2),
            usd_krw_rate=rate,
            pricing_known=(current.pricing_known and incoming.pricing_known) if had_usage else incoming.pricing_known,
        )

    def _refresh_stats(self, state: RunState, threshold: int | None = None) -> None:
        active_threshold = threshold if threshold is not None else state.config.threshold
        state.stats.total = state.stats.total or len(state.results)
        state.stats.processed = len(state.results)
        state.stats.passed = sum(
            1 for result in state.results if result.match and result.match.total_score >= active_threshold
        )
        state.stats.sent = sum(1 for result in state.results if result.send_status == SendStatus.sent)
        state.stats.failed = sum(1 for result in state.results if result.send_status == SendStatus.failed)

    def _log(self, state: RunState, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        state.logs.insert(0, f"{stamp} {message}")
        state.logs = state.logs[:200]

    async def _publish_state(self, state: RunState) -> None:
        payload = {"type": "state", "state": state.model_dump(mode="json")}
        for queue in list(self.subscribers.get(state.run_id, set())):
            await queue.put(payload)
