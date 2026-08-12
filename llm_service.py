import json
import time
import re

import requests

import config
import database
import tools as tools_module
from mlops.logger import log_llm_call
from prompts import MODES, DEFAULT_MODE, get_mode, build_user_prompt, CHAT_SYSTEM_PROMPT


class LLMServiceError(Exception):
    pass


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in model output")
    return json.loads(match.group(0))


def _post_chat(messages: list, max_tokens: int = 400, tools=None, tool_choice=None):
    headers = {
        "Authorization": f"Bearer {config.GEMINI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.MODEL_NAME,
        "messages": messages,
        "temperature": 0.6,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice or "auto"
    resp = requests.post(
        config.GEMINI_API_URL,
        headers=headers,
        json=payload,
        timeout=config.REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()
    message = data["choices"][0]["message"]
    usage = data.get("usage", {})
    return message, usage


def _chat_completion(system_prompt: str, user_prompt: str, max_tokens: int = 400):
    message, usage = _post_chat(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=max_tokens,
    )
    return message.get("content") or "", usage


def _build_context_block(user_id, entry_text: str):
    if not user_id:
        return "", 0, 0

    parts = []

    facts = database.get_memory_facts(user_id, limit=8)
    if facts:
        fact_lines = "\n".join(f"- {f['key']}: {f['value']}" for f in facts)
        parts.append(f"What you already know about this writer:\n{fact_lines}")

    similar = database.search_similar_entries(user_id, entry_text, limit=3)
    if similar:
        sim_lines = []
        for s in similar:
            date = (s.get("timestamp") or "")[:10]
            sim_lines.append(
                f"- {date} [{s.get('mode')}] mood={s.get('mood')!r} "
                f"themes={s.get('themes')!r} reflection={s.get('reflection')!r}"
            )
        parts.append(
            "Related past entries from this writer (already analyzed — for "
            "pattern context only):\n" + "\n".join(sim_lines)
        )

    if not parts:
        return "", 0, len(facts)

    header = (
        "\n\n--- Background context (not part of today's entry — use it only "
        "to notice real patterns; never quote it back or treat it as today's "
        "writing) ---\n"
    )
    return header + "\n\n".join(parts), len(similar), len(facts)


def _run_tool_loop(system_prompt: str, user_prompt: str, user_id, max_tokens: int = 400):
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    tool_calls_made = []
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0}

    for round_num in range(tools_module.MAX_TOOL_ROUNDS):
        force_final = round_num == tools_module.MAX_TOOL_ROUNDS - 1
        message, usage = _post_chat(
            messages,
            max_tokens=max_tokens,
            tools=None if force_final else tools_module.TOOLS,
            tool_choice=None if force_final else "auto",
        )
        for k in total_usage:
            total_usage[k] += usage.get(k) or 0

        calls = message.get("tool_calls")
        if not calls:
            return message.get("content") or "", total_usage, tool_calls_made

        messages.append(message)
        for call in calls:
            fn = call.get("function", {}) or {}
            name = fn.get("name")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            result = tools_module.execute_tool_call(name, args, user_id)
            tool_calls_made.append(name)
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id"),
                "content": json.dumps(result),
            })

    raise LLMServiceError("Model did not produce a final answer after tool calls")


def _run_chat_tool_loop(messages: list, user_id, max_tokens: int = 350):
    tool_calls_made = []
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0}

    for round_num in range(tools_module.MAX_TOOL_ROUNDS):
        force_final = round_num == tools_module.MAX_TOOL_ROUNDS - 1
        message, usage = _post_chat(
            messages,
            max_tokens=max_tokens,
            tools=None if force_final else tools_module.TOOLS,
            tool_choice=None if force_final else "auto",
        )
        for k in total_usage:
            total_usage[k] += usage.get(k) or 0

        calls = message.get("tool_calls")
        if not calls:
            return message.get("content") or "", total_usage, tool_calls_made

        messages.append(message)
        for call in calls:
            fn = call.get("function", {}) or {}
            name = fn.get("name")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            result = tools_module.execute_tool_call(name, args, user_id)
            tool_calls_made.append(name)
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id"),
                "content": json.dumps(result),
            })

    raise LLMServiceError("Model did not produce a final chat reply after tool calls")


