"""Optional Groq / Gemini answers for Ask Rota when the local lookup cannot match."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import httpx

from app.config import (
    ASSISTANT_LLM_TIMEOUT_S,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GROQ_API_KEY,
    GROQ_MODEL,
)

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def cloud_providers() -> list[str]:
    names = []
    if GROQ_API_KEY:
        names.append("groq")
    if GEMINI_API_KEY:
        names.append("gemini")
    return names


def _parse_llm_json(raw: str) -> Optional[dict[str, Any]]:
    text = (raw or "").strip()
    if not text:
        return None
    fenced = _JSON_FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    answer = str(data.get("answer") or "").strip()
    if not answer:
        return None
    items = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        row = {"title": title[:120], "detail": str(item.get("detail") or "")[:240]}
        meta = str(item.get("meta") or "").strip()
        if meta:
            row["meta"] = meta[:80]
        items.append(row)
        if len(items) >= 12:
            break
    suggestions = [str(s).strip()[:80] for s in (data.get("suggestions") or []) if str(s).strip()][:3]
    intent = str(data.get("intent") or "help").strip().lower()
    if intent not in {"schedule", "coverage", "tasks", "help"}:
        intent = "help"
    return {"intent": intent, "answer": answer[:1200], "items": items, "suggestions": suggestions}


def _messages(question: str, snapshot: str) -> list[dict[str, str]]:
    system = (
        "You are Ask Rota, a helpful roster assistant inside RotaShift. "
        "Use FACTS and APP_HOWTO only. Never invent people, dates, or shift codes. "
        "Write a clear, short answer a duty staff member can use immediately. "
        "If FACTS include lookup rows, summarise them; do not drop important names or codes. "
        "Reply with JSON only: "
        '{"intent":"help|schedule|coverage|tasks","answer":"...","items":[{"title":"...","detail":"...","meta":"..."}],'
        '"suggestions":["Show my schedule","Show my tasks","Who is on G shift tomorrow?"]}'
    )
    user = f"FACTS:\n{snapshot}\n\nQUESTION:\n{question}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def _groq_answer(question: str, snapshot: str) -> Optional[dict[str, Any]]:
    if not GROQ_API_KEY:
        return None
    payload = {
        "model": GROQ_MODEL,
        "temperature": 0.1,
        "max_tokens": 700,
        "messages": _messages(question, snapshot),
    }
    try:
        async with httpx.AsyncClient(timeout=ASSISTANT_LLM_TIMEOUT_S) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json=payload,
            )
        if resp.status_code >= 400:
            return None
        content = (((resp.json() or {}).get("choices") or [{}])[0].get("message") or {}).get("content")
        parsed = _parse_llm_json(content or "")
        if parsed:
            parsed["source"] = "groq"
        return parsed
    except Exception:
        return None


async def _gemini_answer(question: str, snapshot: str) -> Optional[dict[str, Any]]:
    if not GEMINI_API_KEY:
        return None
    body = {
        "contents": [{"role": "user", "parts": [{"text": "\n".join(m["content"] for m in _messages(question, snapshot))}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 400,
            "responseMimeType": "application/json",
        },
    }
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
        f"?key={GEMINI_API_KEY}"
    )
    try:
        async with httpx.AsyncClient(timeout=ASSISTANT_LLM_TIMEOUT_S) as client:
            resp = await client.post(url, json=body)
        if resp.status_code >= 400:
            return None
        parts = (((resp.json() or {}).get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        text = "".join(str(p.get("text") or "") for p in parts)
        parsed = _parse_llm_json(text)
        if parsed:
            parsed["source"] = "gemini"
        return parsed
    except Exception:
        return None


async def cloud_answer(question: str, snapshot: str) -> Optional[dict[str, Any]]:
    """Try Groq first (fast), then Gemini. Returns None if neither is configured or both fail."""
    result = await _groq_answer(question, snapshot)
    if result:
        return result
    return await _gemini_answer(question, snapshot)
