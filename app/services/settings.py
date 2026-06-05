from __future__ import annotations

import os
import json
from pathlib import Path

from dotenv import dotenv_values, set_key

from app.models import AppSettingsResponse, AppSettingsUpdate, PromptSettings


DEFAULTS = {
    "HEADHUNTING_PROVIDER": "remember",
    "OPENAI_API_KEY": "",
    "OPENAI_MODEL": "gpt-5.4-mini",
    "APP_HOST": "127.0.0.1",
    "APP_PORT": "8000",
    "MIN_DELAY_SECONDS": "1",
    "MAX_DELAY_SECONDS": "3",
    "USD_KRW_RATE": "1507.2",
    "CRAWLER_MODE": "mock",
    "REMEMBER_URL": "https://career.rememberapp.co.kr/",
    "REMEMBER_CDP_URL": "http://127.0.0.1:9222",
    "REMEMBER_BROWSER_PORT": "9222",
    "REMEMBER_BROWSER_PROFILE_DIR": "browser_profile",
    "BROWSER_LOCALE": "ko-KR",
    "BROWSER_ACCEPT_LANGUAGE": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "BROWSER_TIMEZONE": "Asia/Seoul",
    "CONFIRM_BEFORE_PROPOSAL_SEND": "false",
    "REMEMBER_SKIP_PROPOSAL_SEND": "true",
}


