# Telegram Bot Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A React chat UI that acts as the console of a Telegram bot, talking over a WebSocket to a single FastAPI process that serves exactly one Telegram participant at a time.

**Architecture:** One FastAPI process runs `python-telegram-bot` long polling inside its lifespan. Every state change (claim, release, incoming, outgoing, client connect) goes through `ChatService` under one `asyncio.Lock`; `MessageStore` assigns `seq`, `ConnectionHub` fans frames out through per-connection queues. The React app keeps state in a pure reducer fed by a `useChatSocket` hook.

**Tech Stack:** Python 3.10+, FastAPI, pydantic v2 + pydantic-settings, python-telegram-bot ≥ 21 (asyncio), pytest + pytest-asyncio + httpx; React 18 (JavaScript) + Vite 5 + Vitest; Docker Compose + nginx.

**Spec:** `docs/superpowers/specs/2026-11-24-telegram-chat-design.md` (read it before any task; section numbers below refer to it).

## Global Constraints

- Backend runs as **one process, one uvicorn worker** (Telegram polling allows a single consumer per token).
- `TELEGRAM_BOT_TOKEN` comes from env / `.env`; missing token → fail fast at startup with a clear error. Never commit `.env`.
- Message text: trimmed, non-empty, **≤ 4096 characters**.
- Timestamps are ISO 8601 **UTC**; incoming = Telegram `message.date`, outgoing = `date` of the `Message` returned by `sendMessage`.
- `seq` is strictly increasing for the life of the process and is the only ordering key (it is **not** reset when a session changes).
- An outgoing message is stored/broadcast **only after** Telegram confirms it; the Telegram call happens while holding the service lock.
- Private chats only; edited messages ignored; pending updates dropped on startup.
- No real Telegram calls in tests — tests use `FakeGateway`.
- Frontend stays JavaScript and connects to **same-origin `/ws`** (Vite proxy in dev, nginx in Docker).
- Bot reply copy (exact):
  - `REPLY_CONNECTED = "Connected. Messages you send here will appear in the web chat."`
  - `REPLY_ALREADY_CONNECTED = "You're already connected."`
  - `REPLY_BUSY = "This bot is already in a conversation with someone else. Try again later."`
  - `REPLY_NOT_CONNECTED = "You're not connected. Send /start to connect."`
  - `REPLY_DISCONNECTED = "Disconnected."`
  - `REPLY_SEND_START = "Send /start to connect."`
  - `REPLY_TEXT_ONLY = "Only text messages are supported."`
- Error codes (exact): `NO_PARTICIPANT`, `INVALID_MESSAGE`, `TELEGRAM_ERROR`, `BAD_REQUEST`.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_015xj8wxJHziesua3qAV6o5z
  ```

## Review Focus

1. **Hebrew / emoji text and the 4096 boundary** — a 4096-char Hebrew+emoji message is accepted and round-trips unchanged; 4097 is rejected with `INVALID_MESSAGE`. (Task 1, Task 6)
2. **Garbage on the socket** — non-JSON, unknown `type`, or `send` before `hello` yields `BAD_REQUEST` without crashing the server; garbage *after* hello keeps the connection open. (Task 6)
3. **Two tabs open** — an outgoing message sent from tab 1 appears in tab 2 as a normal confirmed outgoing bubble (no stray pending). (Task 6, Task 8)
4. **Backend restarted while a tab is open** — the tab reconnects with an old `session_id`; the server has none (or a different one); the tab replaces its list with the snapshot (possibly empty) instead of mixing old and new `seq` values. (Task 5, Task 8)
5. **Participant without a Telegram username** — status carries `username: null`; the status bar shows the first name instead of `@null`. (Task 3, Task 9)

---

## File Map

```
backend/
  pytest.ini                         # asyncio_mode = auto
  requirements.txt                   # runtime deps
  requirements-dev.txt               # -r requirements.txt + test deps
  Dockerfile
  app/
    config.py                        # Settings, get_settings()
    models.py                        # pydantic models, frames, frame builders
    store.py                         # MessageStore
    session.py                       # SessionState + ClaimResult/ReleaseResult
    hub.py                           # Connection, ConnectionHub
    service.py                       # ChatService, gateway protocol + errors, reply copy
    telegram_gateway.py              # TelegramGateway (PTB)
    ws.py                            # /ws router
    main.py                          # create_app(), lifespan, /health, /status
  tests/
    conftest.py                      # FakeGateway, FakeWebSocket, fixtures
    test_config.py  test_models.py  test_store.py  test_session.py
    test_hub.py  test_service.py  test_ws.py  test_telegram_gateway.py
