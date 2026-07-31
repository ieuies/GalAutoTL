# -*- coding: utf-8 -*-
"""OpenAI-compatible chat completions client."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Callable, Optional


class ApiError(RuntimeError):
    pass


def _deepseek_v4_model(model: str) -> bool:
    m = (model or "").strip().lower()
    return m.startswith("deepseek-v4") or m in {
        "deepseek-chat",  # legacy alias → V4 flash (if still routed)
        "deepseek-reasoner",
    }


def _should_disable_thinking(model: str) -> bool:
    """Batch localization wants old deepseek-chat speed, not reasoner latency/cost.

    DeepSeek V4 defaults thinking ON; equivalent of former deepseek-chat is
    deepseek-v4-flash + thinking.disabled.
    """
    m = (model or "").strip().lower()
    if not _deepseek_v4_model(m):
        return False
    # Explicit reasoner / pro-with-think stays enabled unless we add a UI toggle later
    if m in ("deepseek-reasoner",) or m.endswith("-reasoner"):
        return False
    return True


class OpenAICompatClient:
    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        temperature: float = 0.3,
        timeout: int = 180,
        *,
        thinking: Optional[str] = None,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key.strip()
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        # None = auto (disable on V4 flash-style); "disabled" | "enabled" = force
        self.thinking = thinking
        # Optional: set by translate loops so retries abort promptly on cancel
        self.cancel_check: Optional[Callable[[], bool]] = None

    def chat(self, system: str, user: str, retries: int = 3) -> str:
        key = self.api_key.strip()
        if not key:
            raise ApiError("未填写 API Key")
        base = self.api_base.rstrip("/")
        if base.endswith("/chat/completions"):
            url = base
        else:
            url = f"{base}/chat/completions"
        timeout = self.timeout
        body: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
        }
        think = self.thinking
        if think is None and _should_disable_thinking(self.model):
            think = "disabled"
        if think in ("disabled", "enabled"):
            body["thinking"] = {"type": think}
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        last_err: Optional[Exception] = None
        last_detail = ""
        for attempt in range(retries):
            if self.cancel_check and self.cancel_check():
                raise ApiError("已取消")
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                return payload["choices"][0]["message"]["content"].strip()
            except urllib.error.HTTPError as e:
                last_err = e
                detail = e.read().decode("utf-8", errors="replace")[:500]
                last_detail = detail
                # OOM / hard server errors: don't spin forever
                if "out of memory" in detail.lower() or "显存不足" in detail:
                    raise ApiError(f"HTTP {e.code}: {detail}") from e
                if e.code in (429, 500, 502, 503):
                    if self.cancel_check and self.cancel_check():
                        raise ApiError("已取消") from e
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise ApiError(f"HTTP {e.code}: {detail}") from e
            except Exception as e:
                last_err = e
                if self.cancel_check and self.cancel_check():
                    raise ApiError("已取消") from e
                time.sleep(1.0 * (attempt + 1))
        raise ApiError(f"请求失败: {last_err}" + (f" | {last_detail}" if last_detail else ""))
