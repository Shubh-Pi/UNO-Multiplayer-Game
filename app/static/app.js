"use strict";

const app = document.getElementById("app");
const toast = document.getElementById("toast");
const connectionBadge = document.getElementById("connection");
const modalRoot = document.getElementById("modal-root");
const COLORS = ["red", "blue", "yellow", "green"];
const COLOR_NAMES = { red: "Red", blue: "Blue", yellow: "Yellow", green: "Green" };
const CARD_SYMBOLS = { skip: "⊘", reverse: "↻", draw2: "+2", wild: "✦", wild4: "+4" };
let socket = null;
let state = null;
let connected = false;
let reconnectAttempt = 0;
let reconnectTimer = null;
let pendingCardId = null;
let toastTimer = null;
let serverClockOffset = 0;
let requestedWildCard = null;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 3600);
}

function updateConnection() {
  const show = Boolean(state);
  connectionBadge.hidden = !show;
  connectionBadge.classList.toggle("online", connected);
  connectionBadge.classList.toggle("offline", !connected);
  connectionBadge.querySelector("span").textContent = connected ? "Connected" : "Reconnecting";
}

function connect() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return;
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  socket = new WebSocket(`${protocol}//${location.host}/ws`);
  socket.addEventListener("open", () => {
    connected = true;
    reconnectAttempt = 0;
    updateConnection();
  });
  socket.addEventListener("message", (event) => {
    let packet;
    try { packet = JSON.parse(event.data); } catch { return; }
    if (packet.type === "ready") {
      if (sessionStorage.getItem("cc_room")) send("reconnect");
      return;
    }
    if (packet.type === "pong") return;
    if (packet.type === "error") {
      pendingCardId = null;
      showToast(packet.message || "That action could not be completed.");
      if (/previous game|room is no longer active/i.test(packet.message || "")) {
        sessionStorage.removeItem("cc_room");
        state = null;
      } else if (/couldn't find another player/i.test(packet.message || "")) {
        sessionStorage.removeItem("cc_room");
        state = null;
      }
      render();
      return;
    }
    if (packet.type === "state" && packet.state) {
      state = packet.state;
      serverClockOffset = state.now - Date.now() / 1000;
      sessionStorage.setItem("cc_room", state.code);
      if (!sessionStorage.getItem("cc_name")) {
        const me = state.players.find((player) => player.id === state.viewer_id);
        if (me) sessionStorage.setItem("cc_name", me.name);
      }
      if (pendingCardId && !state.hand.some((card) => card.id === pendingCardId)) pendingCardId = null;
      render();
      return;
    }
  });
  socket.addEventListener("close", (event) => {
    connected = false;
    updateConnection();
    if (event.code === 4001) {
      showToast("This guest is already connected in another tab. Use a separate browser profile for another player.");
      return;
    }
    reconnectAttempt += 1;
    const delay = Math.min(9000, 700 * (2 ** Math.min(reconnectAttempt, 4)));
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, delay);
  });
  socket.addEventListener("error", () => {
    connected = false;
    updateConnection();
  });
}

function send(type, payload = {}) {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    showToast("Reconnecting to the game. Try that again in a moment.");
    return false;
  }
  socket.send(JSON.stringify({ type, ...payload }));
  return true;
}

function render() {
  updateConnection();
  app.setAttribute("aria-busy", "false");
  if (!state) {
    document.body.dataset.view = "home";
    app.innerHTML = renderHome();
  } else if (state.status === "queue") {
    document.body.dataset.view = "queue";
    app.innerHTML = renderQueue();
  } else if (state.status === "lobby") {
    document.body.dataset.view = "lobby";
    app.innerHTML = renderLobby();
  } else if (["playing", "round_over"].includes(state.status)) {
    document.body.dataset.view = "game";
    app.innerHTML = renderGame();
  } else if (state.status === "complete") {
    document.body.dataset.view = "results";
    app.innerHTML = renderResults();
  } else {
    state = null;
    sessionStorage.removeItem("cc_room");
    document.body.dataset.view = "home";
    app.innerHTML = renderHome();
  }
  updateTimers();
}

