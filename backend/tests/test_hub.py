import asyncio

from app.hub import ConnectionHub
from tests.conftest import FakeWebSocket, drain


async def test_broadcast_reaches_all_in_order():
    hub = ConnectionHub()
    a, b = FakeWebSocket(), FakeWebSocket()
    hub.add(a)
    hub.add(b)
    for i in range(3):
        hub.broadcast({"n": i})
    await drain()
    assert [f["n"] for f in a.sent] == [0, 1, 2]
    assert [f["n"] for f in b.sent] == [0, 1, 2]


async def test_slow_client_is_dropped_others_unaffected():
    hub = ConnectionHub(queue_size=2)
    slow = FakeWebSocket(block=asyncio.Event())
    fast = FakeWebSocket()
    hub.add(slow)
    hub.add(fast)
    for i in range(5):
        hub.broadcast({"n": i})
        await drain()  # events arrive one at a time; fast's writer keeps up
    assert len(hub) == 1
    assert slow.closed_code == 1013
    assert [f["n"] for f in fast.sent] == [0, 1, 2, 3, 4]


async def test_send_targets_one_connection():
    hub = ConnectionHub()
    a, b = FakeWebSocket(), FakeWebSocket()
    conn_a = hub.add(a)
    hub.add(b)
    hub.send(conn_a, {"only": "a"})
    await drain()
    assert a.sent == [{"only": "a"}] and b.sent == []


async def test_remove_is_idempotent():
    hub = ConnectionHub()
    conn = hub.add(FakeWebSocket())
    hub.remove(conn)
    hub.remove(conn)
    assert len(hub) == 0
    hub.broadcast({"n": 1})  # no registered connections: nothing to do
    await drain()


async def test_close_stops_writer():
    hub = ConnectionHub()
    ws = FakeWebSocket()
    conn = hub.add(ws)
    await conn.close()
    assert conn.closed and ws.closed_code == 1013
