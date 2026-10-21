"""Omni-Core brain: routed Gemini (Flash/Pro) + MCP tools + memory.

Routing (Layer 2, selective): small talk and simple asks go to Flash;
hard thinking (planning, analysis, code, decisions, long prompts) goes
to Pro. If Pro is quota-blocked (free tier), it falls back to Flash and
says so in LAST_ROUTE. Force Pro with a "deep:" prefix.

Free tier key: https://aistudio.google.com/apikey  ->  .env GEMINI_API_KEY
"""
import asyncio
import os
import re
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from google import genai
from google.genai import types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import graph_memory  # noqa: F401  (ensures graph tables exist early)
import memory
import personality

FLASH_MODEL = os.environ.get("OMNI_FLASH_MODEL", "gemini-3.5-flash")
PRO_MODEL = os.environ.get("OMNI_PRO_MODEL", "gemini-3.1-pro-preview")
HERE = Path(__file__).parent

LAST_ROUTE = {"model": FLASH_MODEL, "note": "flash"}  # UI reads this

SMART_WORDS = (
    "think", "plan", "analyze", "analyse", "compare", "decide", "decision",
    "strategy", "design", "architect", "debug", "error", "why", "explain",
    "research", "build", "code", "write a", "evaluate", "review", "best",
    "yosi", "yosichu", "plan pannu", "compare pannu", "mudivu", "selavu",
    "eappadi", "eppadi", "reason", "solve", "calculate pannu",
)
REMEMBER_RE = re.compile(r"^\s*(remember|nyabagam vai|save)[:\-]?\s*(.+)", re.I)


def route_model(text: str) -> tuple[str, str]:
    """Pick the brain for this message. Returns (model, why)."""
    t = text.strip().lower()
    if t.startswith("deep:"):
        return PRO_MODEL, "pro (forced: deep:)"
    if len(text) > 300:
        return PRO_MODEL, "pro (long task)"
    if any(w in t for w in SMART_WORDS):
        return PRO_MODEL, "pro (hard thinking)"
    return FLASH_MODEL, "flash"


def _client() -> genai.Client:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key or key == "your-key-here":
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Create a free key at "
            "https://aistudio.google.com/apikey and put it in a .env file."
        )
    return genai.Client(api_key=key)


def _system_prompt() -> str:
    name = os.environ.get("AGENT_NAME", "Jarvis")
    return personality.build_system_prompt(
        name, memory.list_facts(), memory.latest_summary()
    )


def _sanitize(schema: dict) -> dict:
    keep = {}
    for k in ("type", "description", "enum", "items", "required"):
        if k in schema:
            keep[k] = schema[k]
    if "properties" in schema:
        keep["properties"] = {p: _sanitize(s) for p, s in schema["properties"].items()}
    return keep


def _is_quota_error(e: Exception) -> bool:
    s = str(e)
    return "429" in s or "503" in s or "404" in s or "RESOURCE_EXHAUSTED" in s


async def _run_with_tools(user_text: str, model: str) -> str:
    server = StdioServerParameters(
        command=sys.executable,
        args=[str(HERE / "tools_server.py")],
        env={k: v for k, v in os.environ.items() if isinstance(v, str)},
    )
    async with AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(stdio_client(server))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        tools_result = await session.list_tools()
        declarations = [
            types.FunctionDeclaration(
                name=t.name,
                description=t.description or "",
                parameters=_sanitize(t.inputSchema) or None,
            )
            for t in tools_result.tools
        ]
        history = [
            types.Content(role=m["role"], parts=[types.Part.from_text(text=m["text"])])
            for m in memory.recent_messages(limit=20)
        ]
        client = _client()  # keep referenced — a temporary Client is GC'd
        # and its __del__ closes the httpx client ("client has been closed")
        chat = client.chats.create(
            model=model,
            config=types.GenerateContentConfig(
                system_instruction=_system_prompt(),
                tools=[types.Tool(function_declarations=declarations)],
            ),
            history=history,
        )
        response = chat.send_message(user_text)
        for _ in range(6):
            calls = [
                p.function_call
                for p in (response.candidates[0].content.parts or [])
                if getattr(p, "function_call", None)
            ]
            if not calls:
                break
            parts = []
            for call in calls:
                result = await session.call_tool(call.name, dict(call.args or {}))
                text = "".join(
                    c.text for c in result.content if getattr(c, "type", "") == "text"
                )
                parts.append(
                    types.Part.from_function_response(
                        name=call.name, response={"result": text}
                    )
                )
            response = chat.send_message(parts)
        return (response.text or "").strip()