function renderHome() {
  const name = escapeHtml(sessionStorage.getItem("cc_name") || "");
  const savedMode = sessionStorage.getItem("cc_mode") === "teams" ? "teams" : "free";
  const inviteCode = escapeHtml(new URLSearchParams(location.search).get("room") || "");
  return `<section class="screen home-screen">
    <div class="hero-copy">
      <p class="eyebrow">COLOR CLUB · PLAY TOGETHER</p>
      <h1>Bring your best<br><span class="accent">hand.</span></h1>
      <p>Quick card rounds, clever challenges, and a table full of friends. Grab a seat and let the good moves begin.</p>
      <div class="card-fan" aria-label="Four colorful game cards">
        <div class="playing-card red tilt-1"><span class="corner">7</span><span class="middle">7</span><span class="corner">7</span></div>
        <div class="playing-card blue tilt-2"><span class="corner">↻</span><span class="middle">↻</span><span class="corner">↻</span></div>
        <div class="playing-card yellow tilt-3"><span class="corner">4</span><span class="middle">4</span><span class="corner">4</span></div>
        <div class="playing-card green tilt-4"><span class="corner">+2</span><span class="middle">+2</span><span class="corner">+2</span></div>
      </div>
      <div class="hero-foot"><span class="avatar-stack" aria-hidden="true"><span>M</span><span>J</span><span>A</span><span>R</span></span><span>2–8 players · no accounts · all fun</span></div>
    </div>
    <section class="panel entry-panel" aria-labelledby="join-title">
      <p class="eyebrow">YOUR SEAT IS WAITING</p>
      <h2 id="join-title">Let’s play.</h2>
      <div class="field"><label for="display-name">Your display name</label><input class="input" id="display-name" maxlength="20" autocomplete="nickname" placeholder="What should we call you?" value="${name}"></div>
      <div class="field-label">Choose your game mode</div>
      <div class="choice-row" role="group" aria-label="Game mode">
        <button class="choice ${savedMode === "free" ? "active" : ""}" type="button" data-action="mode" data-mode="free" aria-pressed="${savedMode === "free"}">Free-for-all</button>
        <button class="choice ${savedMode === "teams" ? "active" : ""}" type="button" data-action="mode" data-mode="teams" aria-pressed="${savedMode === "teams"}">Teams</button>
      </div>
      <button class="btn btn-primary btn-wide" type="button" data-action="matchmaking">Find an online match <span aria-hidden="true">→</span></button>
      <div class="divider">OR PLAY WITH FRIENDS</div>
      <button class="btn btn-dark btn-wide" type="button" data-action="create">Create a private room</button>
      <div class="field join-code-field"><label for="room-code">Have a room code?</label><input class="input" id="room-code" maxlength="5" autocomplete="off" autocapitalize="characters" placeholder="e.g. K7R4P" value="${inviteCode}"></div>
      <button class="btn btn-light btn-wide" type="button" data-action="join">Join private room</button>
      <p class="form-note">No sign-up. Your name is all you need.</p>
    </section>
  </section>`;
}

function renderQueue() {
  const remaining = state.queue_deadline ? Math.max(0, Math.ceil(state.queue_deadline - (Date.now() / 1000 + serverClockOffset))) : 20;
  return `<section class="screen panel status-card">
    <div class="status-icon pulse" aria-hidden="true">♧</div>
    <p class="eyebrow">ONLINE MATCH · ${state.mode === "teams" ? "TEAMS" : "FREE-FOR-ALL"}</p>
    <h1>Finding your<br><span class="accent">crew.</span></h1>
    <p>We’re bringing 2–8 players together. Your match starts when the table is ready or the 20-second search ends.</p>
    <div class="count-pill count-inline">${state.players.length} joined · <span data-deadline="${state.queue_deadline || ""}">${remaining}s</span></div>
    <div class="player-list queue-players">${renderPlayers(state.players, false)}</div>
    <button class="btn btn-light" type="button" data-action="cancel-queue">Cancel search</button>
  </section>`;
}

