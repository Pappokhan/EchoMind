# EchoMind... write anything, get a second read!

You write something and an LLM reflects it back. Pick what you're writing -
**Journal, Brainstorm, Writing & Messages, Decision Helper, Study Notes, or
General** - and EchoMind adapts: the prompt, the framing, and the labels on
the result all change to fit, while the underlying analysis (a mood/tone
read, a score, a few keywords, a short reflection, one concrete next step)
stays the same shape. Every model call is timed and logged, and a live
dashboard turns those logs into real MLOps signal - latency, error rate,
throughput, token usage - next to your own trend over time and a breakdown
of which modes you actually use.

Built with **Flask**, a **free LLM API (Google Gemini)**, and no build step: open a
browser and go.

## Modes

| Mode | For | Result reads as |
|------|-----|------------------|
| 🌙 Journal | Feelings, your day, anything you need out of your head | Mood · Sentiment · Themes · Reflection · Try this |
| 💡 Brainstorm | Rough idea lists, project notes, stream-of-consciousness | Energy · Novelty · Idea clusters · Insight · Next step |
| ✍️ Writing & Messages | Emails, DMs, posts, anything before you hit send | Tone · Clarity · Key points · Feedback · Suggested edit |
| ⚖️ Decision Helper | A decision you're weighing, laid out in writing | Leaning · Confidence · Key factors · Analysis · Next step |
| 📚 Study Notes | Notes or an explanation you're using to learn something | Grasp · Depth · Key concepts · Summary · Review tip |
| 🔎 General | Anything else | Category · Tone · Keywords · Analysis · Suggestion |

Add a mode by editing the `MODES` dict in `prompts.py` — the system prompt,
UI copy, and result labels for every mode live in one place, and the app,
API, and dashboard pick it up automatically.

## Digest — the second read, second-order

Single entries get reflected back one at a time. **Digest** (`/digest`) looks
back across a whole window — last 7 or 30 days, optionally filtered to one
mode — and synthesizes what's recurring and what's shifted: a headline, a
mood trend, a short narrative, the real recurring themes, and one concrete
thing worth your attention next.

It's built on top of the analyses you already have, not a fresh read of raw
text: the digest prompt is fed each entry's already-computed mood, score,
themes, and reflection rather than the original writing, so it stays small
and cheap no matter how much you've written, and it's an honest synthesis of
what EchoMind already told you — not a second AI reading your private
entries from scratch. Every digest is timed and logged through the same
MLOps pipeline as single-entry analysis (`endpoint: /api/digest` in
`request_logs`/`requests.jsonl`), and past digests are saved to their own
`digests` table so you can look back at how the picture changed over time.
Runs in demo mode too — no API key means a plain aggregate (average score,
top themes, a simple trend) instead of an AI-written narrative.

## RAG, function calling, and cross-session memory

Every `/api/analyze` call is now personalized to the signed-in account, on
top of the original single-shot mode/prompt system:

- **RAG (retrieval-augmented generation).** Before calling the model,
  `database.search_similar_entries()` searches that user's own past entries
  via a SQLite FTS5 index (`entries_fts`, kept in sync automatically by
  triggers on `entries`) for the ones most topically related to what's just
  been written, ranked by BM25. No embeddings API needed — this runs
  entirely offline against SQLite, so it works on Gemini's free tier with no
  extra cost or dependency. The top few hits are folded into the prompt as
  a labeled "Background context" block; the model is instructed to use
  them only to notice real patterns, never to quote them back or invent a
  connection that isn't there.
- **Function calling.** The model can call read-only tools —
  `get_writing_streak`, `get_mood_trend`, `search_past_entries` — via
  Gemini's OpenAI-compatible `tools` API instead of guessing at facts it
  could just check (see `tools.py`). The loop (`llm_service._run_tool_loop`)
  is capped at 3 rounds, with the last round forced tool-free, so a model
  that keeps calling tools can't turn one request into unbounded upstream
  cost — it always terminates with the plain JSON analysis the rest of the
  app expects.
