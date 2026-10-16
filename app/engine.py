"""Deterministic card rules. No network, browser, or LLM calls belong here."""

from __future__ import annotations

import random
import secrets
import time

from .domain import COLORS, Card, Player, Room, eligible_voters


class RuleError(ValueError):
    """An attempted move is invalid for the authoritative game state."""


def make_deck() -> list[Card]:
    cards: list[Card] = []
    for color in COLORS:
        cards.append(Card(secrets.token_urlsafe(8), color, "number", 0))
        for value in range(1, 10):
            cards.extend(Card(secrets.token_urlsafe(8), color, "number", value) for _ in range(2))
        for kind in ("skip", "reverse", "draw2"):
            cards.extend(Card(secrets.token_urlsafe(8), color, kind) for _ in range(2))
    cards.extend(Card(secrets.token_urlsafe(8), None, "wild") for _ in range(4))
    cards.extend(Card(secrets.token_urlsafe(8), None, "wild4") for _ in range(4))
    random.SystemRandom().shuffle(cards)
    return cards


def start_round(room: Room) -> None:
    if len(room.players) < 2:
        raise RuleError("At least two players are needed to start.")
    if room.round_number >= room.total_rounds:
        raise RuleError("This match is already complete.")
    room.round_number += 1
    room.status = "playing"
    room.direction = 1
    room.challenge = None
    room.round_winner_id = None
    room.drawn_card_id = None
    room.draw_pile = make_deck()
    for player in room.players:
        player.hand = [room.draw_pile.pop() for _ in range(7)]
    first = next((i for i, card in enumerate(room.draw_pile) if card.kind == "number"), 0)
    opening = room.draw_pile.pop(first)
    room.discard = [opening]
    room.current_color = opening.color or "red"
    first_player = next((p for p in room.players if p.connected), room.players[0])
    room.turn_player_id = first_player.id
    room.turn_deadline = time.time() + 30


def can_play(room: Room, player: Player, card: Card) -> bool:
    if not room.discard:
        return False
    top = room.discard[-1]
    if card.kind in {"wild", "wild4"}:
        if room.drawn_card_id and card.id != room.drawn_card_id:
            return False
        if card.kind == "wild4" and any(c.color == room.current_color for c in player.hand if c.id != card.id):
            return False
        return True
    return card.color == room.current_color or (
        card.kind == top.kind and card.kind != "number"
    ) or (card.kind == "number" and top.kind == "number" and card.value == top.value)


def play_card(room: Room, player_id: str, card_id: str, *, chosen_color: str | None = None) -> dict:
    player = _require_turn(room, player_id)
    card = next((item for item in player.hand if item.id == card_id), None)
    if not card:
        raise RuleError("That card is not in your hand.")
    if not can_play(room, player, card):
        raise RuleError("That card cannot be played on the current pile.")
    if card.kind in {"wild", "wild4"} and chosen_color not in COLORS:
        raise RuleError("Choose a valid color for a Wild card.")

    player.hand.remove(card)
    room.discard.append(card)
    room.drawn_card_id = None
    room.current_color = chosen_color if card.kind in {"wild", "wild4"} else card.color or room.current_color

    if not player.hand:
        _finish_round(room, player)
        return {"kind": card.kind, "round_finished": True}

    next_id = _next_player_id(room, player_id)
    if card.kind in {"wild", "wild4"}:
        room.turn_player_id = next_id
        room.turn_deadline = None
        return {"kind": card.kind, "round_finished": False, "challenge_target": next_id}
    if card.kind == "reverse":
        if len(room.players) == 2:
            room.turn_player_id = player_id
        else:
            room.direction *= -1
            room.turn_player_id = _next_player_id(room, player_id)
    elif card.kind == "skip":
        room.turn_player_id = _next_player_id(room, next_id)
    elif card.kind == "draw2":
        target = room.player(next_id)
        if target:
            _take_cards(room, target, 2)
        room.turn_player_id = _next_player_id(room, next_id)
    else:
        room.turn_player_id = next_id
    _set_turn_deadline(room)
    _skip_disconnected(room)
    return {"kind": card.kind, "round_finished": room.status == "round_over"}


def draw_card(room: Room, player_id: str) -> dict:
    player = _require_turn(room, player_id)
    if room.drawn_card_id:
        raise RuleError("You already drew this turn. Play that card if it fits, or end your turn.")
    card = _take_cards(room, player, 1)
    room.drawn_card_id = card.id if card and can_play(room, player, card) else None
    if not room.drawn_card_id:
        _advance_turn(room, player_id)
    else:
        room.turn_deadline = time.time() + 30
    return {"playable": bool(room.drawn_card_id), "round_finished": False}


def end_turn(room: Room, player_id: str) -> None:
    _require_turn(room, player_id)
    if not room.drawn_card_id:
        raise RuleError("Play a card or draw before ending your turn.")
    _advance_turn(room, player_id)