function renderLobby() {
  const players = state.players;
  const remaining = Math.max(0, 8 - players.length);
  const invite = `${location.origin}/?room=${encodeURIComponent(state.code)}`;
  const hostControls = state.is_host && !state.online;
  return `<section class="screen">
    <div class="room-heading"><p class="eyebrow">${state.online ? "SERVER MATCH · NO HOST" : `PRIVATE ROOM · ${escapeHtml(playerName(state.players, state.viewer_id))} IS HOST`}</p><h1>Invite your <span class="accent">crew.</span></h1></div>
    <div class="room-code-box"><small>SHARE THIS ROOM CODE</small><div class="room-code" aria-label="Room code ${escapeHtml(state.code)}">${escapeHtml(state.code.split("").join(" "))}</div><div class="code-actions"><button class="btn" type="button" data-action="copy-code">Copy code</button><button class="btn" type="button" data-action="share-room" data-invite="${escapeHtml(invite)}">Share link</button></div></div>
    <div class="lobby-grid">
      <section class="surface">
        <div class="section-title"><h2>Players</h2><span class="count-pill">${players.length} / 8 joined</span></div>
        <div class="player-list">${renderPlayers(players, true)}${remaining ? `<div class="wait-row">＋ &nbsp;Waiting for up to ${remaining} more</div>` : ""}</div>
        <div class="lobby-actions"><p>Share the link or code with your friends.<br>${state.is_host ? "Start when everyone is ready." : "The host will start when everyone is ready."}</p>${hostControls ? `<button class="btn btn-primary btn-wide" type="button" data-action="start" ${players.length < 2 ? "disabled" : ""}>Start game <span aria-hidden="true">→</span></button>` : `<div class="wait-row">${state.online ? "Match is assembling players…" : "Waiting for the host to start"}</div>`}</div>
      </section>
      <section class="surface">
        <div class="section-title"><h2>Match settings</h2>${hostControls ? `<span class="tag">HOST CONTROLS</span>` : ""}</div>
        <div class="settings-grid">
          <div class="setting"><label for="setting-mode">Game mode</label><select class="select" id="setting-mode" data-setting="mode" ${hostControls ? "" : "disabled"}><option value="free" ${state.mode === "free" ? "selected" : ""}>Free-for-all</option><option value="teams" ${state.mode === "teams" ? "selected" : ""}>Teams</option></select></div>
          <div class="setting"><label for="setting-theme">Challenge theme</label><select class="select" id="setting-theme" data-setting="theme" ${hostControls ? "" : "disabled"}><option value="mixed" ${state.theme === "mixed" ? "selected" : ""}>A little bit of everything</option><option value="movies" ${state.theme === "movies" ? "selected" : ""}>Movies & stories</option><option value="food" ${state.theme === "food" ? "selected" : ""}>Food & snacks</option><option value="travel" ${state.theme === "travel" ? "selected" : ""}>Travel & places</option><option value="silly" ${state.theme === "silly" ? "selected" : ""}>Silly & surprising</option></select></div>
          <div class="setting"><label for="setting-rounds">Rounds in this match</label><select class="select" id="setting-rounds" data-setting="rounds" ${hostControls ? "" : "disabled"}><option value="3" ${state.total_rounds === 3 ? "selected" : ""}>3 rounds</option><option value="5" ${state.total_rounds === 5 ? "selected" : ""}>5 rounds</option><option value="7" ${state.total_rounds === 7 ? "selected" : ""}>7 rounds</option></select></div>
        </div>
        <p class="form-note">${state.online ? "The server sets the theme and plays 3 rounds." : "Choose the mix that suits your group."}</p>
      </section>
    </div>
  </section>`;
}

function renderPlayers(players, showHost) {
  return players.map((player) => `<div class="player-row"><span class="avatar" aria-hidden="true">${escapeHtml(initial(player.name))}</span><span class="player-info"><strong>${escapeHtml(player.name)}${player.id === state.viewer_id ? " <span class=\"muted\">(you)</span>" : ""}</strong><small>${player.connected ? "Ready to play" : "Reconnecting…"}</small></span><span class="player-tags">${showHost && player.is_host ? "<span class=\"tag\">Host</span>" : ""}${player.team ? `<span class="tag team-${player.team.toLowerCase()}">Team ${escapeHtml(player.team)}</span>` : ""}</span></div>`).join("");
}

