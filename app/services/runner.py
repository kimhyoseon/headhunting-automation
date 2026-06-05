from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import uuid4

from app.models import (
    CandidateResult,
    LLMUsage,
    RunCreateRequest,
    RunState,
    RunStats,
    RunStatus,
    SendSelectedRequest,
    SendStatus,
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
        if hasattr(adapter, "reset_cancel"):
            adapter.reset_cancel()
        self.remember_adapters[run_id] = adapter
        self.runs[run_id] = state
        self.tasks[run_id] = asyncio.create_task(self._run(state, adapter))
        return state

    def get(self, run_id: str) -> RunState | None:
        return self.runs.get(run_id)

    async def cancel(self, run_id: str) -> RunState | None:
        state = self.get(run_id)
        cancellable = {
            RunStatus.queued,
            RunStatus.running,
            RunStatus.ready_to_send,
            RunStatus.sending,
        }
        if state and state.status in cancellable:
            adapter = self.remember_adapters.get(run_id)
            if adapter and hasattr(adapter, "request_cancel"):
                adapter.request_cancel()
            task = self.tasks.get(run_id)
            if task and not task.done():
                task.cancel()
            self._mark_cancelled(state)
            self._log(state, "Cancelled by user.")
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
            self._log(state, "Manual proposal sending is only available from the send-ready stage.")
            await self._publish_state(state)
            return state

        ids = set(request.candidate_ids)
        state.stats.selected = sum(
            1
            for result in state.results
            if result.candidate.id in ids
            and result.match
            and result.match.total_score >= request.threshold
        )
        state.status = RunStatus.sending
        state.stage = "sending"
        state.send_threshold = request.threshold
        state.stop_reason = None
        self._log(state, f"Sending started. threshold={request.threshold}, selected={state.stats.selected}")
        await self._publish_state(state)

        consecutive_send_failures = 0
        remember_adapter = self.remember_adapters.get(run_id, self.remember)
        for result in state.results:
            if state.status == RunStatus.cancelled:
                return state
            if result.send_status == SendStatus.sent:
                continue
            if not result.match:
                result.send_status = SendStatus.excluded
                result.send_reason = "No match result"
                consecutive_send_failures = 0
                continue
            if result.candidate.id not in ids:
                result.send_status = SendStatus.excluded
                result.send_reason = "Not selected"
                consecutive_send_failures = 0
                continue
            if result.match.total_score < request.threshold:
                result.send_status = SendStatus.excluded
                result.send_reason = "Below send threshold"
                consecutive_send_failures = 0
                continue

            try:
                send_status, reason = self._normalize_send_result(
                    await remember_adapter.send_proposal(result.candidate)
                )
            except asyncio.CancelledError:
                self._mark_cancelled(state)
                await self._publish_state(state)
                raise
            if state.status == RunStatus.cancelled:
                return state

            if send_status == SendStatus.sent:
                result.send_status = SendStatus.sent
                result.send_reason = reason
                consecutive_send_failures = 0
                self._log(state, f"Proposal sent - {result.candidate.id} ({result.candidate.name})")
            elif send_status == SendStatus.skipped:
                result.send_status = SendStatus.skipped
                result.send_reason = reason
                consecutive_send_failures = 0
                self._log(state, f"Proposal skipped - {result.candidate.id}: {reason}")
            else:
                result.send_status = SendStatus.failed
                result.send_reason = reason
                consecutive_send_failures += 1
                self._log(state, f"Proposal failed - {result.candidate.id}: {reason}")
                if consecutive_send_failures >= 3:
                    state.status = RunStatus.failed
                    state.stage = "failed"
                    state.stop_reason = "Three consecutive proposal failures"
                    state.completed_at = datetime.now().isoformat(timespec="seconds")
                    self._refresh_stats(state, request.threshold)
                    self._log(state, "Stopped after three consecutive proposal failures.")
                    await self._publish_state(state)
                    return state

            self._refresh_stats(state, request.threshold)
            await self._publish_state(state)

        self._refresh_stats(state, request.threshold)
        state.status = RunStatus.completed
        state.stage = "completed"
        state.completed_at = datetime.now().isoformat(timespec="seconds")
        state.stop_reason = "Proposal sending completed"
        self._log(state, "Selected proposal sending completed.")
        await self._publish_state(state)
        return state

    def _normalize_send_result(self, send_result) -> tuple[SendStatus, str]:
        raw_status = send_result
        reason = ""
        if isinstance(send_result, tuple):
            raw_status = send_result[0] if len(send_result) >= 1 else False
            reason = str(send_result[1] if len(send_result) >= 2 else "")

        if isinstance(raw_status, SendStatus):
            return raw_status, reason
        if raw_status is True:
            return SendStatus.sent, reason
        if raw_status is None:
            return SendStatus.skipped, reason
        return SendStatus.failed, reason

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
        adapter_name = str(getattr(remember_adapter, "display_name", "") or "candidate browser adapter")
        provider_name = str(getattr(remember_adapter, "provider_name", "") or "Provider")
        self._log(state, f"Search started with {adapter_name}.")
        await self._publish_state(state)

        previous_progress_callback = getattr(remember_adapter, "progress_callback", None)
        last_logged_crawl_count = 0

        async def on_crawl_progress(progress: dict) -> None:
            nonlocal last_logged_crawl_count
            if state.status == RunStatus.cancelled:
                raise asyncio.CancelledError()
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
            current_name = str(progress.get("currentName") or "").strip()
            if current_name:
                state.current_crawl_name = current_name
            if collected > last_logged_crawl_count:
                last_logged_crawl_count = collected
                label = f"{collected}/{state.stats.total or '?'}"
                if current_name:
                    self._log(state, f"Crawled {label} - {current_name}")
                else:
                    self._log(state, f"Crawled {label}")
            await self._publish_state(state)

        if hasattr(remember_adapter, "progress_callback"):
            remember_adapter.progress_callback = on_crawl_progress

        try:
            candidates = await remember_adapter.search(state.config.max_candidate_count)
        except asyncio.CancelledError:
            self._mark_cancelled(state)
            await self._publish_state(state)
            raise
        except Exception as exc:
            state.status = RunStatus.failed
            state.stage = "failed"
            state.stop_reason = str(exc)
            state.completed_at = datetime.now().isoformat(timespec="seconds")
            self._log(state, f"{provider_name} search failed - {exc}")
            await self._publish_state(state)
            return
        finally:
            if hasattr(remember_adapter, "progress_callback"):
                remember_adapter.progress_callback = previous_progress_callback

        if state.status == RunStatus.cancelled:
            return

        state.current_crawl_name = None
        state.stats = RunStats(total=len(candidates), crawled=len(candidates))
        state.stage = "matching"
        process_label = "unlimited" if state.config.max_candidate_count is None else f"{state.config.max_candidate_count}"
        self._log(state, f"Loaded {len(candidates)} candidates. requested={process_label}.")

        crawl_result = getattr(remember_adapter, "last_crawl_result", None)
        if isinstance(crawl_result, dict):
            pages = crawl_result.get("pages") or []
            if pages:
                page_summary = ", ".join(f"{page.get('pageNumber')}p {page.get('collected')}" for page in pages[:10])
                self._log(state, f"{provider_name} page crawl summary: {page_summary}")
        await self._publish_state(state)

        for index, candidate in enumerate(candidates, start=1):
            if state.status == RunStatus.cancelled:
                break
            if state.status == RunStatus.cancelled:
                break

            state.current_candidate = candidate
            self._log(state, f"Matching {index}/{len(candidates)} - {candidate.name} ({candidate.company})")
            await self._publish_state(state)

            try:
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
            except asyncio.CancelledError:
                self._mark_cancelled(state)
                await self._publish_state(state)
                raise

            state.usage = self._combine_usage(state.usage, match.usage)
            result = CandidateResult(candidate=opened, match=match, send_status=SendStatus.pending)
            state.results.append(result)
            state.stats.processed += 1
            if match.passed:
                state.stats.passed += 1
                self._log(state, f"Match passed - {candidate.id} {match.total_score}")
            else:
                self._log(state, f"Match failed - {candidate.id} {match.total_score}")
            await self._publish_state(state)

        if state.status != RunStatus.cancelled:
            state.status = RunStatus.ready_to_send
            state.stage = "ready_to_send"
            state.current_candidate = None
            state.current_crawl_name = None
            state.stop_reason = "Analysis completed - ready to send"
            self._log(state, "All candidates analyzed. Moving to send stage.")
            await self._publish_state(state)

    def _mark_cancelled(self, state: RunState) -> None:
        state.status = RunStatus.cancelled
        state.stage = "cancelled"
        state.current_candidate = None
        state.current_crawl_name = None
        state.stop_reason = "User cancelled"
        state.completed_at = datetime.now().isoformat(timespec="seconds")

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
        state.stats.excluded = sum(1 for result in state.results if result.send_status == SendStatus.excluded)
        state.stats.skipped = sum(1 for result in state.results if result.send_status == SendStatus.skipped)
        state.stats.failed = sum(1 for result in state.results if result.send_status == SendStatus.failed)
        state.stats.consecutive_failed = self._consecutive_send_failures(state.results)

    def _consecutive_send_failures(self, results: list[CandidateResult]) -> int:
        count = 0
        for result in results:
            if result.send_status == SendStatus.failed:
                count += 1
            elif result.send_status in {SendStatus.sent, SendStatus.skipped, SendStatus.excluded}:
                count = 0
        return count

    def _log(self, state: RunState, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        state.logs.insert(0, f"{stamp} {message}")
        state.logs = state.logs[:200]

    async def _publish_state(self, state: RunState) -> None:
        payload = {"type": "state", "state": state.model_dump(mode="json")}
        for queue in list(self.subscribers.get(state.run_id, set())):
            await queue.put(payload)