class SettingsStore:
    def __init__(self, env_path: Path, prompts_path: Path) -> None:
        self.env_path = env_path
        self.prompts_path = prompts_path
        self._ensure_env_file()
        self._ensure_prompts_file()

    def load(self) -> AppSettingsResponse:
        values = self._values()
        key = str(values.get("OPENAI_API_KEY", "")).strip()
        return AppSettingsResponse(
            provider=str(values.get("HEADHUNTING_PROVIDER") or DEFAULTS["HEADHUNTING_PROVIDER"]).strip() or "remember",
            api_key_set=bool(key),
            api_key_preview=self._preview_key(key),
            openai_model=str(values.get("OPENAI_MODEL") or DEFAULTS["OPENAI_MODEL"]).strip(),
            app_host=str(values.get("APP_HOST") or DEFAULTS["APP_HOST"]).strip(),
            app_port=self._int_value(values.get("APP_PORT"), int(DEFAULTS["APP_PORT"])),
            min_delay_seconds=self._float_value(values.get("MIN_DELAY_SECONDS"), float(DEFAULTS["MIN_DELAY_SECONDS"])),
            max_delay_seconds=self._float_value(values.get("MAX_DELAY_SECONDS"), float(DEFAULTS["MAX_DELAY_SECONDS"])),
            usd_krw_rate=self._float_value(values.get("USD_KRW_RATE"), float(DEFAULTS["USD_KRW_RATE"])),
            crawler_mode=self._crawler_mode(values.get("CRAWLER_MODE")),
            remember_url=str(values.get("REMEMBER_URL") or DEFAULTS["REMEMBER_URL"]).strip(),
            remember_cdp_url=str(values.get("REMEMBER_CDP_URL") or DEFAULTS["REMEMBER_CDP_URL"]).strip(),
            remember_browser_port=self._int_value(values.get("REMEMBER_BROWSER_PORT"), int(DEFAULTS["REMEMBER_BROWSER_PORT"])),
            remember_browser_profile_dir=str(values.get("REMEMBER_BROWSER_PROFILE_DIR") or DEFAULTS["REMEMBER_BROWSER_PROFILE_DIR"]).strip(),
            browser_locale=str(values.get("BROWSER_LOCALE") or DEFAULTS["BROWSER_LOCALE"]).strip(),
            browser_accept_language=str(values.get("BROWSER_ACCEPT_LANGUAGE") or DEFAULTS["BROWSER_ACCEPT_LANGUAGE"]).strip(),
            browser_timezone=str(values.get("BROWSER_TIMEZONE") or DEFAULTS["BROWSER_TIMEZONE"]).strip(),
            confirm_before_proposal_send=self._bool_value(values.get("CONFIRM_BEFORE_PROPOSAL_SEND"), False),
            remember_skip_proposal_send=self._bool_value(values.get("REMEMBER_SKIP_PROPOSAL_SEND"), True),
            prompts=self._load_prompts(),
        )

    def save(self, update: AppSettingsUpdate) -> AppSettingsResponse:
        self._ensure_env_file()
        values = self._values()
        next_key = str(values.get("OPENAI_API_KEY", "")).strip()

        if update.clear_api_key:
            next_key = ""
        elif update.openai_api_key is not None and update.openai_api_key.strip():
            next_key = update.openai_api_key.strip()

        to_save = {
            "OPENAI_API_KEY": next_key,
            "OPENAI_MODEL": update.openai_model.strip() or DEFAULTS["OPENAI_MODEL"],
            "APP_HOST": update.app_host.strip() or DEFAULTS["APP_HOST"],
            "APP_PORT": str(update.app_port),
            "MIN_DELAY_SECONDS": str(update.min_delay_seconds),
            "MAX_DELAY_SECONDS": str(update.max_delay_seconds),
            "USD_KRW_RATE": str(update.usd_krw_rate),
            "CRAWLER_MODE": self._crawler_mode(update.crawler_mode),
            "REMEMBER_URL": update.remember_url.strip() or DEFAULTS["REMEMBER_URL"],
            "REMEMBER_CDP_URL": update.remember_cdp_url.strip() or DEFAULTS["REMEMBER_CDP_URL"],
            "REMEMBER_BROWSER_PORT": str(update.remember_browser_port),
            "REMEMBER_BROWSER_PROFILE_DIR": update.remember_browser_profile_dir.strip() or DEFAULTS["REMEMBER_BROWSER_PROFILE_DIR"],
            "BROWSER_LOCALE": update.browser_locale.strip() or DEFAULTS["BROWSER_LOCALE"],
            "BROWSER_ACCEPT_LANGUAGE": update.browser_accept_language.strip() or DEFAULTS["BROWSER_ACCEPT_LANGUAGE"],
            "BROWSER_TIMEZONE": update.browser_timezone.strip() or DEFAULTS["BROWSER_TIMEZONE"],
            "CONFIRM_BEFORE_PROPOSAL_SEND": "true" if update.confirm_before_proposal_send else "false",
            "REMEMBER_SKIP_PROPOSAL_SEND": "true" if update.remember_skip_proposal_send else "false",
        }
        for key, value in to_save.items():
            set_key(str(self.env_path), key, value, quote_mode="never")
            os.environ[key] = value
        self._save_prompts(update.prompts)
        return self.load()

    def _values(self) -> dict[str, str]:
        self._ensure_env_file()
        loaded = {key: str(value or "") for key, value in dotenv_values(self.env_path).items()}
        return {**DEFAULTS, **loaded}

    def _ensure_env_file(self) -> None:
        if self.env_path.exists():
            return
        self.env_path.write_text("\n".join(f"{key}={value}" for key, value in DEFAULTS.items()) + "\n", encoding="utf-8")

    def _ensure_prompts_file(self) -> None:
        if self.prompts_path.exists():
            return
        self.prompts_path.parent.mkdir(parents=True, exist_ok=True)
        self.prompts_path.write_text(
            json.dumps(PromptSettings().model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_prompts(self) -> PromptSettings:
        self._ensure_prompts_file()
        try:
            data = json.loads(self.prompts_path.read_text(encoding="utf-8"))
            prompts = PromptSettings(**data)
        except Exception:
            prompts = PromptSettings()
        normalized = PromptSettings(
            match_system_prompt=prompts.match_system_prompt.strip() or PromptSettings().match_system_prompt,
            match_user_prompt=prompts.match_user_prompt.strip() or PromptSettings().match_user_prompt,
        )
        if normalized != prompts:
            self._save_prompts(normalized)
        return normalized

    def _save_prompts(self, prompts: PromptSettings) -> None:
        self.prompts_path.parent.mkdir(parents=True, exist_ok=True)
        cleaned = PromptSettings(
            match_system_prompt=prompts.match_system_prompt.strip() or PromptSettings().match_system_prompt,
            match_user_prompt=prompts.match_user_prompt.strip() or PromptSettings().match_user_prompt,
        )
        self.prompts_path.write_text(json.dumps(cleaned.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _preview_key(key: str) -> str | None:
        if not key:
            return None
        if len(key) <= 8:
            return "*" * len(key)
        return f"{key[:3]}...{key[-4:]}"

    @staticmethod
    def _int_value(value: str | None, default: int) -> int:
        try:
            parsed = int(str(value or "").strip())
            return parsed if 1 <= parsed <= 65535 else default
        except ValueError:
            return default

    @staticmethod
    def _float_value(value: str | None, default: float) -> float:
        try:
            parsed = float(str(value or "").strip())
            return parsed if parsed >= 0 else default
        except ValueError:
            return default

    @staticmethod
    def _bool_value(value: str | None, default: bool) -> bool:
        normalized = str(value or "").strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
        return default

    @staticmethod
    def _crawler_mode(value: str | None) -> str:
        normalized = str(value or "").strip().lower()
        return normalized if normalized in {"mock", "browser"} else "mock"
