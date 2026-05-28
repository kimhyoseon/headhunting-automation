from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.prompt_defaults import DEFAULT_PROMPTS


class PromptSettings(BaseModel):
    match_system_prompt: str = DEFAULT_PROMPTS["match_system_prompt"]
    match_user_prompt: str = DEFAULT_PROMPTS["match_user_prompt"]


class LLMUsage(BaseModel):
    model: str = ""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    input_cost_usd: float = 0
    cached_input_cost_usd: float = 0
    output_cost_usd: float = 0
    total_cost_usd: float = 0
    total_cost_krw: float = 0
    usd_krw_rate: float = 1507.2
    pricing_known: bool = False


class RunCreateRequest(BaseModel):
    jd_text: str
    threshold: int = Field(default=90, ge=0, le=100)
    test_mode: bool = False
    max_candidate_count: int | None = Field(default=30, ge=1)


class Candidate(BaseModel):
    id: str
    name: str
    company: str
    role: str
    experience: str
    location: str
    skills: list[str]
    resume_text: str
    remember_page_number: int | None = None
    remember_page_url: str | None = None
    remember_detail_url: str | None = None
    remember_profile_id: str | None = None
    remember_profile_card_id: str | None = None
    remember_card_index: int | None = None
    remember_card_text: str | None = None


class ItemScore(BaseModel):
    name: str
    weight: int
    score: int
    note: str


class MatchResult(BaseModel):
    total_score: int
    passed: bool
    reason: str
    item_scores: list[ItemScore] = Field(default_factory=list)
    source: str
    usage: LLMUsage = Field(default_factory=LLMUsage)


class SendStatus(str, Enum):
    pending = "pending"
    skipped = "skipped"
    sent = "sent"
    failed = "failed"


class CandidateResult(BaseModel):
    candidate: Candidate
    match: MatchResult | None = None
    send_status: SendStatus = SendStatus.pending
    send_reason: str | None = None
    selected: bool = True


class RunStatus(str, Enum):
    queued = "queued"
    running = "running"
    paused = "paused"
    ready_to_send = "ready_to_send"
    sending = "sending"
    completed = "completed"
    cancelled = "cancelled"
    failed = "failed"


class RunStats(BaseModel):
    total: int = 0
    crawled: int = 0
    processed: int = 0
    passed: int = 0
    sent: int = 0
    failed: int = 0


class RunEvent(BaseModel):
    type: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class RunState(BaseModel):
    run_id: str
    status: RunStatus
    config: RunCreateRequest
    stage: str = "queued"
    stats: RunStats = Field(default_factory=RunStats)
    current_candidate: Candidate | None = None
    results: list[CandidateResult] = Field(default_factory=list)
    usage: LLMUsage = Field(default_factory=LLMUsage)
    logs: list[str] = Field(default_factory=list)
    stop_reason: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    send_threshold: int | None = None


class SendSelectedRequest(BaseModel):
    candidate_ids: list[str]
    threshold: int = Field(default=90, ge=0, le=100)


class AppSettingsResponse(BaseModel):
    api_key_set: bool
    api_key_preview: str | None = None
    openai_model: str = "gpt-5.4-mini"
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)
    min_delay_seconds: float = Field(default=1, ge=0)
    max_delay_seconds: float = Field(default=3, ge=0)
    usd_krw_rate: float = Field(default=1507.2, gt=0)
    crawler_mode: str = "mock"
    remember_url: str = "https://career.rememberapp.co.kr/"
    remember_cdp_url: str = "http://127.0.0.1:9222"
    remember_browser_port: int = Field(default=9222, ge=1, le=65535)
    remember_browser_profile_dir: str = "browser_profile"
    browser_locale: str = "ko-KR"
    browser_accept_language: str = "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
    browser_timezone: str = "Asia/Seoul"
    prompts: PromptSettings = Field(default_factory=PromptSettings)


class AppSettingsUpdate(BaseModel):
    openai_api_key: str | None = None
    clear_api_key: bool = False
    openai_model: str = "gpt-5.4-mini"
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)
    min_delay_seconds: float = Field(default=1, ge=0)
    max_delay_seconds: float = Field(default=3, ge=0)
    usd_krw_rate: float = Field(default=1507.2, gt=0)
    crawler_mode: str = "mock"
    remember_url: str = "https://career.rememberapp.co.kr/"
    remember_cdp_url: str = "http://127.0.0.1:9222"
    remember_browser_port: int = Field(default=9222, ge=1, le=65535)
    remember_browser_profile_dir: str = "browser_profile"
    browser_locale: str = "ko-KR"
    browser_accept_language: str = "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
    browser_timezone: str = "Asia/Seoul"
    prompts: PromptSettings = Field(default_factory=PromptSettings)