def _call_gemini(entry_text: str, mode_id: str, user_id=None):
    system_prompt = get_mode(mode_id)["system_prompt"]
    user_prompt = build_user_prompt(mode_id, entry_text)

    context_block, rag_hits, memory_hits = _build_context_block(user_id, entry_text)
    if context_block:
        user_prompt += context_block

    content, usage, tool_calls = _run_tool_loop(system_prompt, user_prompt, user_id)
    return content, usage, tool_calls, rag_hits, memory_hits


def _rule_based_fallback(entry_text: str, mode_id: str) -> dict:
    mode = get_mode(mode_id)
    cats = mode["fallback_categories"]

    positive_words = {"happy", "grateful", "excited", "good", "great", "love",
                       "proud", "calm", "relaxed", "joy", "hopeful", "peaceful",
                       "clear", "solid", "confident", "strong", "yes"}
    negative_words = {"sad", "angry", "anxious", "stressed", "tired", "worried",
                       "upset", "frustrated", "overwhelmed", "lonely", "afraid",
                       "hurt", "confused", "unclear", "weak", "no", "stuck"}

    words = re.findall(r"[a-zA-Z']+", entry_text.lower())
    pos = sum(1 for w in words if w in positive_words)
    neg = sum(1 for w in words if w in negative_words)
    total = pos + neg
    score = 0.0 if total == 0 else round((pos - neg) / total, 2)

    if score > 0.3:
        primary = cats["pos"]
    elif score < -0.3:
        primary = cats["neg"]
    else:
        primary = cats["neu"]

    return {
        "mood": primary,
        "sentiment_score": score,
        "themes": ["demo mode"],
        "reflection": (
            f"This is a demo-mode result for {mode['name']} mode, generated with a "
            "simple keyword heuristic (no API key configured). Add a free "
            "GEMINI_API_KEY in your .env file to unlock full LLM-generated analysis."
        ),
        "suggested_action": "Add GEMINI_API_KEY to .env for full AI analysis.",
    }


