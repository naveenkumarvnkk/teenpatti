"""Deterministic Teen Patti rules: deck, hand ranking, betting. No LLM in here."""
import random
from collections import Counter

RANKS = "23456789TJQKA"
SUITS = "♠♥♦♣"
CATEGORIES = ["High Card", "Pair", "Color", "Sequence", "Pure Sequence", "Trail"]


def new_deck():
    deck = [r + s for r in RANKS for s in SUITS]
    random.shuffle(deck)
    return deck


def _values(cards):
    return sorted((RANKS.index(c[0]) + 2 for c in cards), reverse=True)


def _sequence_rank(vals):
    """Return a rank for a run, or None. A-K-Q highest, A-2-3 second, then K-Q-J..."""
    v = sorted(vals)
    if v == [12, 13, 14]:
        return 100
    if v == [2, 3, 14]:
        return 99
    if v[1] == v[0] + 1 and v[2] == v[1] + 1:
        return v[2]
    return None


def evaluate(cards):
    """Return (category_index, tiebreak tuple). Higher tuple wins."""
    vals = _values(cards)
    flush = len({c[1] for c in cards}) == 1
    seq = _sequence_rank(vals)
    counts = Counter(vals)
    if len(counts) == 1:
        return (5, (vals[0],))
    if seq and flush:
        return (4, (seq,))
    if seq:
        return (3, (seq,))
    if flush:
        return (2, tuple(vals))
    if len(counts) == 2:
        pair = max(counts, key=counts.get)
        kicker = min(counts, key=counts.get)
        return (1, (pair, kicker))
    return (0, tuple(vals))


def describe(cards):
    return CATEGORIES[evaluate(cards)[0]]


class Player:
    def __init__(self, name, chips):
        self.name, self.chips = name, chips
        self.reset()

    def reset(self):
        self.cards, self.seen, self.packed, self.in_pot = [], False, False, 0


class Hand:
    """One hand between two players. `stake` is the current blind stake;
    a seen player pays 2x stake to chaal (4x to raise)."""

    def __init__(self, players, cfg):
        self.players, self.cfg = players, cfg
        self.deck = new_deck()
        self.pot, self.stake, self.turns = 0, cfg["boot"], 0
        for p in players:
            p.reset()
            p.cards = [self.deck.pop() for _ in range(3)]
            self._pay(p, cfg["boot"])

    def _pay(self, p, amt):
        amt = min(amt, p.chips)
        p.chips -= amt
        p.in_pot += amt
        self.pot += amt
        return amt

    def cost(self, p, raise_=False):
        mult = 2 if p.seen else 1
        return self.stake * mult * (2 if raise_ else 1)

    def legal_actions(self, p):
        acts = ["chaal", "pack"]
        if self.stake < self.cfg.get("stake_cap", 16) and p.chips >= self.cost(p, True):
            acts.append("raise")
        if not p.seen:
            acts.append("see")
        if self.turns >= 2:
            acts.append("show")
        return acts

    def apply(self, p, action):
        """Apply a validated action. Returns (text, finished)."""
        if action == "see":
            p.seen = True
            return f"{p.name} looks at their cards.", False
        self.turns += 1
        if action == "pack":
            p.packed = True
            return f"{p.name} packs.", True
        if action == "raise":
            amt = self._pay(p, self.cost(p, True))
            self.stake *= 2
            return f"{p.name} raises, puts in {amt}. Stake now {self.stake}.", self._limit()
        if action == "show":
            amt = self._pay(p, self.cost(p))
            return f"{p.name} pays {amt} and asks for a SHOW!", True
        amt = self._pay(p, self.cost(p))
        return f"{p.name} {'chaals' if p.seen else 'plays blind'} for {amt}.", self._limit()

    def _limit(self):
        return (self.pot >= self.cfg["pot_limit"] or self.turns >= self.cfg["max_turns"]
                or any(p.chips <= 0 for p in self.players))

    def winner(self):
        live = [p for p in self.players if not p.packed]
        if len(live) == 1:
            return live[0]
        a, b = live
        return a if evaluate(a.cards) >= evaluate(b.cards) else b


_ALL_SCORES = None


def strength(cards):
    """Share of all possible 3-card hands this hand beats (0-100). Computed once, then cached."""
    global _ALL_SCORES
    if _ALL_SCORES is None:
        from itertools import combinations
        deck = [r + s for r in RANKS for s in SUITS]
        _ALL_SCORES = sorted(evaluate(list(c)) for c in combinations(deck, 3))
    from bisect import bisect_left
    return round(100 * bisect_left(_ALL_SCORES, evaluate(cards)) / len(_ALL_SCORES))
