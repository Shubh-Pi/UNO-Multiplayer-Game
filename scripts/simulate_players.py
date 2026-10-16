"""Open 2-8 independent guest sessions against a local Color Club service."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import time
import urllib.request

from websockets.sync.client import connect


def get_guest_cookie(base_url: str) -> str:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    with opener.open(base_url, timeout=8) as response:
        response.read()
    return "; ".join(f"{cookie.name}={cookie.value}" for cookie in jar)


def open_player(base_url: str, name: str, mode: str, room_code: str | None):
    cookie = get_guest_cookie(base_url)
    ws_url = base_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/ws"
    socket = connect(
        ws_url,
        additional_headers={"Cookie": cookie, "Origin": base_url},
        open_timeout=8,
        max_size=12_000,
        ping_interval=20,
        ping_timeout=20,
    )
    ready = json.loads(socket.recv(timeout=5))
    if ready.get("type") != "ready":
        socket.close()
        raise RuntimeError(f"Unexpected server response for {name}.")
    if room_code:
        action = {"type": "enter", "mode": "join", "code": room_code, "name": name}
    elif not room_code and not socket_holder:
        action = {"type": "enter", "mode": "create", "play_mode": mode, "name": name}
    else:
        action = {"type": "enter", "mode": "join", "code": socket_holder[0], "name": name}
    socket.send(json.dumps(action))
    packet = json.loads(socket.recv(timeout=8))
    if packet.get("type") == "error":
        socket.close()
        raise RuntimeError(packet.get("message", "Could not join the room."))
    if packet.get("type") != "state":
        socket.close()
        raise RuntimeError("The server did not return a room state.")
    if not room_code and not socket_holder:
        socket_holder.append(packet["state"]["code"])
    return socket, packet["state"]


socket_holder: list[str] = []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="Base URL of the running app")
    parser.add_argument("--players", type=int, default=4, help="Number of simulated players (2 to 8)")
    parser.add_argument("--mode", choices=("free", "teams"), default="free", help="Mode when creating a new room")
    parser.add_argument("--room", help="Join an existing private room code instead of creating one")
    args = parser.parse_args()
    if not 2 <= args.players <= 8:
        parser.error("--players must be between 2 and 8")

    base_url = args.url.rstrip("/")
    sockets = []
    latest = None
    try:
        for index in range(args.players):
            name = f"Demo Player {index + 1}"
            socket, latest = open_player(base_url, name, args.mode, args.room)
            sockets.append(socket)
            print(f"Connected {name}.")
            time.sleep(0.05)
        code = args.room or socket_holder[0]
        print(f"\nRoom: {code}")
        print(f"Players: {', '.join(player['name'] for player in latest['players'])}")
        print("Each simulated player has a separate signed guest cookie and WebSocket.")
        print("Keep this terminal open while you test from a browser. Press Ctrl+C to disconnect them.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nClosing simulated players…")
    except Exception as exc:
        print(f"Simulation failed: {exc}", file=sys.stderr)
        return 1
    finally:
        for socket in sockets:
            socket.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
