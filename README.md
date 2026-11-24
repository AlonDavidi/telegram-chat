# Telegram Bot Chat

A web chat (React) that acts as the console of a Telegram bot (FastAPI). The person in the browser talks *as the bot* with exactly one Telegram user at a time, in real time, over a WebSocket.

```
┌──────────────┐  WebSocket /ws   ┌───────────────────────── FastAPI (1 process) ─────────────────────────┐   long polling   ┌──────────┐
│ React UI     │ ◄──────────────► │ ws.py ──► ChatService (one asyncio.Lock) ◄── TelegramGateway (PTB)  │ ◄──────────────► │ Telegram │
│ useChatSocket│                  │               │        │        │                                      │   sendMessage    │  user    │
│ chatReducer  │                  │        SessionState MessageStore ConnectionHub                         │                  └──────────┘
└──────────────┘                  └──────────────────────────────────────────────────────────────────────┘
```

## Quick start (Docker)

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. `cp .env.example .env` and set `TELEGRAM_BOT_TOKEN=...`
3. `docker compose up --build`
4. Open http://localhost:8080, then in Telegram open your bot and send `/start`.

## Local development

```bash
# backend (terminal 1)
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
cp ../.env.example ../.env   # then set TELEGRAM_BOT_TOKEN
uvicorn app.main:app --reload

# frontend (terminal 2)
cd frontend
npm install
npm run dev                  # http://localhost:5173 (proxies /ws and /status to :8000)
```

Tests:

```bash
cd backend && pytest          # 76 tests, no network: Telegram is faked
cd frontend && npm test       # reducer + status bar logic
```

## How to try it

| In Telegram | What happens |
|---|---|
| Account 1 sends `/start` | The bot is claimed. The UI shows "Connected to @user" and enables sending. |
| Account 1 writes | Messages appear in the UI as incoming. |
| You type in the UI | The message reaches account 1; the bubble turns from "Sending…" to a timestamp. |
| Account 2 sends anything | It gets "This bot is already in a conversation…". Nothing reaches the UI. |
| Account 1 sends `/stop` | The bot is free again. History stays on screen until the next `/start`. |

Open the UI in two tabs: both show the same conversation in the same order.

## Architecture

**Backend** (`backend/app/`)

| Module | Responsibility |
|---|---|
| `config.py` | Settings from env / `.env`. Missing or empty token → the process exits with a clear message. |
| `models.py` | Domain models and every WebSocket frame (pydantic). |
| `session.py` | Single-participant rules (pure logic): claim, release, owner. |
| `store.py` | In-memory history of the current session; assigns `seq`. |
| `hub.py` | Fan-out to browsers. One bounded queue + writer task per socket. |
| `service.py` | `ChatService`: the only place that changes state, under one `asyncio.Lock`. |
| `telegram_gateway.py` | python-telegram-bot: routes updates to the service, sends messages, maps errors. |
| `ws.py` | `/ws`: handshake, parsing, validation, error frames. |
| `main.py` | App factory, lifespan (starts/stops the bot), `/health`, `/status`. |

**Frontend** (`frontend/src/`): `chat/chatReducer.js` is pure state (tested); `chat/useChatSocket.js` owns the socket, resume and reconnect; `components/` render it.

### Concurrency and ordering

Every state change, from Telegram or from a browser, goes through `ChatService` under one lock:

- **Claim is atomic.** Ten simultaneous `/start` → exactly one owner (tested).
- **One order for everything.** `seq` is assigned and the frame is broadcast in the same critical section, so every browser sees the same order. `seq` never resets during the process lifetime.
- **Outgoing order = Telegram order.** The message is sent to Telegram *while holding the lock* and stored only after Telegram confirms it, with Telegram's timestamp. A `/stop` cannot slip between "who is the owner" and "send to the owner".
- **Incoming order.** PTB processes updates one at a time (its default).
- **Slow browsers don't block anyone.** Each socket has its own queue; one that overflows is closed and the browser resyncs.