function renderGame() {
  if (state.status === "round_over") return renderRoundResults();
  const current = state.turn_player_id === state.viewer_id;
  const challenge = state.challenge;
  const ownTurn = current && !challenge;
  const myTeam = state.players.find((player) => player.id === state.viewer_id)?.team;
  const scoreText = state.mode === "teams"
    ? `TEAM ${myTeam || ""} · ${state.team_scores?.[myTeam] || 0} PTS`
    : `TABLE SCORE ${state.players.reduce((sum, player) => sum + player.score, 0)}`;
  return `<section class="screen game-shell">
    <div class="game-top"><span>ROUND <strong>${state.round_number} / ${state.total_rounds}</strong> <b>·</b> ${escapeHtml(scoreText)}</span><span class="live-tag">${connected ? "Connected" : "Reconnecting"}</span></div>
    ${state.mode === "teams" ? renderTeamStrip() : ""}
    <div class="opponents">${state.players.filter((player) => player.id !== state.viewer_id).map((player) => `<div class="opponent ${player.id === state.turn_player_id ? "current" : ""}"><span class="avatar">${escapeHtml(initial(player.name))}</span><span><strong>${escapeHtml(player.name)}</strong><small>${player.count} ${player.count === 1 ? "card" : "cards"}${player.id === state.turn_player_id ? " · next" : ""}</small></span></div>`).join("")}</div>
    <div class="table-center">
      <div class="turn-chip ${ownTurn ? "" : "waiting"}">${challenge ? "Challenge in progress" : ownTurn ? "Your turn" : `${escapeHtml(playerName(state.players, state.turn_player_id) || "Player")}’s turn`} · <span data-deadline="${state.turn_deadline || challenge?.deadline || ""}">--</span>s</div>
      <div class="pile"><div class="card-face back" aria-label="Draw pile"><span class="middle">C</span></div><span class="pile-label">Draw pile · ${state.draw_count}</span></div>
      <div class="pile"><div class="card-face ${state.discard?.color || "wild"}"><span class="corner">${escapeHtml(cardCorner(state.discard))}</span><span class="middle">${escapeHtml(cardSymbol(state.discard))}</span><span class="corner">${escapeHtml(cardCorner(state.discard))}</span></div><span class="pile-label">Discard · ${escapeHtml(COLOR_NAMES[state.current_color] || "Wild")}</span></div>
    </div>
    ${challenge ? renderChallenge(challenge) : ""}
    <section class="hand-panel"><div class="hand-heading"><span>Your hand · ${state.hand.length} ${state.hand.length === 1 ? "card" : "cards"}</span><span>${ownTurn ? "Tap a playable card" : "Cards stay private to you"}</span></div>
      <div class="hand-cards" aria-label="Your cards">${state.hand.map((card) => renderHandCard(card, ownTurn)).join("")}</div>
      <div class="turn-controls"><span>Moves confirm when the server accepts them.</span>${ownTurn ? (state.drawn_card_id ? `<button class="btn btn-light" data-action="end-turn">End turn</button>` : `<button class="btn btn-light" data-action="draw">Draw card</button>`) : `<span>${connected ? "Waiting for the table…" : "Reconnecting to your table…"}</span>`}</div>
      ${ownTurn ? `<div class="turn-status">Your turn · play a card or draw</div>` : `<div class="turn-status wait">${challenge ? "Answer the challenge or vote below" : "Stay ready — your turn is coming up"}</div>`}
    </section>
  </section>`;
}

function renderTeamStrip() {
  return `<div class="team-strip team-strip-game">${["A", "B"].map((team) => {
    const members = state.players.filter((player) => player.team === team);
    const mine = members.some((player) => player.id === state.viewer_id);
    return `<div class="team-box ${team === "B" ? "team-b" : ""}"><strong>Team ${team}${mine ? " · Your team" : ""}</strong><span>${members.map((player) => `${escapeHtml(player.name)}${player.id === state.viewer_id ? " (you)" : ""}`).join(" · ")}</span></div>`;
  }).join("")}</div>`;
}

function renderHandCard(card, ownTurn) {
  const legal = ownTurn && isPlayable(card);
  const kind = escapeHtml(card.kind);
  const id = escapeHtml(card.id);
  const symbol = escapeHtml(cardSymbol(card));
  const corner = escapeHtml(cardCorner(card));
  const color = card.color || "wild";
  return `<button class="hand-card ${escapeHtml(color)} ${pendingCardId === card.id ? "pending" : ""}" type="button" data-action="play" data-card-id="${id}" aria-label="Play ${escapeHtml(cardName(card))}" ${legal ? "" : "disabled"}><small>${corner}</small><span class="symbol">${symbol}</span><small>${corner}</small></button>`;
}

