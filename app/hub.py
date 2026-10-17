"""One-process room coordinator and WebSocket protocol for the demo."""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
import unicodedata
from collections import defaultdict, deque
from urllib.parse import urlsplit

from fastapi import WebSocket, WebSocketDisconnect

from . import engine
from .ai_service import ChallengeAI
from .domain import (
    MAX_PLAYERS,
    MIN_PLAYERS,
    ROOM_ALPHABET,
    ROOM_CODE_LENGTH,
    Challenge,
    Player,
    Room,
    public_room_state,
)


VALID_THEMES = {"mixed", "movies", "food", "travel", "silly"}
VALID_MODES = {"free", "teams"}
VALID_ROUNDS = {3, 5, 7}
MESSAGE_LIMIT = 12_000


class Hub:
    def __init__(self, ai: ChallengeAI) -> None:
        self.ai = ai
        self.rooms: dict[str, Room] = {}
        self.connections: dict[str, WebSocket] = {}
        self.player_rooms: dict[str, str] = {}
        self.queues: dict[str, str] = {}
        self._rate: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._room_tasks: dict[str, set[asyncio.Task]] = defaultdict(set)
        self._recap_pending: set[str] = set()
        self._maintenance_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._maintenance_task = asyncio.create_task(self._maintenance_loop())

    async def close(self) -> None:
        if self._maintenance_task:
            self._maintenance_task.cancel()
        for tasks in self._room_tasks.values():
            for task in tasks:
                task.cancel()
        await asyncio.gather(
            *(task for tasks in self._room_tasks.values() for task in tasks),
            *([self._maintenance_task] if self._maintenance_task else []),
            return_exceptions=True,
        )

    async def _maintenance_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = time.time()
            for code, room in list(self.rooms.items()):
                if not room.players or now - room.activity_at > 3600:
                    for player in room.players:
                        self.player_rooms.pop(player.id, None)
                    self.queues.pop(room.mode, None)
                    self.rooms.pop(code, None)
                    for task in self._room_tasks.pop(code, set()):
                        task.cancel()
            for key, times in list(self._rate.items()):
                while times and time.monotonic() - times[0] > 60:
                    times.popleft()
                if not times:
                    self._rate.pop(key, None)

    async def serve(self, websocket: WebSocket, guest_id: str) -> None:
        origin = websocket.headers.get("origin", "")
        host = websocket.headers.get("host", "")
        if not origin or urlsplit(origin).netloc.casefold() != host.casefold():
            await websocket.close(code=1008, reason="Invalid origin")
            return
        await websocket.accept()
        await websocket.send_json({"type": "ready"})
        try:
            async for raw in websocket.iter_text():
                if len(raw.encode("utf-8")) > MESSAGE_LIMIT:
                    await websocket.send_json({"type": "error", "message": "That message is too large."})
                    await websocket.close(code=1009, reason="Message too large")
                    break
                if not self._within_rate_limit(websocket, guest_id):
                    await websocket.send_json({"type": "error", "message": "Too many actions. Please wait a moment."})
                    continue
                try:
                    message = json.loads(raw)
                    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
                        raise ValueError
                    await self.handle(websocket, guest_id, message)
                except engine.RuleError as exc:
                    await self._error(websocket, str(exc))
                except (json.JSONDecodeError, ValueError, TypeError):
                    await websocket.send_json({"type": "error", "message": "That action could not be understood."})
        except WebSocketDisconnect:
            pass
        finally:
            await self._disconnect(websocket, guest_id)

    def _within_rate_limit(self, websocket: WebSocket, guest_id: str) -> bool:
        now = time.monotonic()
        address = websocket.client.host if websocket.client else "unknown"
        for key, maximum in (((address, "shared"), 100), ((address, guest_id), 50)):
            times = self._rate[key]
            while times and now - times[0] > 10:
                times.popleft()
            if len(times) >= maximum:
                return False
        self._rate[(address, "shared")].append(now)
        self._rate[(address, guest_id)].append(now)
        return True

    async def handle(self, websocket: WebSocket, guest_id: str, message: dict) -> None:
        kind = message["type"]
        if kind == "enter":
            await self._enter(websocket, guest_id, message)
            return
        if kind == "heartbeat":
            room = self._current_room(guest_id)
            if room:
                room.activity_at = time.time()
            await websocket.send_json({"type": "pong"})
            return
        if kind == "reconnect":
            await self._reconnect(websocket, guest_id)
            return
        room = self._current_room(guest_id)
        if not room:
            await self._error(websocket, "Your room is no longer active. Return to the join page and create or join a room.")
            return
        if kind == "leave":
            await self._leave(room, guest_id, websocket)
            return
        if kind == "cancel_queue":
            await self._cancel_queue(room, guest_id)
            return
        if kind == "settings":
            self._set_settings(room, guest_id, message)
        elif kind == "start":
            self._start_private(room, guest_id)
        elif kind == "play":
            result = engine.play_card(
                room, guest_id, str(message.get("card_id", "")),
                chosen_color=message.get("color"),
            )
            if result.get("challenge_target"):
                self._begin_challenge(room, result["challenge_target"], result["kind"])
        elif kind == "draw":
            engine.draw_card(room, guest_id)
        elif kind == "end_turn":
            engine.end_turn(room, guest_id)
        elif kind == "answer":
            self._submit_answer(room, guest_id, message.get("answer", ""))
        elif kind == "vote":
            self._submit_vote(room, guest_id, message.get("yes"))
        elif kind == "next_round":
            self._next_round(room, guest_id)
        elif kind == "rematch_vote":
            self._rematch_vote(room, guest_id)
        else:
            raise engine.RuleError("That action is not available.")

        room.activity_at = time.time()
        await self._broadcast(room)
        if room.status == "playing" and room.turn_deadline and not room.challenge:
            self._schedule_turn(room)
        if room.status == "complete":
            self._schedule_recap(room)
        elif room.online and room.status == "round_over":
            self._task(room, self._auto_next_round(room.code, room.round_number))

    async def _enter(self, websocket: WebSocket, guest_id: str, data: dict) -> None:
        action = data.get("mode")
        if action == "reconnect":
            await self._reconnect(websocket, guest_id)
            return
        name = _clean_name(data.get("name"))
        if not name:
            await self._error(websocket, "Enter a display name up to 20 characters long.")
            return
        current = self._current_room(guest_id)
        current_player = current.player(guest_id) if current else None
        if current_player and (
            (action == "join" and _valid_code(data.get("code")) == current.code)
            or (action == "matchmaking" and current.status == "queue" and current.mode == data.get("play_mode", "free"))
        ):
            await self._restore_player(current, current_player, guest_id, name, websocket)
            return

        if action == "create":
            if len(self.rooms) >= 200:
                await self._error(websocket, "The demo is at capacity right now. Try again in a few minutes.")
                return
            mode = data.get("play_mode", "free")
            if mode not in VALID_MODES:
                await self._error(websocket, "Choose Free-for-all or Teams.")
                return
            await self._leave_for_new_entry(current, guest_id, websocket)
            code = self._new_code()
            player = Player(guest_id, name)
            room = Room(code, guest_id, [player], mode=mode, activity_at=time.time())
            self.rooms[code] = room
        elif action == "join":
            code = _valid_code(data.get("code"))
            if not code:
                await self._error(websocket, "Room codes use five letters or numbers, without O, 0, I, or 1.")
                return
            room = self.rooms.get(code)
            if not room:
                await self._error(websocket, "We couldn't find that room. Check the code and try again.")
                return
            if room.status != "lobby":
                await self._error(websocket, "That game has already started. Join a different room.")
                return
            if len(room.players) >= MAX_PLAYERS:
                await self._error(websocket, "That room is full (8 players maximum).")
                return
            existing_player = room.player(guest_id)
            if self._name_taken(room, name) and not existing_player:
                await self._error(websocket, "That display name is already being used in this room.")
                return
            await self._leave_for_new_entry(current if current is not room else None, guest_id, websocket)
            if existing_player:
                await self._restore_player(room, existing_player, guest_id, name, websocket)
                return
            room.players.append(Player(guest_id, name))
            if room.host_id is None:
                room.host_id = guest_id
        elif action == "matchmaking":
            mode = data.get("play_mode", "free")
            if mode not in VALID_MODES:
                await self._error(websocket, "Choose Free-for-all or Teams.")
                return
            room = self._queue_room(mode)
            if len(room.players) >= MAX_PLAYERS:
                await self._error(websocket, "That match is full. Please try again.")
                return
            existing_player = room.player(guest_id)
            if self._name_taken(room, name) and not existing_player:
                await self._error(websocket, "That display name is already being used in this match.")
                return
            await self._leave_for_new_entry(current if current is not room else None, guest_id, websocket)
            if existing_player:
                await self._restore_player(room, existing_player, guest_id, name, websocket)
                return
            room.players.append(Player(guest_id, name))
            self.player_rooms[guest_id] = room.code
            await self._replace_connection(websocket, guest_id)
            await self._broadcast(room)
            if len(room.players) >= MAX_PLAYERS:
                self.queues.pop(mode, None)
                self._launch_match(room)
                await self._broadcast(room)
            return
        else:
            await self._error(websocket, "Choose Create room, Join room, or Find online match.")
            return

        room.activity_at = time.time()
        self.player_rooms[guest_id] = room.code
        await self._replace_connection(websocket, guest_id)
        if room.online and len(room.players) == 1:
            self._task(room, self._queue_timeout(room.code, room.mode))
        await self._broadcast(room)

    async def _restore_player(
        self, room: Room, player: Player, guest_id: str, name: str, websocket: WebSocket
    ) -> None:
        await self._replace_connection(websocket, guest_id)
        player.name = name
        player.connected = True
        player.disconnected_at = None
        await self._broadcast(room)

    async def _leave_for_new_entry(self, room: Room | None, guest_id: str, websocket: WebSocket) -> None:
        """Detach a guest from their old room after an explicit new create/join request."""
        if room and room.player(guest_id):
            await self._replace_connection(websocket, guest_id)
            await self._leave(room, guest_id, websocket)

    def _queue_room(self, mode: str) -> Room:
        code = self.queues.get(mode)
        room = self.rooms.get(code or "")
        if room and room.status == "queue" and len(room.players) < MAX_PLAYERS:
            return room
        if len(self.rooms) >= 200:
            raise engine.RuleError("The demo is at capacity right now. Try again in a few minutes.")
        code = self._new_code()
        room = Room(
            code, None, [], mode=mode, theme=secrets.choice(["mixed", "movies", "food", "travel", "silly"]),
            total_rounds=3, online=True, status="queue", queue_deadline=time.time() + 20, activity_at=time.time(),
        )
        self.rooms[code] = room
        self.queues[mode] = code
        return room

    async def _queue_timeout(self, code: str, mode: str) -> None:
        await asyncio.sleep(20)
        room = self.rooms.get(code)
        if not room or room.status != "queue":
            return
        self.queues.pop(mode, None)
        if len(room.players) >= MIN_PLAYERS:
            self._launch_match(room)
            await self._broadcast(room)
        else:
            for player in list(room.players):
                self.player_rooms.pop(player.id, None)
                await self._send(player.id, {"type": "error", "message": "We couldn't find another player in 20 seconds. Try again or create a private room."})
            self.rooms.pop(code, None)

    def _launch_match(self, room: Room) -> None:
        if len(room.players) < MIN_PLAYERS:
            raise engine.RuleError("At least two players are needed to start.")
        room.status = "playing"
        room.queue_deadline = None
        self._assign_teams(room)
        engine.start_round(room)
        self._schedule_turn(room)

    def _start_private(self, room: Room, player_id: str) -> None:
        if room.online or room.status != "lobby":
            raise engine.RuleError("This room cannot be started right now.")
        if room.host_id != player_id:
            raise engine.RuleError("Only the host can start the game.")
        if not MIN_PLAYERS <= len(room.players) <= MAX_PLAYERS:
            raise engine.RuleError("A game needs 2 to 8 players.")
        self._assign_teams(room)
        engine.start_round(room)

    def _assign_teams(self, room: Room) -> None:
        for index, player in enumerate(room.players):
            player.team = ("A", "B")[index % 2] if room.mode == "teams" else None

    def _set_settings(self, room: Room, player_id: str, data: dict) -> None:
        if room.online or room.status != "lobby" or room.host_id != player_id:
            raise engine.RuleError("Only the host can change private-room settings before the game starts.")
        mode = data.get("mode")
        theme = data.get("theme")
        rounds = data.get("rounds")
        if mode not in VALID_MODES or theme not in VALID_THEMES or rounds not in VALID_ROUNDS:
            raise engine.RuleError("Choose a valid game mode, challenge theme, and match length.")
        room.mode, room.theme, room.total_rounds = mode, theme, rounds

    def _begin_challenge(self, room: Room, target_id: str, card_kind: str) -> None:
        prompt, source = self.ai.prompt_now(room.theme)
        target = room.player(target_id)
        reconnect_deadline = target.disconnected_at + 60 if target and not target.connected and target.disconnected_at else 0
        room.challenge = Challenge(
            player_id=target_id,
            prompt=prompt,
            source=source,
            deadline=max(time.time() + 15, reconnect_deadline),
            source_card=card_kind,
        )
        self._task(room, self._challenge_timeout(room.code, target_id, "answer", room.challenge.deadline))

    def _submit_answer(self, room: Room, player_id: str, raw: object) -> None:
        challenge = room.challenge
        if not challenge or challenge.phase != "answer" or challenge.player_id != player_id:
            raise engine.RuleError("You are not answering the active challenge.")
        answer = _clean_answer(raw)
        if not answer:
            raise engine.RuleError("Type or say an answer before submitting it.")
        challenge.answer = answer
        challenge.phase = "vote"
        challenge.deadline = time.time() + 10
        if not self._eligible_voters(room, challenge.player_id):
            engine.resolve_challenge(room)
            return
        self._task(room, self._challenge_timeout(room.code, player_id, "vote", challenge.deadline))

    def _submit_vote(self, room: Room, player_id: str, yes: object) -> None:
        challenge = room.challenge
        if not challenge or challenge.phase != "vote":
            raise engine.RuleError("Voting is not open right now.")
        if type(yes) is not bool:
            raise engine.RuleError("Choose yes or no.")
        eligible = self._eligible_voters(room, challenge.player_id)
        if player_id not in eligible:
            raise engine.RuleError("You are not eligible to vote on this challenge.")
        if player_id in challenge.votes:
            raise engine.RuleError("Your vote has already been counted.")
        challenge.votes[player_id] = yes
        if eligible and set(challenge.votes) >= eligible:
            engine.resolve_challenge(room)

    def _eligible_voters(self, room: Room, challenged_id: str) -> set[str]:
        from .domain import eligible_voters
        return eligible_voters(room, challenged_id)

    async def _challenge_timeout(self, code: str, player_id: str, phase: str, deadline: float) -> None:
        await asyncio.sleep(max(0.0, deadline - time.time()))
        room = self.rooms.get(code)
        challenge = room.challenge if room else None
        if not room or not challenge or challenge.player_id != player_id or challenge.phase != phase or challenge.deadline != deadline:
            return
        if phase == "answer":
            challenge.answer = "No answer submitted."
            challenge.answer_timed_out = True
            challenge.phase = "vote"
            challenge.deadline = time.time() + 10
            if not self._eligible_voters(room, player_id):
                engine.resolve_challenge(room)
            else:
                self._task(room, self._challenge_timeout(code, player_id, "vote", challenge.deadline))
        else:
            engine.resolve_challenge(room)
        await self._broadcast(room)
        if room.status == "playing" and room.turn_deadline:
            self._schedule_turn(room)

    def _next_round(self, room: Room, player_id: str) -> None:
        if room.online or room.status != "round_over" or player_id != room.host_id:
            raise engine.RuleError("Only the host can start the next round.")
        engine.start_round(room)

    async def _auto_next_round(self, code: str, round_number: int) -> None:
        await asyncio.sleep(5)
        room = self.rooms.get(code)
        if not room or not room.online or room.status != "round_over" or room.round_number != round_number:
            return
        engine.start_round(room)
        await self._broadcast(room)
        if room.turn_deadline:
            self._schedule_turn(room)

    def _rematch_vote(self, room: Room, player_id: str) -> None:
        if room.status != "complete" or not room.player(player_id):
            raise engine.RuleError("The rematch vote is not open.")
        room.rematch_votes.add(player_id)
        connected = {p.id for p in room.connected_players()}
        if len(connected) >= MIN_PLAYERS and connected <= room.rematch_votes:
            room.round_number = 0
            room.match_winner = None
            room.recap = ""
            room.rematch_votes.clear()
            room.team_scores = {"A": 0, "B": 0}
            room.team_round_wins = {"A": 0, "B": 0}
            for player in room.players:
                player.score = 0
                player.round_wins = 0
            self._assign_teams(room)
            engine.start_round(room)

    def _schedule_turn(self, room: Room) -> None:
        if not room.turn_deadline or not room.turn_player_id:
            return
        self._task(room, self._turn_timeout(room.code, room.turn_player_id, room.turn_deadline))

    async def _turn_timeout(self, code: str, player_id: str, deadline: float) -> None:
        await asyncio.sleep(max(0.0, deadline - time.time()))
        room = self.rooms.get(code)
        if not room or room.status != "playing" or room.challenge or room.turn_player_id != player_id or room.turn_deadline != deadline:
            return
        engine.apply_turn_timeout(room)
        await self._broadcast(room)
        if room.status == "playing" and room.turn_deadline:
            self._schedule_turn(room)

    def _schedule_recap(self, room: Room) -> None:
        if room.recap or room.code in self._recap_pending:
            return
        self._recap_pending.add(room.code)
        self._task(room, self._create_recap(room.code))

    async def _create_recap(self, code: str) -> None:
        room = self.rooms.get(code)
        if not room or room.status != "complete":
            return
        if room.mode == "teams":
            scores = [{"label": f"Team {team}", "score": score} for team, score in room.team_scores.items()]
        else:
            scores = [{"label": f"Player {index + 1}", "score": player.score} for index, player in enumerate(room.players)]
        try:
            recap = await self.ai.recap({"rounds": room.round_number, "scores": scores})
        except Exception:
            recap = "A lively match across the table. Ready for a rematch?"
        finally:
            self._recap_pending.discard(code)
        if self.rooms.get(code) is room and room.status == "complete":
            room.recap = recap
            await self._broadcast(room)

    async def _reconnect(self, websocket: WebSocket, guest_id: str) -> None:
        room = self._current_room(guest_id)
        player = room.player(guest_id) if room else None
        if not room or not player or (player.disconnected_at and time.time() - player.disconnected_at > 60):
            self.player_rooms.pop(guest_id, None)
            await self._error(websocket, "Your previous game is no longer available. Return to the join page to start again.")
            return
        await self._replace_connection(websocket, guest_id)
        player.connected = True
        player.disconnected_at = None
        if room.turn_player_id == guest_id and room.status == "playing" and not room.challenge:
            room.turn_deadline = time.time() + 30
            self._schedule_turn(room)
        await self._broadcast(room)

    async def _replace_connection(self, websocket: WebSocket, player_id: str) -> None:
        previous = self.connections.get(player_id)
        self.connections[player_id] = websocket
        if previous and previous is not websocket:
            try:
                await previous.close(code=4001, reason="This player reconnected in another tab")
            except Exception:
                pass

    async def _disconnect(self, websocket: WebSocket, player_id: str) -> None:
        if self.connections.get(player_id) is not websocket:
            return
        self.connections.pop(player_id, None)
        room = self._current_room(player_id)
        if not room:
            return
        player = room.player(player_id)
        if not player:
            return
        player.connected = False
        player.disconnected_at = time.time()
        if room.host_id == player_id:
            room.host_id = next((p.id for p in room.players if p.connected), None)
        if room.status == "queue":
            await self._cancel_queue(room, player_id, broadcast=False)
            return
        if room.status == "lobby":
            self._task(room, self._expire_lobby_player(room.code, player_id, player.disconnected_at))
        if room.turn_player_id == player_id and room.status == "playing" and not room.challenge:
            room.turn_deadline = player.disconnected_at + 60
            self._schedule_turn(room)
        elif room.challenge and room.challenge.player_id == player_id and room.challenge.phase == "answer":
            room.challenge.deadline = player.disconnected_at + 60
            self._task(room, self._challenge_timeout(room.code, player_id, "answer", room.challenge.deadline))
        await self._broadcast(room)

    async def _expire_lobby_player(self, code: str, player_id: str, disconnected_at: float) -> None:
        await asyncio.sleep(60)
        room = self.rooms.get(code)
        player = room.player(player_id) if room else None
        if not room or room.status != "lobby" or not player or player.connected or player.disconnected_at != disconnected_at:
            return
        room.players.remove(player)
        self.player_rooms.pop(player_id, None)
        if room.host_id == player_id:
            room.host_id = next((p.id for p in room.players if p.connected), None)
        if not room.players:
            self.rooms.pop(code, None)
            return
        await self._broadcast(room)

    async def _leave(self, room: Room, player_id: str, websocket: WebSocket) -> None:
        explicitly_leaving_results = room.status == "complete"
        if room.status in {"lobby", "queue"}:
            player = room.player(player_id)
            if player:
                room.players.remove(player)
            self.player_rooms.pop(player_id, None)
            self.connections.pop(player_id, None)
            if room.host_id == player_id:
                room.host_id = next((p.id for p in room.players if p.connected), None)
            if room.status == "queue" and room.mode in self.queues and not room.players:
                self.queues.pop(room.mode, None)
                self.rooms.pop(room.code, None)
            elif not room.players:
                self.rooms.pop(room.code, None)
        else:
            player = room.player(player_id)
            if player:
                player.connected = False
                player.disconnected_at = time.time()
            if room.host_id == player_id:
                room.host_id = next((p.id for p in room.players if p.connected), None)
            if self.connections.get(player_id) is websocket:
                self.connections.pop(player_id, None)
            if player and room.turn_player_id == player_id and room.status == "playing" and not room.challenge:
                room.turn_deadline = player.disconnected_at + 60
                self._schedule_turn(room)
            elif player and room.challenge and room.challenge.player_id == player_id and room.challenge.phase == "answer":
                room.challenge.deadline = player.disconnected_at + 60
                self._task(room, self._challenge_timeout(room.code, player_id, "answer", room.challenge.deadline))
        if explicitly_leaving_results:
            self.player_rooms.pop(player_id, None)
        self.player_rooms.pop(player_id, None)
        try:
            await websocket.send_json({"type": "left"})
        except Exception:
            pass
        await self._broadcast(room)

    async def _cancel_queue(self, room: Room, player_id: str, *, broadcast: bool = True) -> None:
        player = room.player(player_id)
        if player:
            room.players.remove(player)
        self.player_rooms.pop(player_id, None)
        if not room.players:
            self.queues.pop(room.mode, None)
            self.rooms.pop(room.code, None)
            return
        if broadcast:
            await self._broadcast(room)

    def _new_code(self) -> str:
        for _ in range(100):
            code = "".join(secrets.choice(ROOM_ALPHABET) for _ in range(ROOM_CODE_LENGTH))
            if code not in self.rooms:
                return code
        raise RuntimeError("Could not reserve a room code.")

    def _current_room(self, player_id: str) -> Room | None:
        code = self.player_rooms.get(player_id)
        return self.rooms.get(code or "")

    @staticmethod
    def _name_taken(room: Room, name: str) -> bool:
        return any(player.name.casefold() == name.casefold() for player in room.players)

    async def _broadcast(self, room: Room) -> None:
        now = time.time()
        peers = []
        for player in room.players:
            socket = self.connections.get(player.id)
            if socket:
                payload = {"type": "state", "state": public_room_state(room, player.id, now=now)}
                peers.append(self._send_safely(socket, payload))
        if peers:
            await asyncio.gather(*peers)

    async def _send(self, player_id: str, payload: dict) -> None:
        socket = self.connections.get(player_id)
        if socket:
            await self._send_safely(socket, payload)

    @staticmethod
    async def _send_safely(websocket: WebSocket, payload: dict) -> None:
        try:
            await asyncio.wait_for(websocket.send_json(payload), timeout=1.5)
        except Exception:
            return

    @staticmethod
    async def _error(websocket: WebSocket, message: str) -> None:
        await websocket.send_json({"type": "error", "message": message})

    def _task(self, room: Room, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._room_tasks[room.code].add(task)
        task.add_done_callback(lambda finished, code=room.code: self._room_tasks[code].discard(finished))


def _clean_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    name = unicodedata.normalize("NFC", value).strip()
    if not name or len(name) > 20 or any(unicodedata.category(char).startswith("C") for char in name):
        return ""
    return name


def _clean_answer(value: object) -> str:
    if not isinstance(value, str):
        return ""
    answer = unicodedata.normalize("NFC", value).strip()
    if not answer or len(answer) > 240 or any(unicodedata.category(char) == "Cc" for char in answer):
        return ""
    return answer


def _valid_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    if re.fullmatch(f"[{re.escape(ROOM_ALPHABET)}]{{{ROOM_CODE_LENGTH}}}", code):
        return code
    return None