## WebSocket protocol (`/ws`)

Client → server:

```json
{"type": "hello", "session_id": "<id or null>", "last_seq": 0}
{"type": "send", "client_id": "<uuid>", "text": "hi"}
```

Server → client:

```json
{"type": "snapshot", "status": {...}, "messages": [Message, ...]}
{"type": "message", "message": Message}
{"type": "status", "status": {"connected": true, "session_id": "...", "participant": {"first_name": "...", "username": "..."}}}
{"type": "error", "client_id": "<uuid or null>", "code": "NO_PARTICIPANT | INVALID_MESSAGE | TELEGRAM_ERROR | BAD_REQUEST", "detail": "..."}
```

`Message = {seq, id, session_id, direction: "incoming" | "outgoing", text, timestamp (UTC ISO 8601), client_id}`

- The first frame must be `hello`, otherwise `BAD_REQUEST` and close (1008). Later malformed frames get `BAD_REQUEST` and the connection stays open.
- **Resume:** if `hello.session_id` matches the server's session, the snapshot holds only `seq > last_seq`; otherwise (new session, or the backend restarted) it holds the whole history and the UI replaces its list.
- **Outgoing confirmation** is the broadcast `message` frame carrying the sender's `client_id`; other tabs just see an outgoing message.
- Text is trimmed and must be 1–4096 characters, else `INVALID_MESSAGE`. Length is counted in UTF-16 code units, as Telegram and JavaScript count it, so an emoji counts as 2.
- Binary frames get `BAD_REQUEST`.
- **Origin check:** CORS does not cover WebSockets, so `/ws` rejects (close 1008) browser connections whose `Origin` is neither the page's own host (the Vite / nginx proxy case) nor listed in `CORS_ORIGINS`. Clients that send no `Origin` (scripts) are allowed.

## Session rules

| Event | Bot is free | Sender owns the session | Someone else owns it |
|---|---|---|---|
| `/start` | Claim; new session; history reset | "You're already connected." | Busy reply |
| `/stop` | "You're not connected…" | Release (history kept) | Busy reply |
| Text | "Send /start to connect." | Forwarded to the UI | Busy reply |
| Photo, sticker, … | "Send /start to connect." | "Only text messages are supported." | Busy reply |
| Group / channel chats | ignored | ignored | ignored |

## Assumptions and judgment calls

- **Who is the participant:** whoever sends `/start` while the bot is free; `/stop` releases it. Fixed `chat_id` in config was rejected (the reviewer would first need to find their own chat id).
- **History** lives in memory, belongs to the current session, stays visible after `/stop`, and is cleared when a *new* participant claims the bot. A restart loses it (allowed by the assignment).
- **Blocked bot:** if Telegram answers 403 to a send, the session is released automatically.
- **Updates queued while the backend was down are dropped** on startup; the session they belonged to no longer exists.
- **Edited messages are ignored.** **Non-text** messages get "Only text messages are supported." and are not shown.
- **Unknown commands** (`/help`) from the owner are treated as text.
- **The UI user can't kick the participant** (a possible extension).
- **Lost connection:** in-flight sends are marked failed; if the server did process one, the resume snapshot confirms it.
- **Known limitation:** a `sendMessage` that times out is reported as failed, though Telegram may have delivered it.

## Trade-offs

- **WebSocket vs SSE + POST.** SSE gives free reconnect with `Last-Event-ID`, but two channels mean the UI must reconcile POST responses with the stream. One bidirectional socket with `seq`/`client_id` is the natural fit for chat; reconnect and resume are ~40 lines.
- **Long polling vs webhook.** Polling needs no public URL, so the reviewer runs it with one command. Webhook is the production choice (below).
- **In-memory state, single process.** Simple and correct for one participant. Polling also forbids two consumers per token, so the backend must run with one worker.
- **Lock held during the Telegram call.** It buys strict ordering and a race-free `/stop` with no extra machinery. The cost: a slow Telegram call delays other events for its duration, which is acceptable for one participant.

