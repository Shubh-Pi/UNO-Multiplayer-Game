"""Optional OpenAI-compatible LLM integration with immediate local fallbacks."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections import defaultdict, deque

import httpx


FALLBACKS = {
    "mixed": [
        "Name three things you would pack for a trip to the moon.",
        "Invent a name for a tiny dragon who loves snacks.",
        "What would be the worst superpower to have at a party?",
        "Name something that gets better when it is shared.",
    ],
    "movies": [
        "Pitch a movie about a detective who can only solve mysteries while dancing.",
        "Name a fictional character you would trust to plan a road trip.",
        "Invent a one-line sequel title for a movie about a runaway sandwich.",
        "What everyday object would make the most dramatic movie villain?",
    ],
    "food": [
        "Invent a new ice-cream flavor for a very brave astronaut.",
        "What food would make the funniest hat? Explain in one sentence.",
        "Name a snack that deserves its own theme song.",
        "Create a restaurant name for a café run by friendly ghosts.",
    ],
    "travel": [
        "Describe a dream destination using only three vivid words.",
        "Name one surprising thing you would bring to a desert island.",
        "Invent a travel tour for people who love unusual museums.",
        "Which animal would make the most reliable tour guide?",
    ],
    "silly": [
        "Give a royal title to the last person who ate a sandwich.",
        "Invent a new sport using a pillow and a spoon.",
        "What would a cloud say if it could send one text message?",
        "Name the least convincing disguise for a penguin.",
    ],
}


class ChallengeAI:
    def __init__(self) -> None:
        self.base_url = os.getenv("AI_BASE_URL", "").rstrip("/")
        self.api_key = os.getenv("AI_API_KEY", "")
        self.model = os.getenv("AI_MODEL", "gpt-4o-mini")
        self.enabled = bool(self.base_url and self.model)
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=1.5))
        self._prompts: dict[str, deque[tuple[str, bool]]] = defaultdict(deque)
        self._pending: set[str] = set()
        self._lock = asyncio.Lock()
        self._counts: dict[str, int] = defaultdict(int)

    async def close(self) -> None:
        await self._client.aclose()

    def prompt_now(self, theme: str) -> tuple[str, str]:
        key = theme if theme in FALLBACKS else "mixed"
        queue = self._prompts[key]
        if queue:
            prompt, generated = queue.popleft()
        else:
            index = self._counts[key] % len(FALLBACKS[key])
            self._counts[key] += 1
            prompt = FALLBACKS[key][index]
            generated = False
        if self.enabled and len(queue) < 2 and key not in self._pending:
            task = asyncio.create_task(self._refill(key))
            self._pending.add(key)
            task.add_done_callback(lambda _task, theme_key=key: self._pending.discard(theme_key))
        return prompt, "llm" if generated else "builtin"

    async def recap(self, stats: dict) -> str:
        fallback = _local_recap(stats)
        if not self.enabled:
            return fallback
        try:
            prompt = (
                "Write one friendly, concise party-game recap sentence from these anonymous stats. "
                "Do not invent facts or names. Return plain text only, at most 180 characters.\n"
                + json.dumps(stats, separators=(",", ":"))
            )
            text = await self._completion(prompt, timeout=1.6)
            clean = _clean_prompt(text, maximum=180)
            return clean or fallback
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return fallback

    async def _refill(self, theme: str) -> None:
        async with self._lock:
            try:
                prompt = (
                    "Create one original, family-friendly party-game challenge. "
                    "Return JSON only as {\"prompt\":\"...\"}. Ask for a short spoken or typed answer; "
                    "avoid personal, sensitive, political, or risky tasks. Theme: " + theme
                )
                raw = await self._completion(prompt, timeout=3.0)
                decoded = json.loads(raw)
                cleaned = _clean_prompt(decoded.get("prompt", ""), maximum=140)
                if cleaned and len(cleaned) >= 16:
                    self._prompts[theme].append((cleaned, True))
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                # The built-in prompt pool stays available when a provider is slow or unavailable.
                return

    async def _completion(self, prompt: str, *, timeout: float) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = await self._client.post(
            self.base_url + "/chat/completions",
            headers=headers,
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.8,
                "max_tokens": 100,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return payload["choices"][0]["message"]["content"]


def _clean_prompt(value: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    text = re.sub(r"\s+", " ", value).strip().strip('"\'`')
    if any(ord(char) < 32 and char not in "\t\n\r" for char in text):
        return ""
    return text[:maximum].strip()


def _local_recap(stats: dict) -> str:
    rounds = stats.get("rounds", 0)
    scores = stats.get("scores", [])
    if not scores:
        return f"A lively match across {rounds} rounds. Rematch and make a new story."
    highest = max(scores, key=lambda row: row.get("score", 0))
    return f"{highest.get('label', 'The table')} led the scores after {rounds} rounds. Ready for a rematch?"
