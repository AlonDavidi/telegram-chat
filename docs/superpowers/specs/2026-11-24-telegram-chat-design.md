# Telegram Bot Chat — Design

Date: 2026-11-24
Status: Approved 2026-11-24

## 1. Intent

This is a home assignment for a **Senior Full-Stack Developer** role. The system is a web chat UI (React) that acts as the console of a Telegram bot (FastAPI backend): the person in the browser "is" the bot and chats with exactly one remote Telegram participant.

The assignment states that reviewers focus on **architecture, clarity and engineering judgment**, not visual polish or feature count. So the "why" behind every decision in this doc is: show clean separation of concerns, correct concurrency and ordering, and well-argued trade-offs, while keeping the system trivially runnable by a reviewer.

Source requirements: `README.md` at the repo root (note: its Docker section is truncated in the original repo; we complete it).

## 2. Goals

- Real-time, bidirectional chat between the browser and one Telegram private chat.
- Exactly one Telegram participant at a time, claimed with `/start` and released with `/stop`; others are politely rejected.
- Safe state management: no race lets two participants claim the bot; messages are shown in a single, consistent order.
- Each message shows text and timestamp; incoming and outgoing are visually distinct.
- Clean, layered backend; small, readable frontend.
- Automated tests (backend and frontend).
- Docker Compose setup that runs the whole system with one command plus a `.env` file.
- README that documents setup, architecture, the WebSocket protocol, assumptions and trade-offs.

## 3. Non-goals

- Persistence. History lives in memory and is lost on restart (explicitly allowed by the assignment).
- Authentication of the web UI user.
- Media (photos, stickers, files, voice). Text only (see 6.6 for how non-text input is handled).
- Group chats, multiple bots, multiple simultaneous participants.
- Horizontal scaling / multiple backend instances.
- Visual polish beyond the existing CSS.

## 4. Anti-goals (failure even if it "works")

- Anything that requires the reviewer to expose a public URL (ngrok, webhooks) to run it.
- Over-engineering: no Redis, DB, message broker or auth framework. Mention them as production notes only.
- Real Telegram calls inside tests.
- A `main.py` that does everything.
- Hidden assumptions. Every judgment call must appear in the README.

## 5. Constraints

- Frontend: React (existing Vite + React 18 JavaScript project; stays JavaScript).
- Backend: FastAPI, Python 3.10+.
- Bot token configured manually in the backend (environment / `.env`), never committed.
- Telegram long polling allows only **one** consumer per bot token. Running two backend processes with the same token yields `409 Conflict` from Telegram. The backend must run as a single process (single uvicorn worker).
- Telegram message text limit: 4096 characters.

## 6. Decided behavior

### 6.1 Approaches considered (communication channel)

| Approach | Trade-offs | Decision |
|---|---|---|
| **A. WebSocket** — one bidirectional connection, custom JSON protocol with `seq` / `client_id` | Single channel, natural for chat, native in FastAPI. Reconnect and resume must be hand-written. | **Chosen** |
| B. SSE for incoming + `POST /messages` for sending | `EventSource` auto-reconnects with `Last-Event-ID`; REST errors are simple. Two channels; UI must reconcile POST responses with the stream to avoid duplicates. | Rejected: less natural for chat; reconciliation complexity moves to the UI. |
| C. Telegram webhook instead of polling (orthogonal to A/B) | Production-friendly, no polling loop. Requires public HTTPS endpoint. | Rejected for the assignment (violates anti-goal); noted as a production improvement. |