function renderChallenge(challenge) {
  const answerer = challenge.player_id === state.viewer_id;
  const asker = challenge.phase === "answer";
  const deadline = challenge.deadline || "";
  if (asker) {
    return `<section class="challenge-card"><div class="challenge-meta"><span>${challenge.source === "llm" ? "AI-generated challenge" : "Challenge prompt"} · ${answerer ? "Your answer" : `${escapeHtml(playerName(state.players, challenge.player_id))} is answering`}</span><span><b data-deadline="${deadline}">--</b>s</span></div><h3>${answerer ? "Show us what you’ve got." : "Challenge time."}</h3><p class="challenge-prompt">${escapeHtml(challenge.prompt)}</p>${answerer ? `<div class="answer-line"><input class="input" id="challenge-answer" maxlength="240" placeholder="Type a quick answer…" autocomplete="off"><button class="btn btn-primary" data-action="submit-answer">Send answer</button></div><button class="btn btn-light compact mic-button" type="button" data-action="mic">🎙 &nbsp;Answer by voice</button><p class="mic-note">Microphone access is requested only when you tap voice. Your browser’s speech service handles voice; Color Club receives the transcript. Typing always works.</p>` : `<div class="wait-row">The group will vote after the answer.</div>`}</section>`;
  }
  return `<section class="challenge-card"><div class="challenge-meta"><span>Group vote · ${challenge.eligible_voter ? "Your vote counts" : "Waiting for votes"}</span><span><b data-deadline="${deadline}">--</b>s</span></div><h3>Does that answer fit?</h3><p class="challenge-prompt">${escapeHtml(challenge.prompt)}</p><div class="answer-quote">${escapeHtml(challenge.answer || "No answer submitted.")}</div>${challenge.eligible_voter && !challenge.has_voted ? `<div class="vote-row"><button class="btn btn-green" data-action="vote" data-yes="true">Yes, it fits</button><button class="btn btn-light" data-action="vote" data-yes="false">Not quite</button></div>` : `<div class="wait-row">${challenge.has_voted ? "Your vote is in · " : ""}${challenge.votes_cast} / ${challenge.votes_needed} votes counted</div>`}</section>`;
}

function renderRoundResults() {
  const winner = state.players.find((player) => player.id === state.round_winner_id);
  const finalRound = state.round_number >= state.total_rounds;
  return `<section class="screen">
    <div class="results-hero"><p class="eyebrow">ROUND ${state.round_number} COMPLETE${finalRound ? " · MATCH COMPLETE" : ""}</p><h1>${finalRound ? "What a table." : "Round in the books."}<br><span class="winner-name">${escapeHtml(winner?.name || "A player")} takes it.</span></h1><p>${state.mode === "teams" ? `Team ${escapeHtml(winner?.team || "")} scores the round.` : `${escapeHtml(winner?.name || "The winner")} takes the round points.`}</p></div>
    <div class="results-grid"><section class="surface"><div class="section-title"><h2>${finalRound ? "Current standings" : "Match standings"}</h2><span class="count-pill">${state.round_number} / ${state.total_rounds} rounds</span></div>${renderScoreboard()}</section><section class="surface"><h3>${finalRound ? "Final table" : "Next up"}</h3><p class="muted small">${finalRound ? "The final score includes each round win and the cards left in opponents’ hands." : "Every round starts with seven cards. Stay sharp — the next deal is coming."}</p>${!finalRound && state.is_host ? `<button class="btn btn-primary btn-wide" data-action="next-round">Deal next round →</button>` : !finalRound && state.online ? `<div class="wait-row">Next round starts automatically in a moment.</div>` : !finalRound ? `<div class="wait-row">Waiting for the host to deal the next round.</div>` : `<button class="btn btn-light btn-wide" data-action="back-home">Back to join page</button>`}</section></div>
  </section>`;
}

function renderResults() {
  const winnerLabel = state.mode === "teams" ? (state.match_winner === "shared" ? "A shared win" : `Team ${state.match_winner}`) : state.match_winner === "shared" ? "A shared win" : playerName(state.players, state.match_winner);
  return `<section class="screen">
    <div class="results-hero"><p class="eyebrow">MATCH COMPLETE · ${state.round_number} ROUNDS</p><h1>That was a<br><span class="winner-name">close one.</span></h1><p class="eyebrow result-label">WINNER</p><h2>${escapeHtml(winnerLabel)} takes it.</h2><p>${escapeHtml(state.recap || "Adding up the best moments from your match…")}</p></div>
    <div class="results-grid"><section class="surface"><div class="section-title"><h2>Final scores</h2><span class="count-pill">${state.round_number} rounds</span></div>${renderScoreboard()}</section><section class="surface"><h3>Run it back?</h3><p class="muted small">Vote for a rematch with the same players and settings.</p><button class="btn ${state.rematch_votes.includes(state.viewer_id) ? "btn-soft" : "btn-primary"} btn-wide" data-action="rematch">${state.rematch_votes.includes(state.viewer_id) ? "Rematch vote counted ✓" : "Vote for rematch"}</button><p class="form-note">${state.rematch_votes.length} / ${state.players.filter((player) => player.connected).length} votes in</p><button class="btn btn-light btn-wide result-secondary" data-action="back-home">Back to join page</button></section></div>
  </section>`;
}