frontend/
  vite.config.js                     # proxy + vitest config (modify)
  package.json                       # add vitest, "test" script (modify)
  Dockerfile  nginx.conf
  src/App.jsx  src/index.css         # modify
  src/chat/chatReducer.js  src/chat/chatReducer.test.js
  src/chat/useChatSocket.js
  src/components/StatusBar.jsx  MessageList.jsx  MessageInput.jsx
docker-compose.yml  .env.example  README.md   # repo root
```

---

### Task 1: Backend scaffold, config and models

**Files:**
- Create: `backend/requirements.txt`, `backend/requirements-dev.txt`, `backend/pytest.ini`, `backend/app/config.py`, `backend/app/models.py`, `backend/tests/__init__.py`, `backend/tests/test_config.py`, `backend/tests/test_models.py`

**Interfaces:**
- Produces:
  - `config.Settings(BaseSettings)`: `telegram_bot_token: SecretStr` (required), `cors_origins: list[str] = ["http://localhost:5173"]`, `ws_queue_size: int = 100`; `model_config` reads env file `("../.env", ".env")`, `extra="ignore"`. `get_settings() -> Settings` (cached).
  - `models.Direction = Literal["incoming", "outgoing"]`, `models.ErrorCode = Literal["NO_PARTICIPANT","INVALID_MESSAGE","TELEGRAM_ERROR","BAD_REQUEST"]`
  - `models.Participant(BaseModel)`: `chat_id: int = Field(exclude=True)`, `first_name: str`, `username: str | None = None`
  - `models.Message(BaseModel)`: `seq: int`, `id: str`, `session_id: str`, `direction: Direction`, `text: str`, `timestamp: datetime` (serialized as UTC ISO 8601 ending in `Z` or `+00:00`), `client_id: str | None = None`
  - `models.Status(BaseModel)`: `connected: bool`, `session_id: str | None`, `participant: Participant | None`
  - `models.HelloFrame`: `type: Literal["hello"]`, `session_id: str | None = None`, `last_seq: int = Field(0, ge=0)`
  - `models.SendFrame`: `type: Literal["send"]`, `client_id: str = Field(min_length=1, max_length=64)`, `text: str` — validator strips and enforces 1..4096 chars
  - `models.parse_client_frame(data: object) -> HelloFrame | SendFrame` (discriminated union on `type`, raises `pydantic.ValidationError`)
  - Frame builders returning JSON-ready dicts (`model_dump(mode="json")`): `snapshot_frame(status: Status, messages: list[Message]) -> dict`, `message_frame(msg: Message) -> dict`, `status_frame(status: Status) -> dict`, `error_frame(code: ErrorCode, detail: str, client_id: str | None = None) -> dict` — shapes exactly as spec §6.5.

- [ ] **Step 1: Create requirements and pytest config**

`requirements.txt`: `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings>=2`, `python-telegram-bot>=21`. `requirements-dev.txt`: `-r requirements.txt`, `pytest`, `pytest-asyncio`, `httpx`. `pytest.ini`: `[pytest]` / `asyncio_mode = auto` / `testpaths = tests`.

Run: `cd backend && python -m venv venv && . venv/bin/activate && pip install -r requirements-dev.txt`
Expected: installs without errors.

- [ ] **Step 2: Write failing tests**

```python
# tests/test_config.py
def test_missing_token_fails_fast(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)

def test_token_and_defaults(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    s = Settings(_env_file=None)
    assert s.telegram_bot_token.get_secret_value() == "123:abc"
    assert s.cors_origins == ["http://localhost:5173"]
    assert s.ws_queue_size == 100
```

```python
# tests/test_models.py
def test_send_frame_strips_and_accepts_4096_hebrew_emoji():
    text = ("שלום🙂" * 820)[:4096]
    f = parse_client_frame({"type": "send", "client_id": "c1", "text": f"  {text}  "})
    assert f.text == text

@pytest.mark.parametrize("text", ["", "   ", "x" * 4097])
def test_send_frame_rejects_invalid_text(text):
    with pytest.raises(ValidationError):
        parse_client_frame({"type": "send", "client_id": "c1", "text": text})

def test_unknown_type_rejected():
    with pytest.raises(ValidationError):
        parse_client_frame({"type": "nope"})

def test_hello_defaults():
    f = parse_client_frame({"type": "hello"})
    assert f.session_id is None and f.last_seq == 0

def test_status_hides_chat_id_and_allows_no_username():
    s = Status(connected=True, session_id="s1",
               participant=Participant(chat_id=42, first_name="Dana", username=None))
    frame = status_frame(s)
    assert frame == {"type": "status", "status": {"connected": True, "session_id": "s1",
                     "participant": {"first_name": "Dana", "username": None}}}

def test_message_frame_is_json_ready_utc():
    m = Message(seq=1, id="m1", session_id="s1", direction="incoming", text="hi",
                timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc))
    frame = message_frame(m)
    assert frame["type"] == "message"
    assert frame["message"]["timestamp"].startswith("2026-01-01T12:00:00")
    assert frame["message"]["client_id"] is None