def analyze_entry(entry_text: str, mode_id: str = DEFAULT_MODE, user_id=None) -> dict:
    if mode_id not in MODES:
        mode_id = DEFAULT_MODE

    if config.DEMO_MODE:
        start = time.perf_counter()
        result = _rule_based_fallback(entry_text, mode_id)
        latency_ms = (time.perf_counter() - start) * 1000
        log_llm_call(
            endpoint="/api/analyze",
            status="fallback",
            latency_ms=latency_ms,
            model_name="rule-based-fallback",
        )
        result["_latency_ms"] = latency_ms
        result["_demo_mode"] = True
        result["_mode"] = mode_id
        result["_tool_calls"] = []
        result["_rag_entries_used"] = 0
        result["_memory_facts_used"] = 0
        return result

    last_error = None
    for attempt in range(config.MAX_RETRIES + 1):
        start = time.perf_counter()
        try:
            content, usage, tool_calls, rag_hits, memory_hits = _call_gemini(
                entry_text, mode_id, user_id
            )
            latency_ms = (time.perf_counter() - start) * 1000
            parsed = _extract_json(content)

            try:
                parsed["sentiment_score"] = float(parsed.get("sentiment_score", 0))
            except (TypeError, ValueError):
                parsed["sentiment_score"] = 0.0
            parsed["sentiment_score"] = max(-1.0, min(1.0, parsed["sentiment_score"]))
            if not isinstance(parsed.get("themes"), list):
                parsed["themes"] = []
            else:
                # Contract says "up to 4" themes; some models over-generate.
                parsed["themes"] = [str(t) for t in parsed["themes"][:4] if t]

            log_llm_call(
                endpoint="/api/analyze",
                status="success",
                latency_ms=latency_ms,
                model_name=config.MODEL_NAME,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                tool_calls_count=len(tool_calls),
                rag_hits=rag_hits,
            )
            parsed["_latency_ms"] = latency_ms
            parsed["_demo_mode"] = False
            parsed["_mode"] = mode_id
            parsed["_tool_calls"] = tool_calls
            parsed["_rag_entries_used"] = rag_hits
            parsed["_memory_facts_used"] = memory_hits
            return parsed

        except Exception as e:
            latency_ms = (time.perf_counter() - start) * 1000
            last_error = str(e)
            log_llm_call(
                endpoint="/api/analyze",
                status="error",
                latency_ms=latency_ms,
                model_name=config.MODEL_NAME,
                error_message=last_error,
            )
            if attempt < config.MAX_RETRIES:
                time.sleep(config.RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue

    raise LLMServiceError(f"LLM call failed after retries: {last_error}")

def analyze_journal_entry(entry_text: str, mode_id: str = DEFAULT_MODE, user_id=None) -> dict:
    return analyze_entry(entry_text, mode_id, user_id=user_id)

CHAT_ENDPOINT = "/api/chat"
CHAT_HISTORY_TURNS = 20


def _rule_based_chat_reply(user_message: str, attachment=None) -> str:
    attach_note = ""
    if attachment:
        attach_note = f" I also see you attached a {attachment.get('kind', 'file')} ({attachment.get('name', 'file')}), but I can't read it in demo mode."
    return (
        "This is a demo-mode reply (no GEMINI_API_KEY configured), so I can't "
        "actually hold a conversation yet — I can only echo that I received: "
        f"\u201c{user_message[:140]}{'…' if len(user_message) > 140 else ''}\u201d."
        f"{attach_note} "
        "Add a free GEMINI_API_KEY in your .env file to unlock the live chat companion."
    )


def _user_turn_content(text: str, attachment=None):
    if not attachment:
        return text
    caption = text or f"(sent a {attachment.get('kind', 'file')} with no message)"
    parts = [{"type": "text", "text": caption}]
    b64 = attachment.get("b64")
    mime = attachment.get("mime")
    if b64 and mime:
        parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
    return parts


def chat_reply(user_message: str, user_id, attachment=None) -> dict:
    if config.DEMO_MODE:
        start = time.perf_counter()
        reply = _rule_based_chat_reply(user_message, attachment=attachment)
        latency_ms = (time.perf_counter() - start) * 1000
        log_llm_call(
            endpoint=CHAT_ENDPOINT,
            status="fallback",
            latency_ms=latency_ms,
            model_name="rule-based-fallback",
        )
        return {
            "reply": reply,
            "_latency_ms": latency_ms,
            "_demo_mode": True,
            "_tool_calls": [],
            "_rag_entries_used": 0,
            "_memory_facts_used": 0,
        }

    context_block, rag_hits, memory_hits = _build_context_block(user_id, user_message)
    system_prompt = CHAT_SYSTEM_PROMPT + (context_block or "")

    history = database.get_chat_messages(user_id, limit=CHAT_HISTORY_TURNS) if user_id else []
    base_messages = [{"role": "system", "content": system_prompt}]
    for turn in history:
        role = turn.get("role") if turn.get("role") in ("user", "assistant") else "user"
        content = turn.get("content") or ""
        past_attachment = turn.get("attachment")
        if past_attachment:
            note = f"[attached a {past_attachment.get('kind', 'file')}: {past_attachment.get('name', 'file')}]"
            content = f"{content} {note}".strip()
        base_messages.append({"role": role, "content": content})
    base_messages.append({"role": "user", "content": _user_turn_content(user_message, attachment)})

    last_error = None
    for attempt in range(config.MAX_RETRIES + 1):
        start = time.perf_counter()
        try:
            content, usage, tool_calls = _run_chat_tool_loop(list(base_messages), user_id)
            latency_ms = (time.perf_counter() - start) * 1000
            reply = content.strip()
            if not reply:
                raise ValueError("Empty reply from model")

            log_llm_call(
                endpoint=CHAT_ENDPOINT,
                status="success",
                latency_ms=latency_ms,
                model_name=config.MODEL_NAME,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                tool_calls_count=len(tool_calls),
                rag_hits=rag_hits,
            )
            return {
                "reply": reply,
                "_latency_ms": latency_ms,
                "_demo_mode": False,
                "_tool_calls": tool_calls,
                "_rag_entries_used": rag_hits,
                "_memory_facts_used": memory_hits,
            }

        except Exception as e:
            latency_ms = (time.perf_counter() - start) * 1000
            last_error = str(e)
            log_llm_call(
                endpoint=CHAT_ENDPOINT,
                status="error",
                latency_ms=latency_ms,
                model_name=config.MODEL_NAME,
                error_message=last_error,
            )
            if attempt < config.MAX_RETRIES:
                time.sleep(config.RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue

    raise LLMServiceError(f"Chat call failed after retries: {last_error}")