def resolve_challenge(room: Room) -> dict:
    challenge = room.challenge
    if not challenge:
        raise RuleError("There is no active challenge.")
    eligible = eligible_voters(room, challenge.player_id)
    yes_count = sum(1 for voter, vote in challenge.votes.items() if voter in eligible and vote)
    needed = len(eligible) // 2 + 1
    passed = bool(eligible) and not challenge.answer_timed_out and yes_count >= needed
    challenged = room.player(challenge.player_id)
    if not challenged:
        room.challenge = None
        return {"passed": False, "target": None}

    # Wild Draw Four always keeps its normal +4/skip; a failed challenge adds 2.
    is_wild4 = challenge.source_card == "wild4"
    if is_wild4:
        _take_cards(room, challenged, 4)
    if not passed:
        _take_cards(room, challenged, 2)

    room.challenge = None
    room.drawn_card_id = None
    if is_wild4 or not passed:
        room.turn_player_id = _next_player_id(room, challenged.id)
    else:
        room.turn_player_id = challenged.id
    _set_turn_deadline(room)
    _skip_disconnected(room)
    return {"passed": passed, "target": challenged.id, "penalty": (4 if is_wild4 else 0) + (0 if passed else 2)}


def apply_turn_timeout(room: Room) -> None:
    if room.status != "playing" or room.challenge or not room.turn_player_id:
        return
    player = room.player(room.turn_player_id)
    if not player:
        return
    if not player.connected:
        reconnect_deadline = (player.disconnected_at or time.time()) + 60
        if time.time() < reconnect_deadline:
            room.turn_deadline = reconnect_deadline
            return
        _advance_turn(room, player.id)
        return
    _take_cards(room, player, 1)
    room.drawn_card_id = None
    _advance_turn(room, player.id)


def _require_turn(room: Room, player_id: str) -> Player:
    if room.status != "playing":
        raise RuleError("The game is not accepting moves right now.")
    if room.challenge:
        raise RuleError("Finish the active challenge before playing a card.")
    if room.turn_player_id != player_id:
        raise RuleError("It is not your turn.")
    player = room.player(player_id)
    if not player or not player.connected:
        raise RuleError("Your player session is no longer active.")
    return player


def _next_player_id(room: Room, current_id: str) -> str:
    if not room.players:
        raise RuleError("No players remain in this room.")
    index = next((i for i, p in enumerate(room.players) if p.id == current_id), 0)
    return room.players[(index + room.direction) % len(room.players)].id


def _advance_turn(room: Room, current_id: str) -> None:
    room.turn_player_id = _next_player_id(room, current_id)
    room.drawn_card_id = None
    _set_turn_deadline(room)
    _skip_disconnected(room)


def _set_turn_deadline(room: Room) -> None:
    room.turn_deadline = time.time() + 30


def _skip_disconnected(room: Room) -> None:
    # A disconnected player is skipped while their 60-second reconnect window is over.
    for _ in room.players:
        player = room.player(room.turn_player_id or "")
        if player and (player.connected or player.disconnected_at is None or time.time() - player.disconnected_at < 60):
            return
        if room.turn_player_id:
            room.turn_player_id = _next_player_id(room, room.turn_player_id)
    _set_turn_deadline(room)


def _take_cards(room: Room, player: Player, count: int) -> list[Card]:
    taken = []
    for _ in range(count):
        if not room.draw_pile and len(room.discard) > 1:
            top = room.discard.pop()
            room.draw_pile = room.discard
            room.discard = [top]
            random.SystemRandom().shuffle(room.draw_pile)
        if not room.draw_pile:
            break
        card = room.draw_pile.pop()
        player.hand.append(card)
        taken.append(card)
    return taken


def _finish_round(room: Room, winner: Player) -> None:
    room.round_winner_id = winner.id
    room.status = "round_over"
    room.turn_deadline = None
    room.challenge = None
    if room.mode == "teams":
        scoring = [p for p in room.players if p.team != winner.team]
    else:
        scoring = [p for p in room.players if p.id != winner.id]
    points = sum(card.points for opponent in scoring for card in opponent.hand)
    if room.mode == "teams" and winner.team:
        room.team_scores[winner.team] += points
        room.team_round_wins[winner.team] += 1
    else:
        winner.score += points
        winner.round_wins += 1
    if room.round_number >= room.total_rounds:
        _finish_match(room)


def _finish_match(room: Room) -> None:
    if room.mode == "teams":
        leaders = max(room.team_scores.values())
        tied = [team for team, score in room.team_scores.items() if score == leaders]
        most_rounds = max(room.team_round_wins[team] for team in tied)
        tied = [team for team in tied if room.team_round_wins[team] == most_rounds]
        room.status = "complete"
        room.match_winner = tied[0] if len(tied) == 1 else "shared"
        return
    leaders = max(player.score for player in room.players)
    tied = [player for player in room.players if player.score == leaders]
    most_rounds = max(player.round_wins for player in tied)
    tied = [player for player in tied if player.round_wins == most_rounds]
    room.status = "complete"
    room.match_winner = tied[0].id if len(tied) == 1 else "shared"