def test_error_frame():
    assert error_frame("NO_PARTICIPANT", "x", "c1") == {
        "type": "error", "client_id": "c1", "code": "NO_PARTICIPANT", "detail": "x"}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && pytest -q tests/test_config.py tests/test_models.py`
Expected: FAIL (ImportError).

- [ ] **Step 4: Implement `config.py` and `models.py` per the Interfaces block.**

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest -q tests/test_config.py tests/test_models.py` → all PASS.

- [ ] **Step 6: Commit** — `feat(backend): config and protocol models`

---

### Task 2: MessageStore

**Files:**
- Create: `backend/app/store.py`, `backend/tests/test_store.py`

**Interfaces:**
- Consumes: `models.Message`, `models.Direction`
- Produces: `class MessageStore` (synchronous; callers serialize access):
  - `session_id: str | None` (read-only property)
  - `reset(session_id: str) -> None` — clears messages, sets session id; **does not** reset the seq counter
  - `append(direction: Direction, text: str, timestamp: datetime, client_id: str | None = None) -> Message` — `seq` = previous + 1 (first is 1), `id` = uuid4 hex; raises `RuntimeError` if no session
  - `since(last_seq: int) -> list[Message]` — messages with `seq > last_seq`, ascending
  - `all() -> list[Message]`

- [ ] **Step 1: Write failing tests**

```python
def test_append_requires_session():
    with pytest.raises(RuntimeError):
        MessageStore().append("incoming", "hi", NOW)

def test_seq_increases_and_since():
    s = MessageStore(); s.reset("s1")
    a = s.append("incoming", "a", NOW); b = s.append("outgoing", "b", NOW, client_id="c1")
    assert (a.seq, b.seq) == (1, 2) and b.client_id == "c1" and a.session_id == "s1"
    assert [m.seq for m in s.since(1)] == [2]

def test_reset_clears_but_seq_keeps_growing():
    s = MessageStore(); s.reset("s1"); s.append("incoming", "a", NOW)
    s.reset("s2")
    assert s.all() == [] and s.session_id == "s2"
    assert s.append("incoming", "b", NOW).seq == 2
```

- [ ] **Step 2: Run** `pytest -q tests/test_store.py` → FAIL.
- [ ] **Step 3: Implement `MessageStore`.**
- [ ] **Step 4: Run** `pytest -q tests/test_store.py` → PASS.
- [ ] **Step 5: Commit** — `feat(backend): in-memory message store`

---

### Task 3: Session rules

**Files:**
- Create: `backend/app/session.py`, `backend/tests/test_session.py`

**Interfaces:**
- Consumes: `models.Participant`, `models.Status`
- Produces:
  - `class ClaimResult(Enum)`: `CLAIMED`, `ALREADY_OWNER`, `BUSY`
  - `class ReleaseResult(Enum)`: `RELEASED`, `NOT_CONNECTED`, `BUSY`
  - `class SessionState` (pure, synchronous):
    - `owner: Participant | None`, `session_id: str | None` (latest; kept after release)
    - `claim(p: Participant) -> ClaimResult` — on `CLAIMED` sets owner and a new uuid4 `session_id`
    - `release(chat_id: int) -> ReleaseResult` — `RELEASED` only for the owner; `BUSY` if someone else owns; `NOT_CONNECTED` if free
    - `is_owner(chat_id: int) -> bool`
    - `status() -> Status` — `connected = owner is not None`, `session_id` = latest, `participant` = owner

- [ ] **Step 1: Write failing tests** — one test per row of spec §6.2 that involves claim/release:

```python
A = Participant(chat_id=1, first_name="A", username="a")
B = Participant(chat_id=2, first_name="B", username=None)

def test_claim_free():
    s = SessionState(); assert s.claim(A) is ClaimResult.CLAIMED
    assert s.is_owner(1) and s.session_id

def test_claim_again_same_owner_keeps_session():
    s = SessionState(); s.claim(A); sid = s.session_id
    assert s.claim(A) is ClaimResult.ALREADY_OWNER and s.session_id == sid

def test_claim_busy():
    s = SessionState(); s.claim(A)
    assert s.claim(B) is ClaimResult.BUSY and s.is_owner(1)

def test_release_rules():
    s = SessionState()
    assert s.release(1) is ReleaseResult.NOT_CONNECTED
    s.claim(A)
    assert s.release(2) is ReleaseResult.BUSY
    sid = s.session_id
    assert s.release(1) is ReleaseResult.RELEASED
    st = s.status()
    assert st.connected is False and st.session_id == sid and st.participant is None

def test_new_claim_after_release_gets_new_session():
    s = SessionState(); s.claim(A); first = s.session_id; s.release(1)
    s.claim(B); assert s.session_id != first and s.status().participant.username is None
```

