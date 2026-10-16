"""Server-owned game state and small domain types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


COLORS = ("red", "blue", "yellow", "green")
ROOM_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
ROOM_CODE_LENGTH = 5
MAX_PLAYERS = 8
MIN_PLAYERS = 2


@dataclass(slots=True)
class Card:
    id: str
    color: str | None
    kind: str  # number, skip, reverse, draw2, wild, wild4
    value: int | None = None

    @property
    def points(self) -> int:
        if self.kind == "number":
            return self.value or 0
        if self.kind in {"skip", "reverse", "draw2"}:
            return 20
        return 50


@dataclass(slots=True)
class Player:
    id: str
    name: str
    connected: bool = True
    hand: list[Card] = field(default_factory=list)
    team: str | None = None
    score: int = 0
    round_wins: int = 0
    disconnected_at: float | None = None


@dataclass(slots=True)
class Challenge:
    player_id: str
    prompt: str
    source: str = "builtin"
    phase: str = "answer"
    answer: str = ""
    answer_timed_out: bool = False
    deadline: float = 0.0
    votes: dict[str, bool] = field(default_factory=dict)
    source_card: str = "wild"


@dataclass(slots=True)
class Room:
    code: str
    host_id: str | None
    players: list[Player]
    mode: str = "free"
    theme: str = "mixed"
    total_rounds: int = 5
    online: bool = False
    status: str = "lobby"
    queue_deadline: float | None = None
    round_number: int = 0
    direction: int = 1
    current_color: str = "red"
    discard: list[Card] = field(default_factory=list)
    draw_pile: list[Card] = field(default_factory=list)
    turn_player_id: str | None = None
    turn_deadline: float | None = None
    drawn_card_id: str | None = None
    challenge: Challenge | None = None
    round_winner_id: str | None = None
    match_winner: str | None = None
    recap: str = ""
    rematch_votes: set[str] = field(default_factory=set)
    team_scores: dict[str, int] = field(default_factory=lambda: {"A": 0, "B": 0})
    team_round_wins: dict[str, int] = field(default_factory=lambda: {"A": 0, "B": 0})
    activity_at: float = 0.0

    def player(self, player_id: str) -> Player | None:
        return next((player for player in self.players if player.id == player_id), None)

    def connected_players(self) -> list[Player]:
        return [player for player in self.players if player.connected]


def public_room_state(room: Room, viewer_id: str, *, now: float) -> dict[str, Any]:
    """Return a privacy-filtered snapshot; only the viewer's hand is disclosed."""
    viewer = room.player(viewer_id)
    top = room.discard[-1] if room.discard else None
    players = []
    for player in room.players:
        players.append({
            "id": player.id,
            "name": player.name,
            "connected": player.connected,
            "count": len(player.hand),
            "team": player.team,
            "score": player.score,
            "round_wins": player.round_wins,
            "is_host": player.id == room.host_id,
        })
    challenge = None
    if room.challenge:
        eligible = _eligible_voters(room, room.challenge.player_id)
        challenge = {
            "player_id": room.challenge.player_id,
            "prompt": room.challenge.prompt,
            "source": room.challenge.source,
            "phase": room.challenge.phase,
            "answer": room.challenge.answer,
            "deadline": room.challenge.deadline,
            "votes_cast": len(room.challenge.votes),
            "votes_needed": len(eligible) // 2 + 1,
            "eligible_voter": viewer_id in eligible,
            "has_voted": viewer_id in room.challenge.votes,
        }
    return {
        "code": room.code,
        "mode": room.mode,
        "theme": room.theme,
        "total_rounds": room.total_rounds,
        "online": room.online,
        "status": room.status,
        "queue_deadline": room.queue_deadline,
        "round_number": room.round_number,
        "players": players,
        "viewer_id": viewer_id,
        "is_host": viewer_id == room.host_id,
        "hand": [card_json(card) for card in viewer.hand] if viewer else [],
        "discard": card_json(top) if top else None,
        "draw_count": len(room.draw_pile),
        "current_color": room.current_color,
        "direction": room.direction,
        "turn_player_id": room.turn_player_id,
        "turn_deadline": room.turn_deadline,
        "drawn_card_id": room.drawn_card_id,
        "challenge": challenge,
        "round_winner_id": room.round_winner_id,
        "match_winner": room.match_winner,
        "recap": room.recap,
        "rematch_votes": list(room.rematch_votes),
        "team_scores": room.team_scores,
        "team_round_wins": room.team_round_wins,
        "now": now,
    }


def card_json(card: Card) -> dict[str, Any]:
    return {"id": card.id, "color": card.color, "kind": card.kind, "value": card.value}


def _eligible_voters(room: Room, challenged_id: str) -> set[str]:
    challenged = room.player(challenged_id)
    if not challenged:
        return set()
    voters = {p.id for p in room.players if p.connected and p.id != challenged_id}
    if room.mode == "teams" and challenged.team:
        voters = {
            p.id for p in room.players
            if p.connected and p.id != challenged_id and p.team != challenged.team
        }
    return voters


def eligible_voters(room: Room, challenged_id: str) -> set[str]:
    return _eligible_voters(room, challenged_id)