## Production notes

For several backend instances, "one lock" becomes three separate problems:

| Problem | Tool | Why |
|---|---|---|
| Claim / release the participant | DB compare-and-set: `UPDATE bot_session SET owner=:chat WHERE id=1 AND owner IS NULL` (or DynamoDB `ConditionExpression`) | It's durable state, not a lock: no TTL expiry hazard, nothing orphaned on crash, lock and state can't diverge. A Redis `SET NX PX` lock would need fencing tokens to be safe. |
| Message order and history | `seq` from the DB, written in the same transaction as the message | One source of truth for order and resume. |
| Outgoing order | A single consumer per chat: SQS FIFO with `MessageGroupId = chat_id`, or Redis Streams | Ordering without holding a distributed lock across a network call. Incoming: webhook with `max_connections=1` or the same queue. |
| Fan-out to browsers | Redis pub/sub | Each instance holds some of the sockets. A missed pub/sub message is harmless: resume reads from the DB by `last_seq`. |

Also: Telegram webhook instead of polling, authentication for the web UI, rate limiting, media support, and a "disconnect participant" button.

---

## Assignment

_The original assignment text, kept as received (its Docker section was cut off in the original repository)._

### Senior Full-Stack Developer – Home Assignment

### Overview

This assignment simulates a simplified real-world system that displays a web-based interface of a chat between a Telegram bot and a remote participant.

The system should consist:
- A **React.js frontend** that displays a chat UI
- A **FastAPI (Python) backend** that manages a Telegram bot
- A single Telegram chat connection that acts as the remote participant

The focus of this assignment is on **architecture, clarity, and engineering judgment**, not visual polish or feature overload.

---

### Functional Requirements

#### 1. Chat UI

The frontend must display a chat interface between the bot and the remote Telegram participant. 
It should include:

- A list of messages (incoming and outgoing)
- A text input for sending messages
- A send button (or Enter key support)

Each message must include:
- Message text
- Timestamp (time the message was sent)

The chat may not be consistent and may only present messages from the current session.

Incoming and outgoing messages should be visually distinguishable.

---

#### 2. Telegram Bot Integration (Backend)

- The backend must manage a **Telegram bot instance**
- The bot must accept **only one active Telegram chat connection** (Should only accept interacting with one remote participant)
- Messages flow as follows:
  - Messages sent from the frontend are forwarded to the connected Telegram chat
  - Messages received by the Telegram bot are forwarded to the frontend as incoming messages

State management, concurrency handling, and message ordering should be handled safely.

---

#### 3. Backend Configuration State

- The Telegram bot token should be configured manualy in the backend.

---

### Communication Between Frontend and Backend

- The communication mechanism is up to you  
  (e.g. WebSockets, long polling, Server-Sent Events, etc.)
- The chosen approach should be justified by simplicity and correctness
- Real-time or near-real-time behavior is expected

---

### Technical Requirements

- **Frontend:** React.js
- **Backend:** FastAPI (Python)
- Code should be clean, readable, and maintainable
- Assumptions and trade-offs should be documented

---

### Prerequisites

- Node.js (18+ recommended)
- Python 3.10+
- Telegram account
- Telegram Bot Token (created via BotFather)

---

### Setup Instructions

#### Backend

Bash
```bash
cd backend
python -m venv venv
source venv/bin/activate
uvicorn app.main:app --reload
```

#### Frontend

Bash
```bash
cd frontend
npm install
npm run dev
```

#### Docker Setup (Optional but Recommended)

For a streamlined setup, you can run the entire system using Docker Compose. This ensures all dependencies and environment configurations are handled automatically.

1. **Build and start the containers:**
   ```bash
   docker-compose up --build
```
