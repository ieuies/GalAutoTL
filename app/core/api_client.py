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


class OpenAICompatClient:
    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        temperature: float = 0.3,
        timeout: int = 180,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key.strip()
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        # Optional: set by translate loops so retries abort promptly on cancel
        self.cancel_check: Optional[Callable[[], bool]] = None

    def chat(self, system: str, user: str, retries: int = 3) -> str:
        if not self.api_key:
            raise ApiError("未填写 API Key")
        base = self.api_base.rstrip("/")
        if base.endswith("/chat/completions"):
            url = base
        else:
            url = f"{base}/chat/completions"
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
        }
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        last_err: Optional[Exception] = None
        for attempt in range(retries):
            if self.cancel_check and self.cancel_check():
                raise ApiError("已取消")
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                return payload["choices"][0]["message"]["content"].strip()
            except urllib.error.HTTPError as e:
                last_err = e
                detail = e.read().decode("utf-8", errors="replace")[:400]
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
        raise ApiError(f"请求失败: {last_err}")