- [ ] **Step 2: Run** `pytest -q tests/test_session.py` → FAIL.
- [ ] **Step 3: Implement `session.py`.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `feat(backend): single-participant session rules`

---

### Task 4: ConnectionHub

**Files:**
- Create: `backend/app/hub.py`, `backend/tests/conftest.py` (add `FakeWebSocket`), `backend/tests/test_hub.py`

**Interfaces:**
- Produces:
  - `class Connection`: wraps a Starlette `WebSocket`; `queue: asyncio.Queue[dict]` (maxsize from hub); `start() -> None` launches a writer task that loops `await ws.send_json(frame)`; `async close(code: int = 1013) -> None` cancels the writer and closes the socket (ignore errors if already closed); `closed: bool`
  - `class ConnectionHub(queue_size: int = 100)`:
    - `add(ws: WebSocket) -> Connection` — creates, registers, starts a `Connection`
    - `remove(conn: Connection) -> None` — idempotent
    - `send(conn: Connection, frame: dict) -> None` — `put_nowait` to one connection
    - `broadcast(frame: dict) -> None` — `put_nowait` to every registered connection; on `QueueFull` schedule `conn.close()` and `remove(conn)` (other connections unaffected)
    - `__len__() -> int`
  - `tests/conftest.py`: `class FakeWebSocket` with `sent: list[dict]`, `async send_json(data)`, `async close(code=1000)`, `closed_code: int | None`, optional `block: asyncio.Event` that `send_json` awaits (to simulate a slow client).

- [ ] **Step 1: Write failing tests**

```python
async def test_broadcast_reaches_all_in_order():
    hub = ConnectionHub(); a, b = FakeWebSocket(), FakeWebSocket()
    hub.add(a); hub.add(b)
    for i in range(3): hub.broadcast({"n": i})
    await drain()
    assert [f["n"] for f in a.sent] == [0, 1, 2] == [f["n"] for f in b.sent]

async def test_slow_client_is_dropped_others_unaffected():
    hub = ConnectionHub(queue_size=2)
    slow = FakeWebSocket(block=asyncio.Event()); fast = FakeWebSocket()
    hub.add(slow); hub.add(fast)
    for i in range(5): hub.broadcast({"n": i})
    await drain()
    assert len(hub) == 1 and slow.closed_code == 1013
    assert [f["n"] for f in fast.sent] == [0, 1, 2, 3, 4]

async def test_send_targets_one_connection(): ...
async def test_remove_is_idempotent(): ...
```

(`drain()` = small helper in conftest that awaits `asyncio.sleep(0)` in a loop up to ~50 iterations.)

- [ ] **Step 2: Run** `pytest -q tests/test_hub.py` → FAIL.
- [ ] **Step 3: Implement `hub.py`.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `feat(backend): websocket connection hub with per-connection queues`

---

### Task 5: ChatService

**Files:**
- Create: `backend/app/service.py`, `backend/tests/test_service.py`
- Modify: `backend/tests/conftest.py` (add `FakeGateway`, `service` fixture)

