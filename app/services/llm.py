from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from openai import AsyncOpenAI

from app.models import LLMUsage, MatchResult, PromptSettings


DEFAULT_MODEL = "gpt-5.4-mini"
DEBUG_LOG_DIR = Path(__file__).resolve().parents[2] / "logs" / "openai"

FLAGSHIP_PRICING = {
    "gpt-5.5": {"input": 5.0, "cached_input": 0.5, "output": 30.0},
    "gpt-5.5-pro": {"input": 30.0, "cached_input": None, "output": 180.0},
    "gpt-5.4": {"input": 2.5, "cached_input": 0.25, "output": 15.0},
    "gpt-5.4-mini": {"input": 0.75, "cached_input": 0.075, "output": 4.5},
    "gpt-5.4-nano": {"input": 0.2, "cached_input": 0.02, "output": 1.25},
    "gpt-5.4-pro": {"input": 30.0, "cached_input": None, "output": 180.0},
}


class LLMService:
    def __init__(self) -> None:
        self.configure(
            os.getenv("OPENAI_API_KEY", "").strip(),
            os.getenv("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            float(os.getenv("USD_KRW_RATE", "1507.2") or "1507.2"),
            PromptSettings(),
        )

    def configure(
        self,
        api_key: str,
        model: str,
        usd_krw_rate: float = 1507.2,
        prompts: PromptSettings | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.model = model.strip() or DEFAULT_MODEL
        self.usd_krw_rate = usd_krw_rate
        self.prompts = prompts or PromptSettings()
        self.client = AsyncOpenAI(api_key=self.api_key) if self.api_key else None

    async def match_candidate(
        self,
        jd_text: str,
        resume_text: str,
        threshold: int,
        debug_log: bool = False,
        run_id: str | None = None,
        candidate_id: str | None = None,
    ) -> MatchResult:
        if not self.client:
            return self._fallback_match(jd_text, resume_text, threshold, "demo")

        context = {
            "jd_text": jd_text[:9000],
            "resume_text": resume_text[:9000],
        }
        messages = [
            {"role": "system", "content": self.prompts.match_system_prompt},
            {"role": "user", "content": self._render_template(self.prompts.match_user_prompt, context)},
        ]
        request = {
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }
        try:
            response = await self.client.chat.completions.create(**request, timeout=30)
            raw = response.choices[0].message.content or "{}"
            data = json.loads(raw)
            total = self._score_value(data)
            result = MatchResult(
                total_score=total,
                passed=total >= threshold,
                reason=self._short_reason(self._reason_value(data) or self._reason_for(total, threshold)),
                item_scores=[],
                source="openai",
                usage=self._usage_from_response(response),
            )
            self._write_debug_log(
                enabled=debug_log,
                call_type="match",
                request=request,
                response=response,
                raw_content=raw,
                parsed_result=result.model_dump(mode="json"),
                metadata={"run_id": run_id, "candidate_id": candidate_id, "threshold": threshold},
            )
            return result
        except Exception as exc:
            self._write_debug_log(
                enabled=debug_log,
                call_type="match",
                request=request,
                error=repr(exc),
                metadata={"run_id": run_id, "candidate_id": candidate_id, "threshold": threshold},
            )
            return self._fallback_match(jd_text, resume_text, threshold, "fallback")

    def _fallback_match(
        self,
        jd_text: str,
        resume_text: str,
        threshold: int,
        source: str,
    ) -> MatchResult:
        jd = jd_text.lower()
        resume = resume_text.lower()
        words = {
            word.strip(".,;:()[]{}<>\"'").lower()
            for word in jd.replace("/", " ").replace("-", " ").split()
            if len(word.strip(".,;:()[]{}<>\"'")) >= 2
        }
        matched = sorted(word for word in words if word in resume)
        total = min(95, 55 + len(matched) * 4)
        reason = self._reason_for(total, threshold)
        if matched:
            reason = "핵심 키워드 일치"
        return MatchResult(
            total_score=total,
            passed=total >= threshold,
            reason=self._short_reason(reason),
            item_scores=[],
            source=source,
            usage=LLMUsage(model=self.model, usd_krw_rate=self.usd_krw_rate),
        )

    def _usage_from_response(self, response: Any) -> LLMUsage:
        usage = getattr(response, "usage", None)
        if not usage:
            return LLMUsage(model=self.model, usd_krw_rate=self.usd_krw_rate)

        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", input_tokens + output_tokens) or 0)
        details = getattr(usage, "prompt_tokens_details", None)
        cached_tokens = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
        cached_tokens = max(0, min(cached_tokens, input_tokens))
        billable_input_tokens = input_tokens - cached_tokens

        pricing = FLAGSHIP_PRICING.get(self.model)
        if not pricing:
            return LLMUsage(
                model=self.model,
                input_tokens=input_tokens,
                cached_input_tokens=cached_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                usd_krw_rate=self.usd_krw_rate,
                pricing_known=False,
            )

        cached_price = pricing["cached_input"] if pricing["cached_input"] is not None else pricing["input"]
        input_cost = billable_input_tokens * pricing["input"] / 1_000_000
        cached_cost = cached_tokens * cached_price / 1_000_000
        output_cost = output_tokens * pricing["output"] / 1_000_000
        total_cost = input_cost + cached_cost + output_cost
        return LLMUsage(
            model=self.model,
            input_tokens=input_tokens,
            cached_input_tokens=cached_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            input_cost_usd=round(input_cost, 8),
            cached_input_cost_usd=round(cached_cost, 8),
            output_cost_usd=round(output_cost, 8),
            total_cost_usd=round(total_cost, 8),
            total_cost_krw=round(total_cost * self.usd_krw_rate, 2),
            usd_krw_rate=self.usd_krw_rate,
            pricing_known=True,
        )

    def _write_debug_log(
        self,
        *,
        enabled: bool,
        call_type: str,
        request: dict[str, Any],
        response: Any | None = None,
        raw_content: str | None = None,
        parsed_result: dict[str, Any] | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not enabled:
            return
        DEBUG_LOG_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        payload = {
            "timestamp": now.isoformat(timespec="seconds"),
            "call_type": call_type,
            "metadata": metadata or {},
            "request": request,
            "raw_content": raw_content,
            "response": self._to_jsonable(response),
            "parsed_result": parsed_result,
            "error": error,
        }
        filename = f"{now.strftime('%Y%m%d-%H%M%S')}-{call_type}-{uuid4().hex[:8]}.json"
        (DEBUG_LOG_DIR / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _to_jsonable(self, value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        try:
            json.dumps(value, ensure_ascii=False)
            return value
        except TypeError:
            return str(value)

    def _render_template(self, template: str, values: dict[str, str]) -> str:
        rendered = template or ""
        for key, value in values.items():
            rendered = rendered.replace("{" + key + "}", value)
        return rendered

    def _score_value(self, data: dict[str, Any]) -> int:
        raw = data.get("total_score", data.get("score", 0))
        try:
            return max(0, min(100, int(round(float(raw)))))
        except (TypeError, ValueError):
            return 0

    def _reason_value(self, data: dict[str, Any]) -> str:
        raw = data.get("reason", data.get("summary", ""))
        if isinstance(raw, list):
            raw = " ".join(str(item) for item in raw)
        return str(raw or "").strip()

    def _reason_for(self, total: int, threshold: int) -> str:
        if total >= threshold:
            return "핵심 요건 적합"
        return "핵심 요건 부족"

    def _short_reason(self, reason: str, limit: int = 20) -> str:
        cleaned = " ".join(str(reason or "").split())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[:limit]
