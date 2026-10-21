# 🦾 Omni-Core — Revaldo's JARVIS

A personal AI assistant built layer by layer toward the Omni-Core target:
it remembers him, thinks with a routed brain, uses tools, runs code —
and talks like a friend, in Tanglish or English.

## What works today (Phase 1)

- 🧠 **Routed brain** — simple asks → `gemini-3.5-flash`; hard thinking
  (plan / compare / design / code, long prompts, or a `deep:` prefix) →
  `gemini-3.1-pro-preview`. Pro quota full on the free tier? It falls
  back to Flash and says so. The UI shows which brain answered.
- 🗣️ **Tanglish personality core** — natural friend-style Tanglish,
  "sir", brief and direct, honest pushback. English when he writes English.
- 💾 **Memory that starts out knowing him** — `seed.py` preloads verified
  facts + a knowledge graph of his projects (Handoff, Look Roulette,
  TimeMachine, Omni-Core). Facts + graph + history in SQLite; history
  past 60 messages is auto-compacted into a summary.
- 🛠️ **13 MCP tools** — time, Bing web search, page reader, calculator,
  Python sandbox (E2B cloud if keyed, local fallback otherwise), facts,
  reminders, and graph tools (`remember_entity`, `link_entities`,
  `recall_about`, `search_memory`).

## Run it

```bash
cd omni-core
python -m venv .venv
.venv\Scripts\activate          # Windows (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python -m playwright install chromium   # one time — the browser engine
copy .env.example .env          # then paste your Gemini API key into .env
python seed.py                  # one time — loads what bro knows about you
streamlit run app.py            # http://localhost:8501
```

Trouble? `python diagnose.py` prints the full picture (key, MCP, Gemini).

## Telegram (Phase 3)

```bash
# Telegram app → @BotFather → /newbot → copy the token into .env
python telegram_bot.py     # then message your bot once — it replies
                           # with your chat ID for OMNI_OWNER_CHAT_ID
```
Same brain, same memory as the web UI — one Jarvis, two front doors.

## Roadmap

- ✅ Phase 1 — routed brain + personality + seeded memory + tools
- ✅ Phase 2 — browser control (Playwright daemon + 6 tools, persistent profile)
- ✅ Phase 3 — Telegram reach (owner-locked bot, same brain + memory)
- ✅ Phase 4 — proactive scheduler (timed reminders fire on Telegram + 08:00 morning brief with deadline radar)
- ✅ Phase 5 — voice (Telegram voice notes: Gemini STT hears Tanglish, TTS replies aloud)
- ⬜ Later — consensus verify-mode, eval suite (self-learning last, by design)
