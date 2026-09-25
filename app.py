import asyncio
import json
import re
import secrets
import time
from typing import Literal
from pathlib import Path

import httpx
import yaml
from fastapi import FastAPI, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse

import agents
import logging
from engine import Hand, Player, describe, strength

ROOT = Path(__file__).parent
log = logging.getLogger("uvicorn.error")
NOTIFIER = "http://127.0.0.1:8790/notify"  # shared notifier (portfolio/services/notifier) -> Discord


def notify(text):
    """Fire-and-forget: a game never waits on, or fails because of, the notifier."""
    async def send():
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                await c.post(NOTIFIER, json={"app": "teenpatti", "text": text})
        except Exception as e:
            log.info("notifier unavailable: %r", e)
    asyncio.get_running_loop().create_task(send())
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)  # no public API docs


def _origin_key():
    env = ROOT / ".env"
    for line in env.read_text().splitlines() if env.exists() else []:
        if line.startswith("ORIGIN_KEY="):
            return line.split("=", 1)[1].strip()
    return ""


ORIGIN_KEY = _origin_key()


def trim_log(path=ROOT / "teenpatti.log", limit=5_000_000, keep=1_000_000):
    """Runs at startup (each morning's open): if the log is over 5 MB, keep only the last 1 MB.
    Rewritten in place, since launchd holds it open in append mode."""
    try:
        if path.stat().st_size <= limit:
            return
        with open(path, "r+b") as f:
            f.seek(-keep, 2)
            tail = f.read().split(b"\n", 1)[-1]  # start at a whole line
            f.seek(0)
            f.write(b"--- older lines trimmed ---\n" + tail)
            f.truncate()
    except OSError as e:
        log.warning("log trim failed: %r", e)


trim_log()


# The only requests this app answers. Everything else (other paths/methods, uploads, form posts,
# non-JSON bodies, big bodies) is refused before any handler runs.
ROUTES = {("GET", "/"), ("GET", "/api/play"), ("POST", "/api/move")}
MAX_BODY = 512  # bytes; a move is ~100


@app.middleware("http")
async def only_via_router(request: Request, call_next):
    # Public traffic arrives through the tunnel; only the portfolio router knows this key.
    # Local requests (127.0.0.1 without a tunnel header) are allowed for development.
    via_tunnel = "cf-ray" in request.headers
    if ORIGIN_KEY and via_tunnel and request.headers.get("x-origin-key") != ORIGIN_KEY:
        return PlainTextResponse("Not found", status_code=404)
    if (request.method, request.url.path) not in ROUTES:
        return PlainTextResponse("Not found", status_code=404)
    if request.method == "POST":
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype != "application/json" or "transfer-encoding" in request.headers:
            return PlainTextResponse("Unsupported", status_code=415)
        try:
            size = int(request.headers.get("content-length", ""))
        except ValueError:
            return PlainTextResponse("Length required", status_code=411)
        if size > MAX_BODY:
            return PlainTextResponse("Too large", status_code=413)
    elif request.headers.get("content-length", "0") not in ("", "0"):
        return PlainTextResponse("Bad request", status_code=400)  # GETs carry no body
    return await call_next(request)
table_lock = asyncio.Lock()  # one game at a time (8 GB Mac)
waiting = 0


TURN_SECONDS = 45     # a human seat that doesn't move in time packs the hand
MAX_TIMEOUTS = 2      # ...and after this many in a row the game ends
NEXT_SECONDS = 60     # between hands: no answer to "next hand or end?" ends the game
pending = {}          # game id -> {"token", "seat", "legal", "future"} while a human must move


class Move(BaseModel):
    """The only body the app accepts. Unknown fields, wrong types or long strings -> 422."""
    model_config = ConfigDict(extra="forbid", strict=True)
    game: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    token: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    action: Literal["chaal", "raise", "see", "show", "pack", "next", "end"]


def load_config():
    return yaml.safe_load((ROOT / "seats.yaml").read_text())  # re-read each game: edit & go


def sse(kind, **data):
    return f"data: {json.dumps({'type': kind, **data})}\n\n"


