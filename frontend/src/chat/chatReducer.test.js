import { describe, expect, test } from "vitest";
import { chatReducer, initialState, selectResumeInfo, selectTimeline } from "./chatReducer";

const status = (session_id, connected = true) => ({
  connected,
  session_id,
  participant: connected ? { first_name: "Dana", username: "dana" } : null,
});

const msg = (seq, extra = {}) => ({
  seq,
  id: `m${seq}`,
  session_id: "s1",
  direction: "incoming",
  text: `t${seq}`,
  timestamp: "2026-01-01T12:00:00Z",
  client_id: null,
  ...extra,
});

const frame = (f) => ({ type: "frame", frame: f });
const run = (...actions) => actions.reduce(chatReducer, initialState);
const inSession = (...actions) =>
  run(frame({ type: "snapshot", status: status("s1"), messages: [] }), ...actions);

describe("chatReducer", () => {
  test("socket state", () => {
    expect(run({ type: "socket", value: "open" }).socket).toBe("open");
  });

  test("pending → confirmed by client_id", () => {
    const s = inSession(
      { type: "send", client_id: "c1", text: "hi", timestamp: "2026-01-01T12:00:00Z" },
      frame({ type: "message", message: msg(1, { direction: "outgoing", text: "hi", client_id: "c1" }) }),
    );
    const timeline = selectTimeline(s);
    expect(timeline).toHaveLength(1);
    expect(timeline[0]).toMatchObject({ text: "hi", direction: "outgoing", state: "confirmed" });
  });

  test("pending shows before confirmation", () => {
    const s = inSession({ type: "send", client_id: "c1", text: "hi", timestamp: "t" });
    expect(selectTimeline(s)).toEqual([
      { key: "c1", text: "hi", timestamp: "t", direction: "outgoing", state: "pending" },
    ]);
  });

  test("error marks pending as failed with detail", () => {
    const s = inSession(
      { type: "send", client_id: "c1", text: "hi", timestamp: "t" },
      frame({ type: "error", client_id: "c1", code: "TELEGRAM_ERROR", detail: "nope" }),
    );
    expect(selectTimeline(s)[0]).toMatchObject({ state: "failed", error: "nope" });
  });

  test("error without client_id is kept as lastError", () => {
    const s = inSession(frame({ type: "error", client_id: null, code: "BAD_REQUEST", detail: "bad" }));
    expect(s.lastError).toBe("bad");
  });

  test("message from another tab is just appended", () => {
    const s = inSession(
      { type: "send", client_id: "mine", text: "a", timestamp: "t" },
      frame({ type: "message", message: msg(1, { direction: "outgoing", client_id: "theirs" }) }),
    );
    expect(selectTimeline(s).map((m) => m.state)).toEqual(["confirmed", "pending"]);
  });

  test("out-of-order frames render by seq; duplicates ignored", () => {
    const s = inSession(
      frame({ type: "message", message: msg(2) }),
      frame({ type: "message", message: msg(1) }),
      frame({ type: "message", message: msg(2) }),
    );
    expect(selectTimeline(s).map((m) => m.text)).toEqual(["t1", "t2"]);
  });

  test("snapshot with same session merges", () => {
    const s = inSession(
      frame({ type: "message", message: msg(1) }),
      frame({ type: "snapshot", status: status("s1"), messages: [msg(1), msg(2)] }),
    );
    expect(selectTimeline(s).map((m) => m.text)).toEqual(["t1", "t2"]);
  });

  test("snapshot with different session replaces list", () => {
    const s = inSession(
      frame({ type: "message", message: msg(1) }),
      frame({ type: "snapshot", status: status("s2"), messages: [msg(5, { session_id: "s2" })] }),
    );
    expect(selectTimeline(s).map((m) => m.text)).toEqual(["t5"]);
  });

  test("server restarted with no session: snapshot empties the list", () => {
    const s = inSession(
      frame({ type: "message", message: msg(57) }),
      frame({ type: "snapshot", status: status(null, false), messages: [] }),
    );
    expect(selectTimeline(s)).toEqual([]);
    expect(s.status.connected).toBe(false);
  });

  test("status with new session clears messages; release keeps them", () => {
    let s = inSession(frame({ type: "message", message: msg(1) }));
    s = chatReducer(s, frame({ type: "status", status: status("s1", false) }));
    expect(selectTimeline(s)).toHaveLength(1);
    expect(s.status.connected).toBe(false);
    s = chatReducer(s, frame({ type: "status", status: status("s2") }));
    expect(selectTimeline(s)).toEqual([]);
  });

  test("selectResumeInfo", () => {
    expect(selectResumeInfo(initialState)).toEqual({ session_id: null, last_seq: 0 });
    const s = inSession(frame({ type: "message", message: msg(3) }), frame({ type: "message", message: msg(7) }));
    expect(selectResumeInfo(s)).toEqual({ session_id: "s1", last_seq: 7 });
  });
});
