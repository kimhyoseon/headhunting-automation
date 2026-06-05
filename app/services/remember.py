from __future__ import annotations

import asyncio
import os
import random

from app.mock_data import build_mock_candidates
from app.models import Candidate


class MockRememberAdapter:
    """Dummy implementation used until Remember access is available."""

    def __init__(self) -> None:
        self.display_name = "mock Remember adapter"
        self.provider_name = "Remember"
        self.candidates = build_mock_candidates()
        self._cancel_requested = False
        self.configure_delays(os.getenv("MIN_DELAY_SECONDS", "1"), os.getenv("MAX_DELAY_SECONDS", "3"))

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def reset_cancel(self) -> None:
        self._cancel_requested = False

    def _raise_if_cancelled(self) -> None:
        if self._cancel_requested:
            raise asyncio.CancelledError()

    def configure_delays(self, min_delay: str | float, max_delay: str | float) -> None:
        self.min_delay = max(0, float(min_delay))
        self.max_delay = max(0, float(max_delay))

    async def search(self, limit: int | None = None) -> list[Candidate]:
        self._raise_if_cancelled()
        await asyncio.sleep(0.5)
        self._raise_if_cancelled()
        if limit is None:
            return self.candidates
        return self.candidates[:limit]

    async def open_candidate(self, candidate: Candidate) -> Candidate:
        await self._human_delay()
        self._raise_if_cancelled()
        return candidate

    async def send_proposal(self, candidate: Candidate) -> tuple[bool, str]:
        await self._human_delay()
        self._raise_if_cancelled()
        if candidate.id.endswith("07"):
            return False, "발송 차단 - 7일 이내 동일 후보자 접촉"
        if candidate.id.endswith("14"):
            return False, "제안 메시지 전송 타임아웃"
        return True, "리멤버 기본 제안 버튼 발송 완료"

    async def _human_delay(self) -> None:
        lo = min(self.min_delay, self.max_delay)
        hi = max(self.min_delay, self.max_delay)
        await asyncio.sleep(random.uniform(lo, hi))