def state_for(hand, me, opp, history=()):
    if me.seen:
        pct = strength(me.cards)
        verdict = "STRONG" if pct >= 75 else "DECENT" if pct >= 45 else "WEAK"
        cards = (f"{' '.join(me.cards)} ({describe(me.cards)}). This hand beats {pct}% of all "
                 f"possible hands: {verdict}.")
    else:
        cards = ("NOT looked at yet. You are playing BLIND (blind bets cost half), so you do not "
                 "know your hand: never claim it is strong or weak. Choose \"see\" to look at it")
    return (
        f"Teen Patti. Your cards: {cards}. Your chips: {me.chips}.\n"
        f"Opponent {opp.name} is {'SEEN' if opp.seen else 'BLIND'} with {opp.chips} chips.\n"
        f"Pot: {hand.pot}. Your chaal costs {hand.cost(me)}, raise costs {hand.cost(me, True)}.\n"
        "Rankings high to low: Trail, Pure Sequence, Sequence, Color, Pair, High Card."
        + (f"\n{opp.name}'s moves this hand: {', '.join(history)}." if history else "")
    )


async def run_game(visitor):
    cfg = load_config()
    g = cfg["game"]
    log.info("GAME_START player=%s waiting=%d", visitor, waiting)
    notify(f"🃏 **{visitor}** just sat down at the table" + (f" ({waiting} more waiting)" if waiting else "") + ".")
    dealer, pagents, names = agents.build(cfg, visitor)
    seats = [Player(n, g["starting_chips"]) for n in names]
    human = [i for i, a in enumerate(pagents) if a is None]
    # Secret per game: only this visitor's stream learns it, so only their tab can move their seat.
    game_id, token = secrets.token_urlsafe(16), secrets.token_urlsafe(24)
    yield sse("setup", dealer={"name": dealer.name}, hands=g["hands"], game=game_id, token=token,
              turn_seconds=TURN_SECONDS, human=human,
              players=[{"name": p.name, "chips": g["starting_chips"]} for p in seats])
    yield sse("turn", who=dealer.name, seat=-1)
    yield sse("dealer", text=await dealer.narrate(
        f"{seats[0].name} has just sat down to play against {seats[1].name}. Welcome them both."))

    timeouts = 0
    for n in range(1, g["hands"] + 1):
        if any(p.chips < g["boot"] for p in seats) or timeouts >= MAX_TIMEOUTS:
            break
        hand = Hand(seats, g)
        yield sse("hand", n=n, pot=hand.pot, chips=[p.chips for p in seats])
        yield sse("turn", who=dealer.name, seat=-1)
        yield sse("dealer", text=await dealer.narrate(
            f"Hand {n} of {g['hands']} starts. Each player paid the boot (entry bet) of {g['boot']} chips "
            "and got three face-down cards."))
        turn, done, history = n % 2, False, [[], []]
        while not done:
            me, opp = seats[turn], seats[1 - turn]
            legal = hand.legal_actions(me)
            if pagents[turn] is None:  # the visitor's own seat: wait for their click
                deadline = time.time() + TURN_SECONDS
                yield sse("turn", who=me.name, seat=turn, human=True, legal=legal, deadline=deadline,
                          chaal=hand.cost(me), raise_cost=hand.cost(me, True))
                fut = asyncio.get_running_loop().create_future()
                pending[game_id] = {"token": token, "legal": legal, "future": fut}
                try:
                    action, say, fallback = await asyncio.wait_for(fut, TURN_SECONDS), "", False
                    timeouts = 0
                except asyncio.TimeoutError:
                    action, say, fallback = "pack", "", True
                    timeouts += 1
                finally:
                    pending.pop(game_id, None)
            else:
                yield sse("turn", who=me.name, seat=turn)
                action, say, fallback = await pagents[turn].decide(
                    state_for(hand, me, opp, history[1 - turn]), legal)
            history[turn].append(action)
            text, done = hand.apply(me, action)
            log.info("MOVE hand=%d seat=%d who=%s action=%s auto=%s", n, turn,
                     "human" if pagents[turn] is None else me.name, action, fallback)
            yield sse("move", player=me.name, action=action, say=say, fallback=fallback, text=text,
                      pot=hand.pot, chips=[p.chips for p in seats], seen=[p.seen for p in seats])
            if action == "see" and pagents[turn] is None:
                yield sse("yourcards", seat=turn, cards=me.cards, hand=describe(me.cards))
            if action != "see":
                turn = 1 - turn
        win = hand.winner()
        win.chips += hand.pot
        reveal = [{"name": p.name, "cards": p.cards, "hand": describe(p.cards), "packed": p.packed} for p in seats]
        yield sse("result", winner=win.name, pot=hand.pot, reveal=reveal, chips=[p.chips for p in seats])
        lose = seats[1 - seats.index(win)]
        how = (f"{lose.name} packed, so {win.name} takes it without a show" if lose.packed
               else f"{win.name}'s {describe(win.cards)} ({' '.join(win.cards)}) beat {lose.name}'s "
                    f"{describe(lose.cards)} ({' '.join(lose.cards)}) at the show")
        yield sse("turn", who=dealer.name, seat=-1)
        yield sse("dealer", text=await dealer.narrate(f"{win.name} wins the pot of {hand.pot}: {how}."))
        last = n == g["hands"] or any(p.chips < g["boot"] for p in seats) or timeouts >= MAX_TIMEOUTS
        if human and not last:  # the visitor decides whether to deal the next hand
            deadline = time.time() + NEXT_SECONDS
            yield sse("between", next_hand=n + 1, hands=g["hands"], deadline=deadline)
            fut = asyncio.get_running_loop().create_future()
            pending[game_id] = {"token": token, "legal": ["next", "end"], "future": fut}
            try:
                choice = await asyncio.wait_for(fut, NEXT_SECONDS)
            except asyncio.TimeoutError:
                choice = "end"
            finally:
                pending.pop(game_id, None)
            log.info("BETWEEN hand=%d choice=%s", n, choice)
            if choice == "end":
                break

    champ = max(seats, key=lambda p: p.chips)
    log.info("GAME_END player=%s winner=%s chips=%s", visitor, champ.name,
             "/".join(f"{p.name}:{p.chips}" for p in seats))
    notify(f"🏁 {visitor}'s game finished. Winner: **{champ.name}** ("
           + " · ".join(f"{p.name} {p.chips}" for p in seats) + ")")
    yield sse("turn", who=dealer.name, seat=-1)
    yield sse("dealer", text=await dealer.narrate(
        f"Game over. {champ.name} leads with {champ.chips} chips. Thank {seats[0].name} for playing."))
    yield sse("end")