**Interfaces:**
- Consumes: `MessageStore` (Task 2), `SessionState`/`ClaimResult`/`ReleaseResult` (Task 3), `ConnectionHub`/`Connection` (Task 4), models (Task 1)
- Produces (in `service.py`):
  - Reply constants exactly as in Global Constraints.
  - `class GatewayError(Exception)`, `class GatewayForbidden(GatewayError)`
  - `class TelegramGateway(Protocol)`: `async send_text(chat_id: int, text: str) -> datetime`
  - `class ChatError(Exception)`: `code: ErrorCode`, `detail: str`
  - `class ChatService(store: MessageStore, session: SessionState, hub: ConnectionHub, gateway: TelegramGateway)` — every public async method runs under one `asyncio.Lock`:
    - `async connect(ws: WebSocket, hello: HelloFrame) -> Connection` — registers with the hub **and** enqueues the snapshot inside the lock (so no broadcast can slip ahead of it). Snapshot messages: `store.since(hello.last_seq)` if `hello.session_id == store.session_id` and not None, else `store.all()`.
    - `async disconnect(conn: Connection) -> None`
    - `async on_start(p: Participant) -> str` — `CLAIMED` → `store.reset(new session_id)`, broadcast status, return `REPLY_CONNECTED`; `ALREADY_OWNER` → `REPLY_ALREADY_CONNECTED`; `BUSY` → `REPLY_BUSY`
    - `async on_stop(chat_id: int) -> str` — `RELEASED` → broadcast status, `REPLY_DISCONNECTED`; `NOT_CONNECTED` → `REPLY_NOT_CONNECTED`; `BUSY` → `REPLY_BUSY`
    - `async on_text(chat_id: int, text: str, sent_at: datetime) -> str | None` — owner: append incoming + broadcast message, return `None`; free bot → `REPLY_SEND_START`; other user → `REPLY_BUSY`
    - `async on_non_text(chat_id: int) -> str | None` — owner → `REPLY_TEXT_ONLY`; free → `REPLY_SEND_START`; other → `REPLY_BUSY`
    - `async send(client_id: str, text: str) -> Message` — no owner → `ChatError("NO_PARTICIPANT", ...)`; `await gateway.send_text(owner.chat_id, text)`; `GatewayForbidden` → release session, broadcast status, raise `ChatError("TELEGRAM_ERROR", ...)`; other `GatewayError` → `ChatError("TELEGRAM_ERROR", ...)`, nothing stored; success → append outgoing with Telegram's timestamp and `client_id`, broadcast, return it
    - `status() -> Status`
    - public attributes `store`, `session`, `hub`, `gateway` (tests read them)
  - `tests/conftest.py`: `class FakeGateway` with `sent: list[tuple[int, str]]`, `fail_with: Exception | None`, `delay: float = 0`, `send_text` returns `datetime.now(timezone.utc)`, plus no-op `attach(service)`, `async start()`, `async stop()` (Task 6 uses it as the app's gateway); fixtures `gateway` and `service` building the real store/session/hub with `FakeGateway`.

- [ ] **Step 1: Write failing tests**

```python
async def test_send_without_participant(service):
    with pytest.raises(ChatError) as e: await service.send("c1", "hi")
    assert e.value.code == "NO_PARTICIPANT"

async def test_full_session_table(service): ...  # every cell of spec §6.2 via on_start/on_stop/on_text/on_non_text, asserting exact reply constants and that non-owner text is never stored

async def test_concurrent_claims_one_winner(service):
    replies = await asyncio.gather(*(service.on_start(Participant(chat_id=i, first_name=str(i))) for i in range(10)))
    assert replies.count(REPLY_CONNECTED) == 1 and replies.count(REPLY_BUSY) == 9

async def test_outgoing_stored_only_after_gateway_success(service, gateway):
    await service.on_start(A); gateway.fail_with = GatewayError("boom")
    with pytest.raises(ChatError) as e: await service.send("c1", "hi")
    assert e.value.code == "TELEGRAM_ERROR" and service.store.all() == []

async def test_forbidden_releases_session(service, gateway):
    await service.on_start(A); gateway.fail_with = GatewayForbidden("blocked")
    with pytest.raises(ChatError): await service.send("c1", "hi")
    assert service.status().connected is False

async def test_concurrent_sends_keep_order(service, gateway):
    await service.on_start(A); gateway.delay = 0.01
    await asyncio.gather(*(service.send(f"c{i}", f"m{i}") for i in range(5)))
    texts = [t for _, t in gateway.sent]
    assert [m.text for m in service.store.all()] == texts
    assert [m.seq for m in service.store.all()] == sorted(m.seq for m in service.store.all())

async def test_new_claim_resets_history(service): ...  # A claims, message, A stops (history kept), B claims → store empty, new session_id

async def test_connect_snapshot_resume_rules(service):
    # same session + last_seq → delta; different/None session → all; server never claimed → empty list
    ...

async def test_connect_after_restart_with_stale_session(service):
    ws = FakeWebSocket()
    await service.connect(ws, HelloFrame(type="hello", session_id="old", last_seq=57))
    await drain()
    assert ws.sent[0]["type"] == "snapshot" and ws.sent[0]["messages"] == []
    assert ws.sent[0]["status"]["session_id"] is None
```

- [ ] **Step 2: Run** `pytest -q tests/test_service.py` → FAIL.
- [ ] **Step 3: Implement `service.py`.**
- [ ] **Step 4: Run** `pytest -q` (whole backend suite) → PASS.
- [ ] **Step 5: Commit** — `feat(backend): chat service with single lock, ordering and session rules`

---

### Task 6: WebSocket endpoint and app wiring

**Files:**
- Create: `backend/app/ws.py`, `backend/tests/test_ws.py`
- Modify: `backend/app/main.py` (replace contents)

**Interfaces:**
- Consumes: `ChatService` (Task 5), `parse_client_frame`, frame builders (Task 1), `Settings` (Task 1)
- Produces:
  - `main.create_app(settings: Settings | None = None, gateway_factory: Callable[[Settings], Any] | None = None) -> FastAPI` and module-level `app = create_app()` built lazily so importing in tests doesn't require a token (use `create_app` in tests; `app` for uvicorn).
  - The object returned by `gateway_factory(settings)` must provide `send_text`, `attach(service: ChatService) -> None`, `async start() -> None`, `async stop() -> None`. Default factory: `TelegramGateway(settings.telegram_bot_token.get_secret_value())` (Task 7).
  - Lifespan builds store/session/hub(`settings.ws_queue_size`)/gateway/service, calls `gateway.attach(service)`, `await gateway.start()`, stores `app.state.service`; on shutdown `await gateway.stop()`.
  - Routes: `GET /health` → `{"status": "ok"}`; `GET /status` → `service.status()` JSON; `WS /ws` from `ws.router`. CORS from `settings.cors_origins`.
  - `ws.py` protocol handling:
    - accept; first frame must parse as `HelloFrame`, else send `error_frame("BAD_REQUEST", ...)` and close with code 1008.
    - then loop on `receive_text()`: invalid JSON / unknown type / second hello → `BAD_REQUEST` (connection stays open); `send` with invalid text → `INVALID_MESSAGE` with that `client_id` (extract it from the raw dict when present); valid `send` → `await service.send(...)`; `ChatError` → `error_frame(e.code, e.detail, client_id)` to this connection only.
    - on disconnect → `service.disconnect(conn)`.
  - `tests/conftest.py`: fixture `client` = `with TestClient(create_app(Settings(telegram_bot_token="x", _env_file=None), gateway_factory=lambda s: FakeGateway())) as c: yield c` (the `with` runs the lifespan and provides `c.portal` for calling async service methods); the gateway is `client.app.state.service.gateway`.

- [ ] **Step 1: Write failing tests**

```python
def test_health_and_status(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/status").json() == {"connected": False, "session_id": None, "participant": None}

def test_hello_snapshot(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "hello"})
        assert ws.receive_json() == {"type": "snapshot",
            "status": {"connected": False, "session_id": None, "participant": None}, "messages": []}

def test_first_frame_must_be_hello(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "send", "client_id": "c1", "text": "hi"})
        assert ws.receive_json()["code"] == "BAD_REQUEST"
        # then the server closes (receive raises WebSocketDisconnect with code 1008)

def test_garbage_after_hello_keeps_connection(client):
    # send "not json" → BAD_REQUEST; {"type":"x"} → BAD_REQUEST; then a valid send still gets a reply (NO_PARTICIPANT)

def test_send_without_participant(client): ...   # error NO_PARTICIPANT with client_id "c1"

@pytest.mark.parametrize("text", ["   ", "x" * 4097])
def test_invalid_text(client, text): ...         # error INVALID_MESSAGE with client_id "c1"

def test_send_broadcasts_to_two_tabs(client):
    # claim via client.portal.call(service.on_start, A); two sockets hello;
    # tab1 sends {"client_id":"c1","text":"שלום 🙂"} → both receive message frame with client_id "c1",
    # direction "outgoing", same seq, text unchanged; gateway.sent == [(1, "שלום 🙂")]

def test_resume_delta(client): ...               # hello with current session_id + last_seq returns only newer messages
```

- [ ] **Step 2: Run** `pytest -q tests/test_ws.py` → FAIL.
- [ ] **Step 3: Implement `ws.py` and rewrite `main.py`.**
- [ ] **Step 4: Run** `pytest -q` → PASS.
- [ ] **Step 5: Commit** — `feat(backend): websocket protocol endpoint and app factory`

---

### Task 7: Telegram gateway

**Files:**
- Create: `backend/app/telegram_gateway.py`, `backend/tests/test_telegram_gateway.py`

**Interfaces:**
- Consumes: `ChatService` callbacks, `GatewayError`/`GatewayForbidden` (Task 5), `Participant` (Task 1)
- Produces: `class TelegramGateway(token: str)`:
  - `attach(service: ChatService) -> None` — builds the PTB `Application` (`ApplicationBuilder().token(token).build()`; default sequential update processing) and registers, all with `filters.ChatType.PRIVATE`:
    - `CommandHandler("start", ...)` → `service.on_start(participant_from(update))`
    - `CommandHandler("stop", ...)` → `service.on_stop(chat_id)`
    - `MessageHandler(filters.TEXT, ...)` → `service.on_text(chat_id, message.text, message.date)` (other `/commands` arrive here as text)
    - `MessageHandler(~filters.TEXT, ...)` → `service.on_non_text(chat_id)`
    - a non-`None` return value is sent back with `update.message.reply_text(reply)`
  - `async start()` — `initialize()`, `start()`, `updater.start_polling(drop_pending_updates=True, allowed_updates=["message"])` (this also ignores edited messages)
  - `async stop()` — `updater.stop()`, `stop()`, `shutdown()`
  - `async send_text(chat_id: int, text: str) -> datetime` — `bot.send_message`, returns `msg.date` (UTC); maps `telegram.error.Forbidden` → `GatewayForbidden`, any other `telegram.error.TelegramError` (incl. `TimedOut`, `NetworkError`) → `GatewayError`
  - module function `participant_from(update) -> Participant` (`effective_chat.id`, `effective_user.first_name`, `effective_user.username`)

- [ ] **Step 1: Write failing tests** (no network: call the handler callbacks directly with `SimpleNamespace` updates and a recording fake service; patch `gateway._app.bot.send_message` with an `AsyncMock`):

```python
async def test_start_handler_replies(...):  # reply_text called with service's return value
async def test_text_handler_forwards_date(...):  # on_text(42, "hi", <date>) and no reply when service returns None
async def test_participant_without_username(): assert participant_from(upd).username is None
async def test_send_text_maps_forbidden(...): side_effect=telegram.error.Forbidden("x") → GatewayForbidden
async def test_send_text_maps_timeout(...): side_effect=telegram.error.TimedOut() → GatewayError
async def test_send_text_returns_telegram_date(...)
```

Handlers are exposed as methods (`_on_start`, `_on_stop`, `_on_text`, `_on_non_text`) so tests can call them.

- [ ] **Step 2: Run** `pytest -q tests/test_telegram_gateway.py` → FAIL.
- [ ] **Step 3: Implement `telegram_gateway.py`.**
- [ ] **Step 4: Run** `pytest -q` → PASS.
- [ ] **Step 5: Manual smoke (needs a real token):** `TELEGRAM_BOT_TOKEN=... uvicorn app.main:app` from `backend/`; send `/start` from Telegram → `curl localhost:8000/status` shows `connected: true`. If no token is available to the implementer, skip and note it for the final check.
- [ ] **Step 6: Commit** — `feat(backend): python-telegram-bot gateway`

---

### Task 8: Frontend chat reducer

**Files:**
- Create: `frontend/src/chat/chatReducer.js`, `frontend/src/chat/chatReducer.test.js`
- Modify: `frontend/package.json` (devDependency `vitest`, script `"test": "vitest run"`), `frontend/vite.config.js` (add `test: { environment: "node" }`)

**Interfaces:**
- Produces (`chatReducer.js`):
  - `initialState = { socket: "connecting", status: { connected: false, session_id: null, participant: null }, messages: [], pending: [], lastError: null }`
  - `chatReducer(state, action) -> state`, actions:
    - `{ type: "socket", value: "connecting" | "open" | "reconnecting" }`
    - `{ type: "send", client_id, text, timestamp }` → append `{ client_id, text, timestamp, state: "pending" }` to `pending`
    - `{ type: "frame", frame }` for server frames:
      - `snapshot`: if `frame.status.session_id !== state.status.session_id` → `messages = frame.messages`; else merge (dedupe by `seq`); drop pending whose `client_id` is now in messages; set `status`
      - `message`: insert by `seq` (dedupe), remove pending with same `client_id`
      - `status`: if new `session_id` is non-null and differs from current → clear `messages`; set `status`
      - `error`: with `client_id` → that pending item gets `state: "failed", error: detail`; without → `lastError = detail`
  - `selectTimeline(state) -> Array<{ key, text, timestamp, direction, state: "confirmed" | "pending" | "failed", error? }>` — confirmed messages sorted by `seq`, then pending/failed (direction `"outgoing"`)
  - `selectResumeInfo(state) -> { session_id, last_seq }` — `last_seq` = max `seq` or 0

- [ ] **Step 1: Write failing tests** (`vitest`):

```js
test("pending → confirmed by client_id", ...)          // send then message frame with same client_id → timeline has one confirmed outgoing
test("error marks pending as failed", ...)              // detail is kept
test("message from another tab is just appended", ...)  // message with unknown client_id → confirmed, pending untouched
test("out-of-order frames render by seq; duplicates ignored", ...)
test("snapshot with same session merges", ...)
test("snapshot with different session replaces list", ...)   // includes server session null after restart → messages []
test("status with new session clears messages", ...)
test("selectResumeInfo", ...)
```

- [ ] **Step 2: Run** `cd frontend && npm install && npm test` → FAIL.
- [ ] **Step 3: Implement `chatReducer.js`.**
- [ ] **Step 4: Run** `npm test` → PASS.
- [ ] **Step 5: Commit** — `feat(frontend): chat state reducer`

---

### Task 9: Frontend socket hook, components and dev proxy

**Files:**
- Create: `frontend/src/chat/useChatSocket.js`, `frontend/src/components/StatusBar.jsx`, `frontend/src/components/MessageList.jsx`, `frontend/src/components/MessageInput.jsx`
- Modify: `frontend/src/App.jsx`, `frontend/src/index.css`, `frontend/vite.config.js`

**Interfaces:**
- Consumes: `chatReducer`, `initialState`, `selectTimeline`, `selectResumeInfo` (Task 8)
- Produces:
  - `useChatSocket(url?: string) -> { state, timeline, send(text: string): void }` — default url `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`; on open sends `{type:"hello", ...selectResumeInfo(state)}` (read current state via a ref); dispatches every parsed frame; reconnect delays `[1000, 2000, 4000, 8000, 10000]` ms (last repeats), reset after a successful open; `send` creates `client_id = crypto.randomUUID()`, dispatches `send` then writes the frame; cleans up socket and timers on unmount (StrictMode double-mount safe).
  - `StatusBar({ socket, status })` — socket text: connecting / connected / reconnecting; participant text: `Connected to @username`, or `Connected to <first_name>` when `username` is null, or `Waiting for a Telegram user to send /start`.
  - `MessageList({ items })` — one `.chat-message.incoming|outgoing` per item, `pending`/`failed` modifier classes, shows `error` under failed bubbles, timestamp via `toLocaleTimeString()`; auto-scrolls to bottom when `items.length` changes.
  - `MessageInput({ disabled, onSend })` — Enter sends; trims; ignores empty; clears on send; disabled when socket not open or `!status.connected`.
  - `vite.config.js` `server.proxy`: `"/ws": { target: "ws://localhost:8000", ws: true }`, `"/status": "http://localhost:8000"`.
  - CSS: `.chat-message.pending .chat-bubble { opacity: .6 }`, `.chat-message.failed .chat-bubble` red border, `.chat-error` small red text, status bar styles.

- [ ] **Step 1: Implement hook and components; wire `App.jsx`.**
- [ ] **Step 2: Build check** — `npm run build` → succeeds; `npm test` still PASS.
- [ ] **Step 3: Manual check with the backend running** (fake or real token): UI shows "Waiting for a Telegram user…", send is disabled; stop the backend → status shows reconnecting; restart → reconnects.
- [ ] **Step 4: Commit** — `feat(frontend): websocket chat UI`

---

### Task 10: Docker

**Files:**
- Create: `backend/Dockerfile`, `frontend/Dockerfile`, `frontend/nginx.conf`, `docker-compose.yml`, `.env.example`

**Interfaces:**
- `.env.example`: `TELEGRAM_BOT_TOKEN=` and a commented `# CORS_ORIGINS=["http://localhost:5173"]`.
- `backend/Dockerfile`: `python:3.12-slim`, install `requirements.txt`, `CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--workers","1"]`.
- `frontend/Dockerfile`: stage 1 `node:20-alpine` `npm ci && npm run build`; stage 2 `nginx:alpine` with `dist/` and `nginx.conf`.
- `nginx.conf`: serve SPA; `location /ws` → `proxy_pass http://backend:8000;` with `proxy_http_version 1.1`, `Upgrade`/`Connection "upgrade"` headers and `proxy_read_timeout 3600s`; `location /status` → backend.
- `docker-compose.yml`: `backend` (`env_file: .env`, healthcheck `python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')"`), `frontend` (`ports: ["8080:80"]`, `depends_on: backend: condition: service_healthy`).

- [ ] **Step 1: Write the files.**
- [ ] **Step 2: Verify** — `docker compose config` → valid. If Docker is available: `docker compose build` → succeeds. (If not available to the implementer, note it for the final check.)
- [ ] **Step 3: Commit** — `chore: docker compose setup`

---

### Task 11: README

**Files:**
- Modify: `README.md` (repo root)

- [ ] **Step 1: Write the README** per spec §6.11: solution docs first (overview, quick start with Docker, local dev, how to try it: BotFather → token → `.env` → open UI → `/start` in Telegram, running tests), text architecture diagram, WebSocket protocol (frames from spec §6.5), session rules table (§6.2), assumptions and judgment calls (all items marked *my call* in the spec, plus §6.6.1), trade-offs (WebSocket vs SSE, polling vs webhook, in-memory + single process, lock held during the Telegram call, send-timeout ambiguity), production notes (the multi-instance list from spec §6.11). The original assignment text moves under a final `## Assignment` heading, with the truncated Docker section left as-is there.
- [ ] **Step 2: Verify** every command in the README by running it (or note which need a real token / Docker).
- [ ] **Step 3: Commit** — `docs: README with setup, protocol, assumptions and trade-offs`

---

### Task 12: Final verification

- [ ] **Step 1:** `cd backend && pytest -q` → all PASS.
- [ ] **Step 2:** `cd frontend && npm test && npm run build` → PASS.
- [ ] **Step 3:** End-to-end with a real bot token (Alon runs this): `docker compose up --build`, open `http://localhost:8080`, send `/start` from Telegram account 1, exchange messages in both directions (including Hebrew + emoji), open a second tab, `/start` from Telegram account 2 → busy reply, `/stop` from account 1 → UI shows waiting, restart backend → open tab recovers.
- [ ] **Step 4:** Commit any fixes; branch ready for review.
