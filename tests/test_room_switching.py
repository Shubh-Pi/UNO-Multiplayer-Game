import unittest
from unittest.mock import AsyncMock

from app.domain import Player, Room
from app.hub import Hub


class FakeWebSocket:
    def __init__(self):
        self.messages = []
        self.closed = []

    async def send_json(self, payload):
        self.messages.append(payload)

    async def close(self, *, code=1000, reason=None):
        self.closed.append((code, reason))


def run_immediate(coroutine):
    """Run Hub test coroutines that contain no event-loop scheduling points."""
    try:
        coroutine.send(None)
    except StopIteration:
        return
    raise AssertionError("test coroutine unexpectedly scheduled asynchronous work")


class RoomSwitchingTests(unittest.TestCase):
    def test_create_after_disconnect_starts_a_new_room(self):
        async def scenario():
            hub = Hub(ai=None)
            hub._broadcast = AsyncMock()
            guest_id = "guest-12345678901234567890"
            old_code = "ABCDE"
            old_player = Player(guest_id, "Balgu", connected=False)
            old_player.disconnected_at = 1
            old_room = Room(old_code, guest_id, [old_player], status="playing")
            hub.rooms[old_code] = old_room
            hub.player_rooms[guest_id] = old_code

            websocket = FakeWebSocket()
            await hub._enter(websocket, guest_id, {
                "mode": "create",
                "name": "Balgu",
                "play_mode": "free",
            })

            new_code = hub.player_rooms[guest_id]
            self.assertNotEqual(new_code, old_code)
            self.assertIn(new_code, hub.rooms)
            self.assertFalse(old_player.connected)
            self.assertGreater(old_player.disconnected_at, 1)
            self.assertIsNotNone(hub.rooms[new_code].player(guest_id))

        run_immediate(scenario())

    def test_join_after_disconnect_switches_to_requested_room(self):
        async def scenario():
            hub = Hub(ai=None)
            hub._broadcast = AsyncMock()
            guest_id = "guest-12345678901234567890"
            old_player = Player(guest_id, "Balgu", connected=False)
            old_player.disconnected_at = 1
            old_room = Room("ABCDE", guest_id, [old_player], status="playing")
            target_room = Room("JKLMN", "other-guest", [Player("other-guest", "Jordan")])
            hub.rooms[old_room.code] = old_room
            hub.rooms[target_room.code] = target_room
            hub.player_rooms[guest_id] = old_room.code
            websocket = FakeWebSocket()

            await hub._enter(websocket, guest_id, {
                "mode": "join",
                "name": "Balgu",
                "code": target_room.code,
            })

            self.assertEqual(hub.player_rooms[guest_id], target_room.code)
            self.assertIsNotNone(target_room.player(guest_id))
            self.assertGreater(old_player.disconnected_at, 1)

        run_immediate(scenario())

    def test_explicit_reconnect_still_returns_to_existing_room(self):
        async def scenario():
            hub = Hub(ai=None)
            hub._broadcast = AsyncMock()
            guest_id = "guest-12345678901234567890"
            room = Room("ABCDE", guest_id, [Player(guest_id, "Balgu", connected=False)])
            hub.rooms[room.code] = room
            hub.player_rooms[guest_id] = room.code
            websocket = FakeWebSocket()

            await hub._enter(websocket, guest_id, {"mode": "reconnect", "name": "Balgu"})

            self.assertEqual(hub.player_rooms[guest_id], room.code)
            self.assertTrue(room.player(guest_id).connected)
            hub._broadcast.assert_awaited_once_with(room)

        run_immediate(scenario())


if __name__ == "__main__":
    unittest.main()