function renderScoreboard() {
  if (state.mode === "teams") {
    return `<div class="score-list">${["A", "B"].map((team, index) => `<div class="score-row"><span class="score-rank">0${index + 1}</span><span class="score-name">Team ${team}<small>${state.team_round_wins?.[team] || 0} round wins · ${state.players.filter((player) => player.team === team).map((player) => escapeHtml(player.name)).join(", ")}</small></span><span class="score-value">${state.team_scores?.[team] || 0}</span></div>`).join("")}</div>`;
  }
  const players = [...state.players].sort((a, b) => b.score - a.score || b.round_wins - a.round_wins);
  return `<div class="score-list">${players.map((player, index) => `<div class="score-row"><span class="score-rank">${String(index + 1).padStart(2, "0")}</span><span class="score-name">${escapeHtml(player.name)}${player.id === state.viewer_id ? " (you)" : ""}<small>${player.round_wins} ${player.round_wins === 1 ? "round win" : "round wins"}</small></span><span class="score-value">${player.score}</span></div>`).join("")}</div>`;
}

function playerName(players, id) {
  return players.find((player) => player.id === id)?.name || "";
}
function initial(name) { return Array.from(String(name || "?").trim())[0]?.toUpperCase() || "?"; }
function cardCorner(card) {
  if (!card) return "?";
  if (card.kind === "number") return card.value;
  return CARD_SYMBOLS[card.kind] || "✦";
}
function cardSymbol(card) { return card?.kind === "number" ? card.value : CARD_SYMBOLS[card?.kind] || "?"; }
function cardName(card) { return card?.kind === "number" ? `${COLOR_NAMES[card.color]} ${card.value}` : `${COLOR_NAMES[card?.color] || "Wild"} ${card?.kind}`; }
function isPlayable(card) {
  if (!state.discard) return false;
  if (state.drawn_card_id && card.id !== state.drawn_card_id) return false;
  if (card.kind === "wild" || card.kind === "wild4") {
    if (card.kind === "wild4" && state.hand.some((held) => held.id !== card.id && held.color === state.current_color)) return false;
    return true;
  }
  const top = state.discard;
  return card.color === state.current_color || (card.kind !== "number" && card.kind === top.kind) || (card.kind === "number" && top.kind === "number" && card.value === top.value);
}

function currentName() {
  const input = document.getElementById("display-name");
  const name = input?.value.trim() || sessionStorage.getItem("cc_name") || "";
  if (name) sessionStorage.setItem("cc_name", name);
  return name;
}

function updateSettings() {
  const mode = document.getElementById("setting-mode")?.value;
  const theme = document.getElementById("setting-theme")?.value;
  const rounds = Number(document.getElementById("setting-rounds")?.value);
  send("settings", { mode, theme, rounds });
}

function playCard(cardId) {
  const card = state?.hand.find((item) => item.id === cardId);
  if (!card || !isPlayable(card)) return;
  pendingCardId = cardId;
  if (card.kind === "wild" || card.kind === "wild4") {
    requestedWildCard = cardId;
    modalRoot.innerHTML = `<div class="modal-backdrop" data-action="close-modal"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="choose-color-title"><p class="eyebrow">WILD CARD</p><h2 id="choose-color-title">Choose a color.</h2><p class="muted">The next player gets a themed challenge.</p><div class="color-options">${COLORS.map((color) => `<button class="color-option ${color}" data-action="choose-color" data-color="${color}">${COLOR_NAMES[color]}</button>`).join("")}</div></section></div>`;
    return;
  }
  render();
  send("play", { card_id: cardId });
}

function updateTimers() {
  document.querySelectorAll("[data-deadline]").forEach((element) => {
    const deadline = Number(element.dataset.deadline);
    if (!deadline) { element.textContent = "--"; return; }
    const remaining = Math.max(0, Math.ceil(deadline - (Date.now() / 1000 + serverClockOffset)));
    element.textContent = String(remaining);
  });
}

