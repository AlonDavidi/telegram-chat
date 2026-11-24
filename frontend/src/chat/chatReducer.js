// Pure chat state. The server is the source of truth for ordering (seq);
// the UI only adds optimistic "pending" bubbles until the server echoes them back.

const CONNECTION_LOST = "Connection lost before the server confirmed this message.";

export const initialState = {
  socket: "connecting", // "connecting" | "open" | "reconnecting"
  status: { connected: false, session_id: null, participant: null },
  messages: [], // confirmed, sorted by seq
  pending: [], // { client_id, text, timestamp, state: "pending" | "failed", error? }
  lastError: null,
};

function mergeBySeq(existing, incoming) {
  const bySeq = new Map(existing.map((m) => [m.seq, m]));
  for (const m of incoming) bySeq.set(m.seq, m);
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq);
}

function withoutConfirmed(pending, messages) {
  const confirmed = new Set(messages.map((m) => m.client_id).filter(Boolean));
  return pending.filter((p) => !confirmed.has(p.client_id));
}

function applyFrame(state, frame) {
  switch (frame.type) {
    case "snapshot": {
      const sameSession = frame.status.session_id === state.status.session_id;
      const messages = sameSession ? mergeBySeq(state.messages, frame.messages) : [...frame.messages];
      return {
        ...state,
        status: frame.status,
        messages,
        pending: withoutConfirmed(state.pending, messages),
      };
    }
    case "message": {
      const messages = mergeBySeq(state.messages, [frame.message]);
      return { ...state, messages, pending: withoutConfirmed(state.pending, messages) };
    }
    case "status": {
      const newSession =
        frame.status.session_id !== null && frame.status.session_id !== state.status.session_id;
      return { ...state, status: frame.status, messages: newSession ? [] : state.messages };
    }
    case "error": {
      if (!frame.client_id) return { ...state, lastError: frame.detail };
      return {
        ...state,
        pending: state.pending.map((p) =>
          p.client_id === frame.client_id ? { ...p, state: "failed", error: frame.detail } : p,
        ),
      };
    }
    default:
      return state;
  }
}

export function chatReducer(state, action) {
  switch (action.type) {
    case "socket": {
      if (action.value !== "reconnecting") return { ...state, socket: action.value };
      // The server may or may not have processed in-flight sends. Mark them
      // failed; if one did go through, the resume snapshot confirms it.
      return {
        ...state,
        socket: action.value,
        pending: state.pending.map((p) =>
          p.state === "pending" ? { ...p, state: "failed", error: CONNECTION_LOST } : p,
        ),
      };
    }
    case "send":
      return {
        ...state,
        pending: [
          ...state.pending,
          { client_id: action.client_id, text: action.text, timestamp: action.timestamp, state: "pending" },
        ],
      };
    case "frame":
      return applyFrame(state, action.frame);
    default:
      return state;
  }
}

export function selectTimeline(state) {
  const confirmed = state.messages.map((m) => ({
    key: m.id,
    text: m.text,
    timestamp: m.timestamp,
    direction: m.direction,
    state: "confirmed",
  }));
  const local = state.pending.map((p) => ({
    key: p.client_id,
    text: p.text,
    timestamp: p.timestamp,
    direction: "outgoing",
    state: p.state,
    ...(p.error ? { error: p.error } : {}),
  }));
  return [...confirmed, ...local];
}

export function selectResumeInfo(state) {
  const last = state.messages[state.messages.length - 1];
  return { session_id: state.status.session_id, last_seq: last ? last.seq : 0 };
}