- **Cross-session memory.** Two of the tools, `remember_about_me` /
  `forget_about_me`, are the write path into a new `user_memory` table:
  durable facts about the writer (a job, an ongoing project, a recurring
  goal) that persist across visits, not just within one request. Saved
  facts are fed back into the "Background context" block on every future
  analysis for that account. The model is explicitly instructed to save
  only stable, self-stated facts — never a single day's mood or anything
  sensitive like health details. Users can see and delete what's been
  saved on their **Profile** page ("What EchoMind remembers about you"),
  backed by `GET /api/memory` and `DELETE /api/memory/<key>`.

Every `/api/analyze` response now also reports `tools_used`,
`rag_entries_used`, and `memory_facts_used` for transparency, and
`request_logs` gained matching `tool_calls_count` / `rag_hits` columns so
this shows up in the MLOps dashboard's existing metrics pipeline too. All
three features degrade gracefully: with no saved memory and no matching
past entries (e.g. a brand-new account), analysis runs exactly as it did
before — zero tool calls, zero RAG hits, same JSON contract.

## Chat — a conversation that carries over, not a new thread every visit

`/chat` is a persistent chat companion, separate from the one-shot
entry/mode flow above: instead of submitting a single piece of writing and
getting one analysis back, you talk to EchoMind turn by turn, and the
conversation is still there the next time you open the page — today, next
week, whenever.

- **Text, image, or PDF input.** Alongside typed text, you can attach one
  image (PNG/JPEG/WEBP/GIF) or one PDF per message via the paperclip-style
  buttons next to the composer. The file is validated (type + an 8MB size
  cap — see `config.MAX_ATTACHMENT_SIZE_BYTES`), stored on disk under
  `DATA_DIR/chat_uploads/<user_id>/`, and sent to the model as part of that
  turn as a multimodal message (`llm_service._user_turn_content` — Gemini's
  OpenAI-compatible endpoint accepts the file inline as a base64 data URI,
  images and PDFs alike). Only the *current* turn's file bytes are ever
  sent to the model; earlier turns that had an attachment are represented
  in later requests as a short "[attached a ...]" text note instead of
  being re-uploaded every time, so a long thread doesn't balloon in size or
  cost. Attachments are served back to their owner only, via
  `GET /api/chat/attachment/<message_id>`, and are deleted from disk when a
  conversation is cleared.
- **Every turn is stored.** `POST /api/chat` saves both your message and the
  model's reply to a new `chat_messages` table (`database.save_chat_message`)
  before/after calling the model, and `GET /api/chat/history` replays the
  thread back on page load — so refreshing, signing out, or coming back
  three days later all pick up exactly where you left off.
- **Multi-day continuity, not just same-session memory.** `chat_reply()`
  (`llm_service.py`) folds the last 20 turns back in as real conversation
  history *and* layers the same "Background context" block used elsewhere
  (`_build_context_block`: saved memory facts + related past journal
  entries), so the companion isn't starting from zero even on a brand-new
  session days later.
