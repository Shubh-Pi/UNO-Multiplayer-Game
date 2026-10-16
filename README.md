# UNO Multiplayer Game

A mobile-first, real-time multiplayer party card game for 2–8 people. This is a portfolio/demo project, not a revenue product. There are no accounts, purchases, or database-backed profiles.

## Stack and hosting

- **Python 3.13** on Render Free for the public demo (`.python-version`, `render.yaml`). The app also runs on Python 3.11+ for local development.
- **FastAPI + WebSockets** for authoritative game rules and immediate room updates.
- **Server-rendered HTML**, CSS, and a small amount of browser JavaScript. No React or TypeScript.
- One Uvicorn worker and in-memory room state keep turns fast and deterministic. Render is configured as one free instance; rooms do not survive a service restart or deploy.

WebSockets fit the game because a move or vote should reach every player as soon as it is accepted, without polling. The server validates each move and sends each player a private snapshot: the public table plus that player's own hand. No LLM request is made while validating a move.

## Run locally

From this folder in PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:SESSION_SECRET = "local-demo-change-this-to-a-long-random-value"
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

If Python 3.13 is not installed yet, use an installed Python 3.11+ version for local development, such as `py -3.11 -m venv .venv`. Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Try it with multiple players

The simulator opens 2–8 independent guest sessions, each with its own signed cookie and WebSocket. Start the server, open a second PowerShell window in this folder, activate `.venv`, then run:

```powershell
python scripts/simulate_players.py --players 4 --mode teams
```

It prints the room code and keeps all simulated players connected. You can also create a room in a browser and fill it with simulated guests:

```powershell
python scripts/simulate_players.py --players 3 --room K7R4P
```

To try the whole UI with real browser sessions, open the app in separate browser profiles (for example, Chrome and Edge, or two separately created Chrome profiles). Separate tabs in one profile share the same guest cookie and represent one player. Use one profile to create the room and the others to enter the displayed code.

## Publish the demo on Render Free

1. Put this `color-club` folder in a GitHub repository and push it to your account.
2. In Render, create a **Blueprint** from that repository and select `render.yaml`. The blueprint creates a single Free web service, generates `SESSION_SECRET`, enables secure cookies, and uses one Uvicorn worker.
3. Pick the Render region closest to the people you expect to test with. The user group location is still unknown; a single region cannot give distant players identical network latency.
4. Wait for `/health` to report healthy, then open the `onrender.com` URL on separate phones or browser profiles. Share the private-room link or code.

Render Free can sleep after inactivity and may take about a minute to wake. A restart or deployment clears active rooms because the demo keeps state in memory. The client reconnects automatically when the service is available again; if the instance restarted, it shows a clear message and players can create/join another room. WebSocket heartbeats are sent while a room is open.

## Game flow and rules

- The room creator receives a fresh five-character code using an alphabet without O/0 or I/1. Friends enter only a display name and room code. Private-room hosts choose Free-for-all or Teams, a challenge theme, and 3, 5, or 7 rounds.
- Online matches are server-created, use the chosen mode, a server-selected theme, and three rounds. The server waits up to 20 seconds to gather 2–8 players, then starts with at least two.
- Each player starts a round with seven cards. Play a card matching the current color or symbol, or draw one. A drawn playable card can be played immediately; otherwise the turn ends. Turns last 30 seconds; a timeout draws one card and advances the turn.
- Skip, Reverse, Draw Two, Wild, and Wild Draw Four use familiar card effects. With two players, Reverse skips the other player. A Wild asks the next player to answer a themed challenge in 15 seconds, then the eligible players vote for 10 seconds. A majority “yes” avoids the challenge penalty; otherwise, the challenged player draws two and loses their turn.
- **Wild Draw Four clarification:** it also starts the challenge. Its standard draw-four and skipped turn still apply; a failed challenge adds two more cards.
- Teams split evenly by join order, with one extra player on Team A for odd counts. The first teammate to empty their hand wins the round. Free-for-all round points equal the cards left in all opponents' hands; the winning team gets the value of cards left by the opposing team. Number cards score their number, action cards 20, and Wild cards 50. Match ties break by round wins, then are shared.
- The server generates prompts from an optional LLM-backed cache with built-in prompts available immediately. AI never validates moves, answers, penalties, or scores. At match end, it can write a short stats-based recap. Players can vote for a rematch.

## Optional AI settings

Leave these unset to use the built-in prompt and recap fallbacks. To use a remote **OpenAI-compatible Chat Completions API**, configure these environment variables in Render (or in your local shell):

```text
AI_BASE_URL=https://your-provider.example/v1
AI_API_KEY=your-secret-key
AI_MODEL=your-model-name
```

The endpoint and key are optional; the game keeps working without them. AI prompts are generated asynchronously in the background and cached, so they do not delay moves. Provider use may have a separate cost or quota; check its terms before enabling it. The app does not include a vector database or RAG because no approved retrieval corpus was selected.

Voice entry is an optional browser enhancement. The microphone is requested only after a player taps **Answer by voice**. Speech is handled by the browser's speech service; UNO Multiplayer Game receives only the transcript. Typed answers work on every supported browser.

## Security and demo limits

- The server owns hands, turn order, timers, penalties, team membership, scoring, room codes, and match results.
- Guest identity is a random, signed `HttpOnly`, `SameSite=Lax` cookie. Render enables `Secure` cookies; local HTTP disables that flag.
- The WebSocket checks its `Origin`, validates message size, rate-limits actions by source IP, validates room names/settings/moves, and rejects attempts to join an active game.
- Public snapshots never include other players' cards. User strings are escaped in the UI, and the app sets a restrictive Content Security Policy and security headers.
- Disconnects have a 60-second reconnect window. If a player does not return, their turns are skipped. A private-room host disconnect transfers host controls to the next connected player.
- The single-process in-memory design is for a small demo. It is not durable, horizontally scalable, or an uptime guarantee. Do not run multiple workers/instances without adding shared state and coordination.

## Project map

- `app/engine.py` — deterministic card rules and scoring.
- `app/hub.py` — room creation, matchmaking, WebSocket events, timers, reconnects, and server-side authorization.
- `app/ai_service.py` — optional LLM cache plus immediate built-in prompt/recap fallbacks.
- `app/templates/index.html`, `app/static/` — accessible, responsive interface and browser client.
- `scripts/simulate_players.py` — multi-guest local simulator.
- `render.yaml` — Render Free Blueprint deployment settings.