Telegram library: `python-telegram-bot` (v21+, asyncio), long polling, started and stopped inside the FastAPI lifespan. (Builder's call to swap for `aiogram` is **not** open; keep PTB.)

### 6.2 Session (single participant) rules

The backend holds at most one **active session** = one Telegram private `chat_id` plus display info (first name, username) and a `session_id` (UUID generated on claim).

| Event | Bot is free | Sender owns the session | Bot is busy with someone else |
|---|---|---|---|
| `/start` | Claim: new session, history reset, reply "Connected. Messages you send here will appear in the web chat." | Reply "You're already connected." (no reset) | Reply "This bot is already in a conversation with someone else. Try again later." |
| `/stop` | Reply "You're not connected. Send /start to connect." | Release session, reply "Disconnected." | Same busy reply as above |
| Text message | Reply "Send /start to connect." Not forwarded. | Forwarded to UI as incoming | Same busy reply; not forwarded |
| Message from a group/channel | Ignored (private chats only) | — | — |

- Claim and release are atomic (guarded by the service lock, 6.4). Two concurrent `/start` from different users: exactly one wins.
- On release, the session ends; history stays visible until a **new** session is claimed, at which point history is reset (the assignment says the chat may show only the current session). *(My call.)*
- The UI user cannot kick the participant. *(My call; listed as a possible extension.)*

### 6.3 Message model

```
Message {
  seq: int            # strictly increasing per server process, assigned by the store
  id: str             # UUID
  session_id: str
  direction: "incoming" | "outgoing"
  text: str
  timestamp: str      # ISO 8601 UTC
  client_id: str|null # echo of the UI-generated id for outgoing messages
}
```

- `timestamp` = time the message was sent: for incoming, Telegram's `message.date`; for outgoing, the `date` of the `Message` returned by Telegram's `sendMessage`. UI renders it in local time.
- `seq` is the single source of ordering. The UI always sorts / appends by `seq`.

### 6.4 Concurrency and ordering

- All state mutations (claim, release, append incoming, send outgoing) go through `ChatService`, serialized by one `asyncio.Lock`.
- An outgoing message is appended to the store **only after Telegram confirms delivery** (`sendMessage` returned). The Telegram call is made while holding the lock, so:
  - the order of messages in Telegram equals the order of `seq` in the UI;
  - a `/stop` cannot interleave between "read current chat_id" and "send".
- Trade-off (documented in README): a slow Telegram call delays processing of other events for that duration. Acceptable at one-participant scale; a per-session queue would be the production alternative.
- PTB processes updates sequentially (`concurrent_updates` disabled, the default), preserving incoming order.
- Append and broadcast happen inside the same critical section, so every WebSocket client sees events in `seq` order.
- Broadcasting must not let one slow browser block others: each connection has its own bounded `asyncio.Queue` drained by a writer task; if a queue overflows the connection is closed (client reconnects and resyncs via `last_seq`).

### 6.5 WebSocket protocol (`/ws`)

Client → server:

```json
{"type": "hello", "session_id": "<uuid>|null", "last_seq": 0}
{"type": "send", "client_id": "<uuid>", "text": "hi"}
```

Server → client:

```json
{"type": "snapshot", "status": {...}, "messages": [Message, ...]}
{"type": "message", "message": Message}
{"type": "status", "status": {"connected": true, "session_id": "...", "participant": {"first_name": "...", "username": "..."}}}
{"type": "error", "client_id": "<uuid>|null", "code": "NO_PARTICIPANT|INVALID_MESSAGE|TELEGRAM_ERROR|BAD_REQUEST", "detail": "..."}
```

- The first client frame must be `hello`. The server replies with `snapshot`: the current status plus messages from the store.
- The store holds the messages of the **latest session**, which stays readable after `/stop` until a new session is claimed. The status keeps the latest `session_id` even when `connected: false` (it is `null` only if nobody ever claimed the bot since startup). *(My call.)*
- Resume rule: if the client's `session_id` equals the store's `session_id`, the snapshot holds messages with `seq > last_seq`; otherwise (different session, or the backend restarted and the ids no longer match) the snapshot holds **all** store messages and the UI replaces its list. This makes a backend restart safe even though `seq` restarts from 1. *(My call.)*
- Outgoing success is signalled by the broadcast `message` event carrying the original `client_id` (no separate `ack` type). Other tabs see it as a normal outgoing message.
- Validation: `text` trimmed, non-empty, ≤ 4096 chars, else `INVALID_MESSAGE`. Unknown/malformed frames → `BAD_REQUEST` (connection stays open).
- Sending with no active session → `NO_PARTICIPANT`. Telegram failure (e.g. participant blocked the bot) → `TELEGRAM_ERROR`. If Telegram says the bot was blocked (`Forbidden`), the session is released and a `status` event is broadcast. *(My call.)*

Also: `GET /health` (exists) and `GET /status` returning the same status object (useful for debugging and the Docker healthcheck).

### 6.6 Non-text input from the participant

Non-text messages (photo, sticker, voice, ...) from the session owner are not forwarded as content. The bot replies "Only text messages are supported." and nothing is shown in the UI. Captions are ignored. *(My call.)*

### 6.6.1 Other Telegram edge cases *(my calls, documented in README)*

- Updates that queued up while the backend was down are dropped on startup (`drop_pending_updates=True`), since the in-memory session they belonged to no longer exists.
- Edited messages are ignored.
- A `sendMessage` that times out is reported as `TELEGRAM_ERROR` and the UI marks it failed, even though Telegram may have delivered it. Documented as a known limitation.

### 6.7 Frontend behavior

- Connects to same-origin `/ws` (Vite dev server proxies `/ws` to `localhost:8000`; nginx does the same in Docker). No hard-coded backend URL in code.
- Status bar: WebSocket state (connecting / connected / reconnecting) and participant state ("Connected to @username" / "Waiting for a Telegram user to send /start").
- Sending: message appears immediately as **pending** (dimmed) keyed by `client_id`; replaced by the server `message` with the same `client_id`; on `error` with that `client_id` it is marked **failed** with the error text. Send is disabled while no participant is connected or the socket is down.
- Enter sends; Shift+Enter is not required (single-line input).
- Incoming vs outgoing styling uses the existing `.incoming` / `.outgoing` CSS classes.
- Auto-scroll to the newest message.
- Reconnect with exponential backoff (1s, 2s, 4s… capped at 10s), sending `hello` with the last known `seq`.
- Structure: `useChatSocket` hook (connection + protocol), a pure `chatReducer` (state transitions, unit-tested), components `StatusBar`, `MessageList`, `MessageInput`.

### 6.8 Backend structure

```
backend/
  app/
    main.py           # app factory, lifespan (start/stop bot), routes, CORS
    config.py         # pydantic-settings: TELEGRAM_BOT_TOKEN (required), CORS_ORIGINS
    models.py         # Message, Participant, Status, WS frame schemas (pydantic)
    store.py          # MessageStore: append (assigns seq), since(seq), reset
    session.py        # Session state + claim/release rules (pure logic)
    hub.py            # ConnectionHub: register/unregister, per-connection queue, broadcast
    service.py        # ChatService: the lock; orchestrates session, store, gateway, hub
    telegram_gateway.py  # PTB Application wiring; handlers -> ChatService; send_text()
    ws.py             # /ws endpoint: protocol parsing, validation, dispatch to ChatService
  tests/
  requirements.txt
  requirements-dev.txt
  Dockerfile
.env.example          # at repo root, used by docker compose and local runs
```

- `ChatService` depends on a `TelegramGateway` **protocol/interface** (`send_text(chat_id, text) -> sent_at`), so tests inject a fake. The gateway calls back into `ChatService` for `/start`, `/stop` and text.
- Missing `TELEGRAM_BOT_TOKEN` → fail fast at startup with a clear error.
- CORS tightened to `CORS_ORIGINS` (default `http://localhost:5173`).

### 6.9 Testing

Backend (`pytest`, `pytest-asyncio`, FastAPI `TestClient`, fake gateway):
- Session rules table (6.2), every cell.
- Concurrent claims via `asyncio.gather`: exactly one owner.
- Store: `seq` strictly increasing under concurrent appends; `since()`; reset on new session.
- Service: send with no participant → `NO_PARTICIPANT`; message stored only after gateway success; gateway failure → error, nothing stored; `Forbidden` releases the session.
- Ordering: concurrent sends are delivered to the gateway and stored in the same order.
- WebSocket integration: hello → snapshot; send → broadcast with `client_id` to two clients; validation errors; resume with `last_seq`.

Frontend (`vitest`): `chatReducer` — pending → confirmed, pending → failed, snapshot merge, session change clears list, ordering by `seq`. Component tests are optional for the builder.

### 6.10 Docker

- `backend/Dockerfile`: `python:3.12-slim`, single uvicorn worker.
- `frontend/Dockerfile`: multi-stage — Node build, then `nginx:alpine` serving `dist/` with an nginx config that proxies `/ws` (with Upgrade headers) and `/status` to the backend service.
- `docker-compose.yml` at the repo root: `backend` (env from `.env`), `frontend` exposed on `http://localhost:8080`.
- Run: `cp .env.example .env`, fill the token, `docker compose up --build`.

### 6.11 README

Keep the original assignment text under an "Assignment" heading at the bottom, and put the solution docs on top *(my call)*: setup (local + Docker), how to try it (BotFather → token → open UI → send `/start` to the bot), architecture diagram (text), WebSocket protocol, assumptions, trade-offs (WebSocket vs SSE, polling vs webhook, in-memory state and the single-process constraint, lock held during Telegram call), and production notes (UI auth, rate limiting, media support, kick button), including the multi-instance design:
- Telegram webhook instead of polling (polling allows a single consumer per token).
- Session claim/release as a compare-and-set in the DB (`UPDATE ... WHERE owner IS NULL` / DynamoDB `ConditionExpression`), not a Redis lock: no TTL expiry hazard, no orphaned lock on crash, state and lock can't diverge.
- `seq` and history persisted in the DB in the same transaction as the message.
- Outgoing ordering via a single consumer per chat (SQS FIFO with `MessageGroupId = chat_id`, or Redis Streams) instead of holding a lock across the Telegram call; incoming ordering via webhook `max_connections=1` or the same queue.
- Redis pub/sub for fan-out to WebSocket connections across instances; resume on reconnect reads from the DB via `last_seq`, so lost pub/sub messages are harmless.

## 7. Left to the builder

- Exact file split inside the frontend and naming details, as long as 6.7 holds.
- Logging format (use stdlib `logging`, INFO level).
- Queue size per WebSocket connection (suggest 100).
- Exact wording of README sections and bot replies (the texts above are suggestions).
- Pinning exact dependency versions.
