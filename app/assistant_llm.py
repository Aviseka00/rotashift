"""Groq then Gemini for Ask Rota. General questions are open; live roster rows stay factual."""
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
_ANSWER_CAP = 8000

GENERAL_CONTEXT = (
    "GENERAL_MODE: Answer the user's question fully. It is not limited to RotaShift. "
    "Use general knowledge. Do not say you can only help with rosters or this app."
)


def cloud_providers() -> list[str]:
    names = []
    if GROQ_API_KEY:
        names.append("groq")
    if GEMINI_API_KEY:
        names.append("gemini")
    return names


def _parse_object(raw: str) -> Optional[dict[str, Any]]:
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
    answer = str(data.get("answer") or data.get("content") or data.get("text") or "").strip()
    if not answer:
        return None
    items = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        row = {"title": title[:120], "detail": str(item.get("detail") or "")[:400]}
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
    return {"intent": intent, "answer": answer[:_ANSWER_CAP], "items": items, "suggestions": suggestions}


def _parse_llm_json(raw: str) -> Optional[dict[str, Any]]:
    text = (raw or "").strip()
    if not text:
        return None
    parsed = _parse_object(text)
    if parsed:
        return parsed
    return {"intent": "help", "answer": text[:_ANSWER_CAP], "items": [], "suggestions": []}


def _message_text(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        bits = []
        for part in content:
            if isinstance(part, str):
                bits.append(part)
            elif isinstance(part, dict):
                bits.append(str(part.get("text") or part.get("content") or ""))
        joined = "\n".join(bit for bit in bits if bit.strip())
        if joined.strip():
            return joined
    return str(message.get("reasoning") or "")


def _messages(question: str, snapshot: str, *, factual: bool = False) -> list[dict[str, str]]:
    if factual:
        system = (
            "You are Ask Rota. Live lookup rows in ROTA_CONTEXT are the source of truth. "
            "If they name a colleague, answer about THAT person by first name. "
            "Lead with their shift code and time (for example: “Smruti is on A today, 06:00–14:30.”). "
            "Never substitute the logged-in user's shift for a colleague. "
            "Never invent people, dates, or codes. If no_shift_rows=true, say they are unassigned "
            "for those dates. Write a short, useful answer. Prefer JSON: "
            '{"intent":"schedule","answer":"...","items":[],"suggestions":[]}'
        )
    else:
        system = (
            "You are Ask Rota, a capable general assistant used inside RotaShift. "
            "Answer ANY question the user asks: general knowledge, writing, math, language, "
            "clinical concepts, how-tos, explanations, brainstorming — not only roster topics.\n"
            "Never refuse just because the topic is outside RotaShift or missing from context.\n"
            "Write a complete, useful answer. Prefer JSON with the full reply in answer: "
            '{"intent":"help","answer":"...","items":[],"suggestions":[]}'
            " Plain text is also fine."
        )
    context = (snapshot or "").strip() or GENERAL_CONTEXT
    user = f"ROTA_CONTEXT:\n{context}\n\nQUESTION:\n{question}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def _groq_answer(question: str, snapshot: str, *, factual: bool = False) -> Optional[dict[str, Any]]:
    if not GROQ_API_KEY:
        return None
    payload = {
        "model": GROQ_MODEL,
        "temperature": 0.1 if factual else 0.6,
        "max_tokens": 2048,
        "messages": _messages(question, snapshot, factual=factual),
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
        message = (((resp.json() or {}).get("choices") or [{}])[0].get("message") or {})
        parsed = _parse_llm_json(_message_text(message))
        if parsed:
            parsed["source"] = "groq"
        return parsed
    except Exception:
        return None


async def _gemini_answer(question: str, snapshot: str, *, factual: bool = False) -> Optional[dict[str, Any]]:
    if not GEMINI_API_KEY:
        return None
    packed = _messages(question, snapshot, factual=factual)
    body = {
        "systemInstruction": {"parts": [{"text": packed[0]["content"]}]},
        "contents": [{"role": "user", "parts": [{"text": packed[1]["content"]}]}],
        "generationConfig": {
            "temperature": 0.1 if factual else 0.6,
            "maxOutputTokens": 2048,
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


async def cloud_answer(question: str, snapshot: str, *, factual: bool = False) -> Optional[dict[str, Any]]:
    """Try Groq first (fast), then Gemini. Returns None if neither is configured or both fail."""
    result = await _groq_answer(question, snapshot, factual=factual)
    if result:
        return result
    return await _gemini_answer(question, snapshot, factual=factual)
