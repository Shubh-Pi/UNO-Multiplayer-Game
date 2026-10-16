"""FastAPI entry point for local development and the Render demo service."""

from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .ai_service import ChallengeAI
from .hub import Hub


BASE_DIR = Path(__file__).resolve().parent
SESSION_SECRET = os.getenv("SESSION_SECRET") or secrets.token_urlsafe(48)
SECURE_COOKIES = os.getenv("SECURE_COOKIES", "true" if os.getenv("RENDER") else "false").lower() == "true"


@asynccontextmanager
async def lifespan(app: FastAPI):
    ai = ChallengeAI()
    hub = Hub(ai)
    await hub.start()
    app.state.ai = ai
    app.state.hub = hub
    yield
    await hub.close()
    await ai.close()


app = FastAPI(title="Color Club", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="color_club_guest",
    max_age=60 * 60 * 24 * 7,
    same_site="lax",
    https_only=SECURE_COOKIES,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self' ws: wss:; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=(self)"
    if SECURE_COOKIES:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path == "/":
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    # A random guest id is an anonymous room identity, not a user account.
    request.session.setdefault("guest_id", secrets.token_urlsafe(24))
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    session = websocket.scope.get("session", {})
    guest_id = session.get("guest_id")
    if not isinstance(guest_id, str) or len(guest_id) < 20:
        await websocket.close(code=1008, reason="Open the game page first")
        return
    await websocket.app.state.hub.serve(websocket, guest_id)