@app.get("/api/play")
async def play(name: str = Query(..., max_length=30)):
    visitor = re.sub(r"[^\w .-]", "", name).strip()
    if len(visitor) < 2:  # no anonymous games: the page requires a name, and so does the API
        return JSONResponse({"error": "Enter your name (at least 2 letters or numbers) to play."}, status_code=400)

    async def stream():
        global waiting
        waiting += 1
        queued = True
        try:
            while table_lock.locked():
                yield sse("queue", position=waiting)
                await asyncio.sleep(3)
            async with table_lock:
                waiting -= 1
                queued = False
                async for ev in run_game(visitor):
                    yield ev
        finally:
            if queued:
                waiting -= 1

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/move")
async def move(m: Move):
    p = pending.get(m.game)
    # Same answer for "no such game" and "wrong token", so the endpoint can't be probed.
    if not p or not secrets.compare_digest(p["token"], m.token) or p["future"].done():
        return JSONResponse({"error": "Not your turn."}, status_code=409)
    if m.action not in p["legal"]:  # the engine's rules decide, never the client
        return JSONResponse({"error": "That move isn't allowed now.", "legal": p["legal"]}, status_code=409)
    p["future"].set_result(m.action)
    return {"ok": True}


@app.get("/")
async def index():
    # no-cache: browsers must fetch the latest page after each deploy, not replay an old copy
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-cache"})