def _generate(user_text: str, model: str, use_tools: bool) -> str:
    if use_tools:
        return asyncio.run(_run_with_tools(user_text, model))
    history = [
        types.Content(role=m["role"], parts=[types.Part.from_text(text=m["text"])])
        for m in memory.recent_messages(limit=20)
    ]
    client = _client()  # keep referenced — see _run_with_tools note
    chat_session = client.chats.create(
        model=model,
        config=types.GenerateContentConfig(system_instruction=_system_prompt()),
        history=history,
    )
    return (chat_session.send_message(user_text).text or "").strip()


# ---------- event compaction ----------
COMPACT_AT = 60
COMPACT_TAKE = 40


def _maybe_compact() -> None:
    try:
        if memory.message_count() <= COMPACT_AT:
            return
        old = memory.oldest_messages(COMPACT_TAKE)
        if not old:
            return
        transcript = "\n".join(f"{m['role']}: {m['text']}" for m in old)
        prev = memory.latest_summary()
        if prev:
            transcript = f"Previous summary:\n{prev}\n\nOlder messages:\n{transcript}"
        client = _client()
        resp = client.models.generate_content(
            model=FLASH_MODEL,
            contents=(
                "Summarize this conversation history into a compact factual "
                "brief (max 150 words): keep names, decisions, preferences, "
                "open tasks and dates. No fluff.\n\n" + transcript
            ),
        )
        summary = (resp.text or "").strip()
        if summary:
            memory.apply_compaction(summary, [m["id"] for m in old])
    except Exception:
        pass


def format_error(e: BaseException) -> str:
    if isinstance(e, BaseExceptionGroup):
        return "; ".join(format_error(sub) for sub in e.exceptions)
    return f"{type(e).__name__}: {e}"


def _log_error(e: BaseException) -> None:
    try:
        import traceback

        with open(HERE / "omni_error.log", "a", encoding="utf-8") as f:
            f.write("\n--- chat error ---\n")
            traceback.print_exception(e, file=f)
    except Exception:
        pass


def _quota_reply() -> str:
    return (
        "⚠️ Sir, Google free quota mudinjiduchu — indha model oda daily "
        "limit theerndhuduchu (free tier romba chinna limit). Quota daily "
        "reset aagum. Udane continue panna, .env la OMNI_FLASH_MODEL ah "
        "vera model ku maathunga (e.g. gemini-3-flash-preview — adhodha "
        "quota separate bucket) apram Streamlit restart pannunga."
    )


def chat(user_text: str) -> str:
    """One message end-to-end: route, tools, memory, compaction."""
    m = REMEMBER_RE.match(user_text)
    if m:
        fact = m.group(2).strip()
        memory.add_fact(fact)
        graph_memory.add_entity(fact, "fact")
        memory.add_message("user", user_text)
        reply = "Note panniten, sir. ✅"
        memory.add_message("model", reply)
        return reply

    clean_text = user_text.strip()
    if clean_text.lower().startswith("deep:"):
        clean_text = clean_text[5:].strip()

    model, why = route_model(user_text)
    try:
        reply = _generate(clean_text, model, use_tools=True)
        LAST_ROUTE.update(model=model, note=why)
    except RuntimeError:
        raise
    except Exception as e:
        if _is_quota_error(e) and model != PRO_MODEL:
            # Flash bucket empty — a no-tools retry would only 429 again.
            reply = _quota_reply()
            LAST_ROUTE.update(model=model, note="quota exhausted")
        elif model == PRO_MODEL and _is_quota_error(e):
            # Free-tier Pro quota: fall back to Flash, keep working.
            try:
                reply = _generate(clean_text, FLASH_MODEL, use_tools=True)
                LAST_ROUTE.update(
                    model=FLASH_MODEL, note="flash (Pro quota — fallback)"
                )
            except Exception as e2:
                _log_error(e2)
                if _is_quota_error(e2):
                    reply = _quota_reply()
                    LAST_ROUTE.update(model=FLASH_MODEL, note="quota exhausted")
                else:
                    reply = _generate(clean_text, FLASH_MODEL, use_tools=False)
                    LAST_ROUTE.update(model=FLASH_MODEL, note="flash (no tools)")
        else:
            _log_error(e)
            try:
                reply = _generate(clean_text, FLASH_MODEL, use_tools=False)
                LAST_ROUTE.update(model=FLASH_MODEL, note="flash (tools failed)")
            except Exception as e3:
                if _is_quota_error(e3):
                    reply = _quota_reply()
                    LAST_ROUTE.update(model=FLASH_MODEL, note="quota exhausted")
                else:
                    raise

    memory.add_message("user", user_text)
    if reply:
        memory.add_message("model", reply)
    _maybe_compact()
    return reply or "(no reply — try again)"
