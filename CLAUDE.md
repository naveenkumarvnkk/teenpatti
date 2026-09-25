# Teen Patti Agents — instructions for Claude Code

## What this is

A multi-agent demo served at https://www.meetnaveenkunisetty.com/teenpatti/. A dealer agent runs a
Teen Patti table and two player agents bet on their three cards. A visitor enters a name, clicks
"Deal me in" and watches the game stream live. Play money only.

This is its own project. The portfolio (`~/Documents/Projects/portfolio`) owns the domain and its
router Worker forwards `/teenpatti/*` here with the prefix stripped. Never put portfolio code here.
Domain colour: **Agentic AI olive `#5C5A2E`** (tint `#ECEBDD`); follow the portfolio's design system.

## Product rules (from Naveen)

- Never show model or provider names (Ollama, Qwen, ...) or how it is hosted (tunnel, Mac) on any
  public page, including the portfolio card. Visitors see only the agents and the game.
- Seat 1 is the visitor's typed name (played by an AI agent); seat 2 is Meera; dealer is Raju.
- Agent lines are spoken in the first person to the table: no "we", "user", "assistant", and no
  leaked reasoning. Free-text model output is never shown directly: every call uses a JSON schema.
- Show whose turn it is at all times (the `turn` SSE event drives the sticky status bar).
- Quality over latency is fine, but `think: true` on qwen3:4b took more than 60 s per move on the
  8 GB Mac, so it stays off.

## Layout

- `seats.yaml` — the only place to configure agents: `defaults` (provider/model/base_url), `game`
  rules knobs, `dealer` and `players` (each: `name`, `instructions`, optional `provider`/`model`/
  `temperature` overrides). Re-read at the start of every game; no restart needed.
- `engine.py` — deterministic rules: deck, hand ranking, blind/seen betting, pot, show. **No LLM
  here.** Models never decide legality or winners.
- `agents.py` — `PlayerAgent.decide()` returns a JSON action validated against legal moves (falls
  back to chaal, flagged `auto` in the UI); `DealerAgent.narrate()` for table talk. Add new
  providers (Groq/OpenRouter for "Option C") in `Agent.chat`.
- `app.py` — FastAPI. `GET /api/play?name=` streams SSE events; one game at a time behind a lock
  with a visible queue (8 GB Mac).
- `static/index.html` — the whole UI. Use **relative** URLs so it works under `/teenpatti/`.

## Runtime (no inbound to the Mac)

- `deploy/install-app.sh` — launchd service `com.naveen.teenpatti`, bound to `127.0.0.1:8765`.
- `deploy/setup-tunnel.sh` — one-time Cloudflare Tunnel `teenpatti` → origin
  `teenpatti-origin.meetnaveenkunisetty.com`, run by launchd `com.naveen.teenpatti-tunnel`.
  The Mac only makes outbound connections; no router ports, no public IP.
- Hours: open 8 AM–8 PM Central. The portfolio router's `HOURS["/teenpatti"]` gates visitors and
  serves the portfolio's `site/downtime.html` (card animation + countdown) outside hours or when
  the origin is down. On the Mac, `deploy/install-hours.sh` installs launchd `com.naveen.teenpatti-hours`,
  which runs a copy of `deploy/hours.sh` from `~/Library/Application Support/teenpatti/` (launchd
  can't run scripts in ~/Documents) at 07:58, 20:05 and login. Change hours in both places.
- Models: Ollama on the same Mac. Current plan "Option B" = one small model, three personas.
  Next steps if quality is poor: Option A (three tiny different models) or C (players on APIs).

## Run locally

```bash
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8765 --reload
```
