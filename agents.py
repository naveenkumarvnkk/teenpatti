"""LLM agents. Each seat is built from seats.yaml: name + instructions + provider/model."""
import json
import logging
import re

import httpx

log = logging.getLogger("uvicorn.error")


class Agent:
    def __init__(self, name, instructions, provider, model, base_url, temperature=0.8, think=False, **_):
        self.name, self.instructions = name, instructions.strip()
        self.provider, self.model, self.base_url = provider, model, base_url
        self.temperature, self.think = temperature, think

    @property
    def label(self):
        return f"{self.provider}:{self.model}"

    async def chat(self, prompt, schema=None):
        if self.provider == "ollama":
            return await self._ollama(prompt, schema)
        raise ValueError(f"Unknown provider {self.provider}")  # add openai/groq/etc. here for Option C

    async def _ollama(self, prompt, schema):
        body = {
            "model": self.model,
            "stream": False,
            "think": self.think,  # qwen3 etc.: skip slow reasoning traces
            "options": {"temperature": self.temperature, "num_predict": 1200 if self.think else 200},
            "keep_alive": "30m",
            "messages": [
                {"role": "system", "content": self.instructions},
                {"role": "user", "content": prompt},
            ],
        }
        if schema:
            body["format"] = schema  # structured output: the reply must match this JSON schema
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{self.base_url}/api/chat", json=body)
            r.raise_for_status()
            return r.json()["message"]["content"].strip()


VOICE = (
    "Speak only as yourself, in the first person, to the people at the table. "
    "Never mention being an AI, a model, a user or an assistant, and never speak as \"we\"."
)


def clean(text, name):
    """Strip labels/quotes small models add ("Raju: ...", '"...'), and odd meta words."""
    text = text.strip().strip('"“”').strip()
    text = re.sub(rf"^(\*\*)?{re.escape(name)}(\*\*)?\s*[:\-–]\s*", "", text, flags=re.I)
    text = re.sub(r"^(We|User|Assistant)\s*[,:]\s*", "", text)
    text = text.strip().strip('"“”').strip()
    return text[:1].upper() + text[1:]


def sentences(text, n=2):
    """Keep at most n whole sentences, so a line is never cut mid-word."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(parts[:n])


class PlayerAgent(Agent):
    async def decide(self, state, legal):
        prompt = (
            f"{state}\n\nLegal actions: {', '.join(legal)}.\n"
            f"{VOICE}\n"
            'Reply with JSON: {"action": "<one legal action>", '
            '"say": "<what you say out loud at the table, max 15 words>"}'
        )
        raw = ""
        try:
            schema = {
                "type": "object",
                "properties": {"action": {"type": "string", "enum": legal}, "say": {"type": "string"}},
                "required": ["action", "say"],
            }
            raw = await self.chat(prompt, schema=schema)
            data = json.loads(raw)
            action = str(data.get("action", "")).lower().strip()
            say = sentences(clean(str(data.get("say", "")), self.name), 1)
        except Exception as e:
            log.warning("%s: model call failed: %r raw=%r", self.name, e, raw[:200])
            action, say = "", ""
        if action not in legal:  # never trust the model with rules
            log.warning("%s: illegal action %r (legal %s) raw=%r", self.name, action, legal, raw[:200])
            return "chaal", say or "(hmm...)", True
        return action, say, False


class DealerAgent(Agent):
    async def narrate(self, event):
        try:
            # A schema (not free text) stops reasoning models from leaking their planning.
            raw = await self.chat(
                f"What just happened at the table: {event}\n"
                "Use only these facts; never invent cards, hands or numbers.\n"
                f"Say your next line out loud as {self.name}. {VOICE}\n"
                'Reply with JSON: {"line": "<your spoken line, one or two sentences>"}',
                schema={"type": "object", "properties": {"line": {"type": "string"}}, "required": ["line"]},
            )
            return sentences(clean(str(json.loads(raw).get("line", "")), self.name)) or event
        except Exception as e:
            log.warning("%s: narration failed: %r", self.name, e)
            return event


def build(cfg, player_name):
    """Seats from seats.yaml; "{player}" becomes the visitor's name, {name}/{opponent} fill personas.
    Returns (dealer, players, names); a human seat's player is None."""
    d = cfg["defaults"]
    dealer = DealerAgent(**{**d, **cfg["dealer"]})
    seats = [dict(p, name=p["name"].replace("{player}", player_name)) for p in cfg["players"]]
    players = []
    for i, p in enumerate(seats):
        if p.get("kind") == "human":  # played by the visitor from the page, not a model
            players.append(None)
            continue
        opp = seats[1 - i]["name"]
        text = p["instructions"].replace("{name}", p["name"]).replace("{opponent}", opp)
        players.append(PlayerAgent(**{**d, **p, "instructions": text}))
    return dealer, players, [p["name"] for p in seats]