- **The same tools, the same memory.** Chat uses the identical `tools.py`
  function-calling loop as entry analysis — `get_writing_streak`,
  `get_mood_trend`, `search_past_entries`, and the `remember_about_me` /
  `forget_about_me` write path — so a fact you mention in chat (e.g. "I
  started a new job") can be saved once and then inform both future chats
  *and* future journal analysis, and vice versa. One durable memory, two
  ways to talk to it.
- **Clearing is explicit.** `DELETE /api/chat` wipes the thread for that
  account (with a confirm prompt in the UI) — saved memory facts and past
  journal entries are untouched, since they're a separate table.
- Degrades the same way everything else does: with no `GEMINI_API_KEY`,
  `/chat` still works end-to-end, just with a placeholder demo-mode reply
  instead of a real conversation.

## Why this app

- **Interesting & useful**: reflective writing — of any kind — is a
  genuinely good fit for an LLM: better with a second read than a first one.
- **Unique**: most "LLM starter apps" stop at a chat box. This one treats the
  model as a service with a face — it has a health check, retries, a
  graceful offline fallback, and a metrics dashboard, not just a text box.
- **General-purpose by design**: one JSON contract (mood / score / themes /
  reflection / action), six different prompts and framings on top of it, so
  the same small app is useful for journaling, brainstorming, proofreading a
  message, weighing a decision, or checking your own understanding.
- **Real MLOps, small scale**: every request is logged to both an
  append-only JSONL file (`logs/requests.jsonl`) and SQLite, tracking
  latency, success/error/fallback status, token usage, and prompt version —
  the same shape of signal a production system would track, just sized for
  one laptop.

## Quickstart

```bash
cd echomind
python3 -m venv .venv && source .venv/bin/activate 
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Open **http://localhost:5000**.

**No API key yet?** The app still runs — it falls back to a small local
keyword-based sentiment heuristic ("demo mode", shown clearly in the UI) so
you can try the whole flow before signing up for anything.

## Run with Docker

The app now ships with a proper container setup: a multi-stage `Dockerfile`
that runs as a non-root user behind `gunicorn` (not the Flask dev server),
a `docker-compose.yml` with persistent volumes for the SQLite DB and JSONL
logs, and a built-in `HEALTHCHECK` against `/health`.

```bash
cd echomind
cp .env.example .env
docker compose up --build
```

Open **http://localhost:5000**. Data and logs persist in the
`echomind-data` / `echomind-logs` Docker volumes across rebuilds — delete
them with `docker compose down -v` if you want a clean slate.

Useful env vars (set in `.env`, all optional):

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `5000` | Host port the container is published on |
| `WEB_CONCURRENCY` | `2` | gunicorn worker count |
| `GUNICORN_TIMEOUT` | `60` | gunicorn worker timeout (seconds) |
| `FLASK_SECRET_KEY` | *(dev default)* | Set a real random value before exposing this publicly |

Without Docker Compose, plain `docker build`/`docker run` also works — see
the `Dockerfile` for the image contract (it reads `PORT`, `WEB_CONCURRENCY`,
`DATA_DIR`, `LOG_DIR` the same way).

## Project structure

```
echomind/
├── app.py                 # Flask routes
├── config.py               # env-driven configuration
├── database.py              # SQLite: entries (+ mode), request_logs, digests, chat_messages
├── llm_service.py           # Gemini API client: retries, timing, fallback, mode-aware,
│                             #   RAG context assembly, bounded tool-calling loop,
│                             #   chat_reply() for the multi-day chat companion
├── digest_service.py         # Digest: synthesizes many entries into one summary
├── streaks.py                # Writing-streak computation (current/longest/active days)
├── prompts.py                # MODES: per-mode prompts, UI copy, result labels
├── tools.py                   # Function-calling tools (streak/mood-trend/search/memory)
├── mlops/
│   ├── logger.py              # logs every LLM call (JSONL + SQLite)
│   └── metrics.py             # aggregates logs into dashboard metrics
├── templates/
│   ├── base.html, index.html, dashboard.html, digest.html
├── static/
│   ├── css/style.css           # design system (ink/echo theme)
│   └── js/main.js, dashboard.js, digest.js
├── data/                    # SQLite DB lives here (created at runtime)
├── logs/                    # requests.jsonl lives here (created at runtime)
├── requirements.txt
└── .env.example
```

## How the MLOps layer works

1. **Every** call to `analyze_journal_entry()` is timed with
   `time.perf_counter()`, whether it succeeds, errors, or falls back to the
   offline heuristic.
2. `mlops/logger.py` writes one record per call to `logs/requests.jsonl`
   (durable, shippable to any log pipeline) and to the `request_logs` SQLite
   table (queryable for the dashboard).
3. `mlops/metrics.py` aggregates those logs into: total requests, success
   rate, average & P95 latency, requests per day, and correlates them with
   the journal entries themselves (mood trend, sentiment average, top
   recurring themes).
4. The `/dashboard` page polls `/api/metrics` and renders it all with
   Chart.js — a lightweight, no-infrastructure model-observability panel.
5. `/health` gives a simple liveness probe showing which model and prompt
   version are currently active — useful before wiring up real uptime
   monitoring.
6. Prompts are versioned (`config.PROMPT_VERSION` in `prompts.py`) so future
   prompt changes can be correlated against metrics over time.

## API endpoints

| Method | Path            | Purpose                                          |
|--------|-----------------|---------------------------------------------------|
| GET    | `/`             | Public cover page if signed out; main UI (mode selector + composer) if signed in |
| GET    | `/dashboard`    | Metrics dashboard UI                              |
| GET    | `/digest`       | Digest UI (period + mode window synthesis)        |
| GET    | `/chat`         | Persistent, multi-day chat companion UI           |
| POST   | `/api/analyze`  | Analyze text for a given `mode`, returns JSON     |
| GET    | `/api/chat/history` | This account's chat thread (optional `?limit=`) |
| POST   | `/api/chat`     | Send a chat message, returns the reply as JSON    |
| DELETE | `/api/chat`     | Clear this account's chat thread                  |
| GET    | `/api/entries`  | Recent entries (optional `?mode=` filter)         |
| GET    | `/api/modes`    | Available modes + their UI/label metadata         |
| POST   | `/api/digest`   | Generate & save a digest for a period             |
| GET    | `/api/digests`  | Recently generated digests (optional `?limit=`)   |
| GET    | `/api/metrics`  | Aggregated MLOps + result metrics, incl. mode mix |
| GET    | `/api/streak`   | Current/longest writing streak, total active days |
| GET    | `/api/memory`   | Durable facts saved about the signed-in user       |
| DELETE | `/api/memory/<key>` | Remove one saved memory fact                   |
| GET    | `/health`       | Liveness probe / active model info                |

`POST /api/analyze` body: `{"entry_text": "...", "mode": "journal"}` — `mode`
is optional and defaults to `journal`; must be one of the ids returned by
`/api/modes` (`journal`, `brainstorm`, `writing`, `decision`, `study`, `general`).
Its response now also includes `tools_used`, `rag_entries_used`, and
`memory_facts_used` (see "RAG, function calling, and cross-session memory"
above).

`POST /api/digest` body: `{"days": 7, "mode": null}` — `days` is `7` or `30`
(defaults to `7`), `mode` optionally limits the digest to one mode's entries.

`POST /api/chat` body: JSON `{"message": "..."}` (max 2000 chars), or
`multipart/form-data` with a `message` field and an optional `attachment`
file field (one image or PDF, ≤8MB — see "Chat" above). Response includes
`reply`, an `attachment` object (`name`/`mime`/`kind`/`url`) when one was
sent, plus the same transparency fields as `/api/analyze`: `tools_used`,
`rag_entries_used`, `memory_facts_used`, `demo_mode`, `latency_ms`.
`GET /api/chat/history` returns `{"messages": [...], "day_count": N}`,
oldest first, each message shaped
`{"role": "user"|"assistant", "content", "created_at", "attachment"?}`.
`GET /api/chat/attachment/<message_id>` streams back one previously-sent
file, scoped to the signed-in owner.

## Notes on the free API

This uses [Google Gemini](https://aistudio.google.com/apikey) because its
free tier needs no credit card and is quite generous for a personal or demo
project (as of writing: ~1,500 requests/day and up to 1M tokens/minute on
`gemini-3.5-flash-lite`). It's accessed through Gemini's OpenAI-compatible chat
completions endpoint, so the request/response shapes — including the
`tools`/function-calling contract — match the OpenAI format the rest of this
codebase already speaks. Swapping providers only touches `llm_service.py`
and `config.py`; the rest of the app (storage, MLOps logging, UI) is
provider-agnostic.

## Production notes

Containerized deploys should use the Docker setup above — it already runs
behind `gunicorn`, persists SQLite + logs in volumes, and exposes a health
check. If you outgrow SQLite, swap the connection logic in `database.py`
for a real database; `logs/requests.jsonl` is append-only JSONL, so it
ships to any log aggregator (ELK, Datadog, etc.) without changes.

## Writing Streak

A small habit-tracking layer on top of the same `entries` table: `GET
/api/streak` (`streaks.py`) returns your current streak, longest streak, and
total active days, computed from the distinct calendar dates you've written
on. It's forgiving by design — like GitHub/Duolingo streaks, writing
yesterday keeps today's streak shown as "alive" rather than resetting to 0,
since the calendar day isn't over yet; it only breaks once a full day is
skipped. Shown as a badge in the homepage hero, refreshed after every entry
you analyze.

## Changelog

**Chat — persistent, multi-day conversation**:
- Added a `chat_messages` table and `/chat` UI: a turn-by-turn conversation
  companion that persists across sessions, separate from the one-shot
  entry/mode flow.
- Added `llm_service.chat_reply()`, sharing `_build_context_block` (RAG +
  saved memory) and the same `tools.py` function-calling loop as entry
  analysis, so `remember_about_me`/`forget_about_me` and the read-only
  tools work identically in chat and in journal analysis, against one
  shared memory.
- Added `GET/POST/DELETE /api/chat(/history)`; conversation history (last
  20 turns) is folded back into every reply so the thread has real
  continuity within a session and across separate days.
- Same demo-mode fallback pattern as `/api/analyze` and `/api/digest` — no
  `GEMINI_API_KEY` still means a fully working (if non-conversational) chat
  page, not a broken one.

**RAG, function calling, cross-session memory**:
- Added a SQLite FTS5 index (`entries_fts`) over each user's own entries,
  kept in sync via triggers, powering lexical RAG retrieval
  (`database.search_similar_entries`) with no embeddings API required.
- Added `tools.py`: 5 function-calling tools (`get_writing_streak`,
  `get_mood_trend`, `search_past_entries`, `remember_about_me`,
  `forget_about_me`) exposed to the model via Gemini's OpenAI-compatible
  `tools` API, run through a bounded (max 3 rounds) tool-calling loop in
  `llm_service._run_tool_loop`.
- Added a `user_memory` table for durable, cross-session facts the model
  learns about a writer, fed back into every future analysis; manageable
  by the user via `/api/memory` and a new section on the Profile page.
- `request_logs` gained `tool_calls_count` / `rag_hits` columns; every
  `/api/analyze` response now reports `tools_used`, `rag_entries_used`,
  and `memory_facts_used`.

**Hardening pass 2** (application security):
- Added CSRF protection (Flask-WTF) on every state-changing request — the
  four HTML forms (login, register, profile, logout) via an auto-included
  token, and the two JSON `fetch()` calls (`/api/analyze`, `/api/digest`)
  via an `X-CSRFToken` header read from a meta tag. CSRF failures return
  JSON on `/api/*` and a flash + redirect elsewhere instead of a raw 400.
- Added rate limiting (Flask-Limiter): `/login` and `/register` are capped
  per-IP to blunt credential stuffing / signup abuse, and `/api/analyze` +
  `/api/digest` are capped since each call is a real, metered request to
  the LLM provider — an unlimited public endpoint is a cost and
  availability risk, not just an abuse one. 429s return JSON on `/api/*`.
- Fixed a timing side-channel in `/login`: a nonexistent username used to
  return fast (skipping the password hash check) while a real username
  with a wrong password was slow, which leaks which usernames/emails are
  registered. Login now always runs a hash comparison, real or dummy.
- Added `Content-Security-Policy` (allowlisting only the two real
  third-party origins: Google Fonts, the Chart.js CDN) and
  `Strict-Transport-Security` (once cookies are marked Secure).
- Session/remember-me cookies are now explicitly `HttpOnly`, `SameSite=Lax`,
  and `Secure` by default (opt out only for local HTTP dev via
  `SESSION_COOKIE_SECURE=0`).
- Added `MAX_CONTENT_LENGTH` (256KB) so an oversized request body is
  rejected before it reaches app code, not after.
- `logs/requests.jsonl` now rotates (10MB × 5 backups) via
  `RotatingFileHandler` instead of growing forever on a long-running
  deployment.

**Hardening pass 1** (bug fixes + Docker/MLOps):
- Fixed a 500 crash in `POST /api/analyze` when `entry_text`/`mode` weren't
  strings.
- Clamped `/api/entries?limit=` to a sane range (was previously unbounded —
  a negative value could dump the whole table).
- `FLASK_DEBUG` now defaults to **off** instead of on (previous default
  silently exposed the Werkzeug debugger if unset).
- Enabled SQLite WAL mode + busy timeout so concurrent gunicorn workers
  don't hit "database is locked"; added indexes used by the dashboard/
  entries queries.
- Fixed incomplete HTML-escaping in `main.js` (only `<` was escaped).
- Dashboard now actually polls `/api/metrics` on an interval (previously
  loaded once despite the docs claiming otherwise) and properly destroys/
  recreates Chart.js instances instead of erroring on refresh.
- Replaced a no-op empty-state branch on the sentiment chart with a real
  "no entries yet" message.
- `llm_service.py` no longer burns a retry when a model returns a
  non-numeric `sentiment_score`; themes are capped at 4 per the stated
  contract.
- Added a multi-stage `Dockerfile` (non-root, gunicorn, `/health`
  HEALTHCHECK), `docker-compose.yml` with persistent volumes, and
  `.dockerignore`.
