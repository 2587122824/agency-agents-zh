from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMResult:
    provider: str
    model: str
    content: str
    raw: dict | None = None


class CodexAPI:
    """Small LLM adapter.

    Default mode is offline, which writes executable prompt packages without
    calling a model. Set OPENAI_API_KEY to execute steps with an OpenAI-compatible
    chat completions endpoint.
    """

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.provider = provider or os.getenv("MY_WORKFLOW_PROVIDER") or "auto"
        self.model = model or os.getenv("OPENAI_MODEL") or "gpt-5.5"
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")

    def run(self, system_prompt: str, user_prompt: str) -> LLMResult:
        provider = self.provider
        if provider == "auto":
            provider = "openai" if self.api_key else "offline"

        if provider == "openai":
            return self._run_openai(system_prompt, user_prompt)
        if provider == "offline":
            return self._run_offline(system_prompt, user_prompt)

        raise ValueError(f"Unsupported provider: {provider}")

    def _run_offline(self, system_prompt: str, user_prompt: str) -> LLMResult:
        content = (
            "# 待执行提示词\n\n"
            "当前未检测到 API Key，所以本步骤未调用模型。\n\n"
            "把同目录下的 `prompt.md` 内容发送给模型，即可得到本步骤产出；"
            "或在可视化界面的 API Key 输入框填入密钥后重新运行工作流。\n"
        )
        return LLMResult(provider="offline", model="none", content=content)

    def _run_openai(self, system_prompt: str, user_prompt: str) -> LLMResult:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required for provider=openai")

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.4,
        }
        request = urllib.request.Request(
            url=f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI request failed: HTTP {exc.code}: {body}") from exc

        content = raw["choices"][0]["message"]["content"]
        return LLMResult(provider="openai", model=self.model, content=content, raw=raw)
