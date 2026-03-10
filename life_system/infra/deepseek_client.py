from __future__ import annotations

from typing import Any


class DeepSeekClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def generate_encouragement(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float = 0.6,
        max_tokens: int = 220,
    ) -> str:
        try:
            from openai import OpenAI
        except Exception as exc:
            raise RuntimeError("openai_sdk_not_installed") from exc

        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        resp: Any = client.chat.completions.create(
            model=self.model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        )
        choices = getattr(resp, "choices", None) or []
        if not choices:
            raise RuntimeError("deepseek_empty_response")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None) if message is not None else None
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("deepseek_empty_response")
        return content.strip()