function useMicrophone() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    showToast("Voice recognition is not available in this browser. Type your answer instead.");
    return;
  }
  const button = document.querySelector('[data-action="mic"]');
  const recognition = new SpeechRecognition();
  recognition.lang = navigator.language || "en-US";
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
  button.disabled = true;
  button.textContent = "Listening… tap microphone permission if asked";
  recognition.onresult = (event) => {
    const input = document.getElementById("challenge-answer");
    if (input) { input.value = event.results[0][0].transcript.slice(0, 240); input.focus(); }
  };
  recognition.onerror = () => showToast("Voice input could not start. You can type your answer instead.");
  recognition.onend = () => { if (button.isConnected) { button.disabled = false; button.textContent = "🎙  Answer by voice"; } };
  recognition.start();
}

app.addEventListener("input", (event) => {
  if (event.target.id === "display-name") sessionStorage.setItem("cc_name", event.target.value.slice(0, 20));
  if (event.target.id === "room-code") event.target.value = event.target.value.toUpperCase().replace(/[^A-Z2-9]/g, "").replace(/[OI01]/g, "").slice(0, 5);
});

app.addEventListener("change", (event) => {
  if (event.target.matches("[data-setting]")) updateSettings();
});

app.addEventListener("click", async (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;
  if (action === "mode") {
    sessionStorage.setItem("cc_mode", target.dataset.mode);
    render();
  } else if (action === "create" || action === "matchmaking") {
    const name = currentName();
    if (!name) { showToast("Add your display name first."); document.getElementById("display-name")?.focus(); return; }
    sessionStorage.removeItem("cc_room");
    send("enter", { mode: action === "create" ? "create" : "matchmaking", name, play_mode: sessionStorage.getItem("cc_mode") || "free" });
  } else if (action === "join") {
    const name = currentName();
    const code = document.getElementById("room-code")?.value.trim().toUpperCase() || "";
    if (!name) { showToast("Add your display name first."); document.getElementById("display-name")?.focus(); return; }
    if (code.length !== 5) { showToast("Enter the 5-character room code."); document.getElementById("room-code")?.focus(); return; }
    sessionStorage.removeItem("cc_room");
    send("enter", { mode: "join", name, code });
  } else if (action === "copy-code") {
    try { await navigator.clipboard.writeText(state.code); showToast(`Room code ${state.code} copied.`); }
    catch { showToast(`Share this room code: ${state.code}`); }
  } else if (action === "share-room") {
    const invite = target.dataset.invite;
    try {
      if (navigator.share) await navigator.share({ title: "Join my Color Club game", text: `Join my room with code ${state.code}.`, url: invite });
      else { await navigator.clipboard.writeText(invite); showToast("Invite link copied."); }
    } catch { /* A canceled share sheet is not an error. */ }
  } else if (action === "start") {
    send("start");
  } else if (action === "cancel-queue") {
    send("cancel_queue");
    sessionStorage.removeItem("cc_room");
    state = null;
    render();
  } else if (action === "play") {
    playCard(target.dataset.cardId);
  } else if (action === "choose-color") {
    const cardId = requestedWildCard;
    requestedWildCard = null;
    modalRoot.innerHTML = "";
    pendingCardId = cardId;
    render();
    send("play", { card_id: cardId, color: target.dataset.color });
  } else if (action === "close-modal" && event.target === target) {
    requestedWildCard = null; pendingCardId = null; modalRoot.innerHTML = "";
  } else if (action === "draw") {
    send("draw");
  } else if (action === "end-turn") {
    send("end_turn");
  } else if (action === "submit-answer") {
    const answer = document.getElementById("challenge-answer")?.value || "";
    send("answer", { answer });
  } else if (action === "mic") {
    useMicrophone();
  } else if (action === "vote") {
    send("vote", { yes: target.dataset.yes === "true" });
  } else if (action === "next-round") {
    send("next_round");
  } else if (action === "rematch") {
    send("rematch_vote");
  } else if (action === "back-home") {
    send("leave");
    state = null;
    sessionStorage.removeItem("cc_room");
    render();
  }
});

document.getElementById("brand-home").addEventListener("click", () => {
  if (state) send("leave");
  state = null;
  sessionStorage.removeItem("cc_room");
  render();
});

setInterval(() => {
  if (socket?.readyState === WebSocket.OPEN && state) send("heartbeat");
  updateTimers();
}, 25_000);
setInterval(updateTimers, 250);

connect();
render();
