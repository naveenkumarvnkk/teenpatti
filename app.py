import asyncio
import json
import re
from pathlib import Path

import yaml
from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse

import agents
from engine import Hand, Player, describe, strength

ROOT = Path(__file__).parent
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)  # no public API docs


def _origin_key():
    env = ROOT / ".env"
    for line in env.read_text().splitlines() if env.exists() else []:
        if line.startswith("ORIGIN_KEY="):
            return line.split("=", 1)[1].strip()
    return ""


ORIGIN_KEY = _origin_key()


@app.middleware("http")
async def only_via_router(request: Request, call_next):
    # Public traffic arrives through the tunnel; only the portfolio router knows this key.
    # Local requests (127.0.0.1 without a tunnel header) are allowed for development.
    via_tunnel = "cf-ray" in request.headers
    if ORIGIN_KEY and via_tunnel and request.headers.get("x-origin-key") != ORIGIN_KEY:
        return PlainTextResponse("Not found", status_code=404)
    return await call_next(request)
table_lock = asyncio.Lock()  # one game at a time (8 GB Mac)
waiting = 0


def load_config():
    return yaml.safe_load((ROOT / "seats.yaml").read_text())  # re-read each game: edit & go


def sse(kind, **data):
    return f"data: {json.dumps({'type': kind, **data})}\n\n"


def state_for(hand, me, opp):
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
    )


async def run_game(visitor):
    cfg = load_config()
    g = cfg["game"]
    dealer, pagents = agents.build(cfg, visitor)
    seats = [Player(a.name, g["starting_chips"]) for a in pagents]
    yield sse("setup", dealer={"name": dealer.name}, hands=g["hands"],
              players=[{"name": a.name, "chips": g["starting_chips"]} for a in pagents])
    yield sse("turn", who=dealer.name, seat=-1)
    yield sse("dealer", text=await dealer.narrate(
        f"{seats[0].name} has just sat down to play against {seats[1].name}. Welcome them both."))

    for n in range(1, g["hands"] + 1):
        if any(p.chips < g["boot"] for p in seats):
            break
        hand = Hand(seats, g)
        yield sse("hand", n=n, pot=hand.pot, chips=[p.chips for p in seats])
        yield sse("turn", who=dealer.name, seat=-1)
        yield sse("dealer", text=await dealer.narrate(
            f"Hand {n} of {g['hands']} starts. Each player paid the boot (entry bet) of {g['boot']} chips "
            "and got three face-down cards."))
        turn, done = n % 2, False
        while not done:
            me, opp = seats[turn], seats[1 - turn]
            yield sse("turn", who=me.name, seat=turn)
            action, say, fallback = await pagents[turn].decide(state_for(hand, me, opp), hand.legal_actions(me))
            text, done = hand.apply(me, action)
            yield sse("move", player=me.name, action=action, say=say, fallback=fallback, text=text,
                      pot=hand.pot, chips=[p.chips for p in seats], seen=[p.seen for p in seats])
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

    champ = max(seats, key=lambda p: p.chips)
    yield sse("turn", who=dealer.name, seat=-1)
    yield sse("dealer", text=await dealer.narrate(
        f"Game over. {champ.name} leads with {champ.chips} chips. Thank {seats[0].name} for playing."))
    yield sse("end")


@app.get("/api/play")
async def play(name: str = Query("Guest", max_length=30)):
    visitor = re.sub(r"[^\w .-]", "", name).strip() or "Guest"

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


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")
