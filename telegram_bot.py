"""Omni-Core Phase 3 — Telegram front door.

Talk to Jarvis from your phone. Long-polling bot, no server needed:
    python telegram_bot.py

Setup (2 minutes, free):
  1. Telegram → @BotFather → /newbot → copy the token
  2. .env: TELEGRAM_BOT_TOKEN=<token>
  3. Run this script, send the bot any message — it replies with your
     chat ID. Put it in .env as OMNI_OWNER_CHAT_ID and restart.
  4. From then on the bot answers ONLY you. Same brain, same memory
     as the web UI (one SQLite brain, two front doors).
"""
import json
import os
import time
import urllib.request

from dotenv import load_dotenv

load_dotenv()  # before importing brain — it reads env at import time

import brain  # noqa: E402
import memory  # noqa: E402
import scheduler  # noqa: E402
import voice  # noqa: E402

VOICE_REPLY = os.environ.get("OMNI_VOICE_REPLY", "1") == "1"

API = "https://api.telegram.org/bot{token}/{method}"
OWNER = os.environ.get("OMNI_OWNER_CHAT_ID", "").strip()


def api(token: str, method: str, payload: dict, timeout: int = 45) -> dict:
    req = urllib.request.Request(
        API.format(token=token, method=method),
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "omni-core"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def split_reply(text: str, limit: int = 3900) -> list[str]:
    """Telegram caps messages at 4096 chars — split on line breaks,
    hard-chunking any single line that is longer than the limit."""
    parts, cur = [], ""
    for line in (text or "(no reply)").splitlines(keepends=True):
        while len(line) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) > limit and cur:
            parts.append(cur)
            cur = ""
        cur += line
    if cur:
        parts.append(cur)
    return parts or ["(no reply)"]


def handle_text(chat_id: int, text: str, send) -> None:
    """One incoming message. `send(chat_id, text)` is injected for tests."""
    if not OWNER:
        send(chat_id, f"👋 Your chat ID is {chat_id}.\n"
                      "Put it in .env as OMNI_OWNER_CHAT_ID and restart the bot — "
                      "then I will answer only you, sir.")
        return
    if str(chat_id) != OWNER:
        send(chat_id, "🔒 Private assistant. I answer only my owner.")
        return
    text = text.strip()
    if text == "/start":
        send(chat_id, "Vanakkam, sir. Jarvis online. 🦾 Enna pannanum?")
    elif text == "/facts":
        facts = memory.list_facts()
        send(chat_id, "\n".join(f"• {f}" for f in facts) or "Nothing saved yet, sir.")
    elif text == "/reset":
        memory.clear_history()
        send(chat_id, "Chat history cleared, sir. Facts and graph safe irukku.")
    elif text:
        try:
            reply = brain.chat(text)
        except Exception as e:
            reply = f"⚠️ {brain.format_error(e)}"
        for part in split_reply(reply):
            send(chat_id, part)


def download_file(token: str, file_id: str) -> bytes:
    out = api(token, "getFile", {"file_id": file_id})
    path = out["result"]["file_path"]
    req = urllib.request.Request(
        f"https://api.telegram.org/file/bot{token}/{path}",
        headers={"User-Agent": "omni-core"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def send_audio_file(token: str, chat_id: int, wav: bytes) -> None:
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav)
        tmp = f.name
    try:
        subprocess.run(
            ["curl", "-s", "--max-time", "60",
             "-F", f"chat_id={chat_id}",
             "-F", f"audio=@{tmp}",
             f"https://api.telegram.org/bot{token}/sendAudio"],
            capture_output=True, timeout=75,
        )
    finally:
        os.unlink(tmp)


def handle_voice(chat_id: int, token: str, file_id: str, send) -> None:
    """A Telegram voice note: hear it, think, reply in text + voice."""
    if not OWNER:
        send(chat_id, f"👋 Your chat ID is {chat_id}.\n"
                      "Put it in .env as OMNI_OWNER_CHAT_ID and restart the bot — "
                      "then I will answer only you, sir.")
        return
    if str(chat_id) != OWNER:
        send(chat_id, "🔒 Private assistant. I answer only my owner.")
        return
    try:
        audio = download_file(token, file_id)
        heard = voice.transcribe(audio, "audio/ogg")
    except Exception as e:
        send(chat_id, f"⚠️ Voice puriyala, sir: {brain.format_error(e)}")
        return
    if not heard:
        send(chat_id, "⚠️ Onnum kekkala, sir — marupadiyum pesunga.")
        return
    send(chat_id, f"🎙️ Neenga sonnadhu: {heard}")
    try:
        reply = brain.chat(heard)
    except Exception as e:
        reply = f"⚠️ {brain.format_error(e)}"
    for part in split_reply(reply):
        send(chat_id, part)
    if VOICE_REPLY:
        try:
            wav = voice.speak(reply)
            if wav:
                send_audio_file(token, chat_id, wav)
        except Exception as e:
            print(f"voice reply failed: {e}")


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or token == "your-token-here":
        print("TELEGRAM_BOT_TOKEN is not set in .env.")
        print("Create a free bot: Telegram → @BotFather → /newbot → copy the token.")
        return
    send = lambda cid, txt: api(token, "sendMessage", {"chat_id": cid, "text": txt})  # noqa: E731
    print("Jarvis Telegram bot polling… (Ctrl+C to stop)")
    offset = 0
    while True:
        try:
            out = api(token, "getUpdates", {"offset": offset, "timeout": 30}, timeout=40)
        except Exception as e:
            print(f"poll error: {e} — retrying in 5s")
            time.sleep(5)
            continue
        for upd in out.get("result", []):
            offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            chat_id = (msg.get("chat") or {}).get("id")
            text = msg.get("text") or ""
            voice_note = msg.get("voice") or msg.get("audio") or {}
            if chat_id is not None:
                try:
                    api(token, "sendChatAction", {"chat_id": chat_id, "action": "typing"})
                except Exception:
                    pass
                if voice_note.get("file_id"):
                    handle_voice(chat_id, token, voice_note["file_id"], send)
                elif text:
                    handle_text(chat_id, text, send)
        # Phase 4: proactive — fire due reminders + morning brief.
        if OWNER:
            try:
                scheduler.tick(lambda txt: send(int(OWNER), txt))
            except Exception as e:
                print(f"scheduler tick error: {e}")


if __name__ == "__main__":
    main()
