import { useCallback, useEffect, useReducer, useRef } from "react";
import { chatReducer, initialState, selectResumeInfo, selectTimeline } from "./chatReducer";

const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 10000];

// Same origin: the Vite dev server (dev) or nginx (Docker) proxies /ws to the backend.
export function defaultSocketUrl() {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/ws`;
}

export function useChatSocket(url = defaultSocketUrl()) {
  const [state, dispatch] = useReducer(chatReducer, initialState);
  const stateRef = useRef(state);
  stateRef.current = state;
  const socketRef = useRef(null);

  useEffect(() => {
    let disposed = false;
    let attempt = 0;
    let timer = null;

    function connect() {
      const ws = new WebSocket(url);
      socketRef.current = ws;

      ws.onopen = () => {
        attempt = 0;
        dispatch({ type: "socket", value: "open" });
        // Resume: the server sends only what we missed (or everything, if our session is stale).
        ws.send(JSON.stringify({ type: "hello", ...selectResumeInfo(stateRef.current) }));
      };

      ws.onmessage = (event) => {
        let frame;
        try {
          frame = JSON.parse(event.data);
        } catch {
          return;
        }
        dispatch({ type: "frame", frame });
      };

      ws.onclose = () => {
        if (disposed) return;
        socketRef.current = null;
        dispatch({ type: "socket", value: "reconnecting" });
        const delay = RECONNECT_DELAYS_MS[Math.min(attempt, RECONNECT_DELAYS_MS.length - 1)];
        attempt += 1;
        timer = setTimeout(connect, delay);
      };
    }

    connect();
    return () => {
      // Also runs on React StrictMode's dev double-mount.
      disposed = true;
      clearTimeout(timer);
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [url]);

  const send = useCallback((text) => {
    const ws = socketRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    const client_id = crypto.randomUUID();
    dispatch({ type: "send", client_id, text, timestamp: new Date().toISOString() });
    ws.send(JSON.stringify({ type: "send", client_id, text }));
    return true;
  }, []);

  return { state, timeline: selectTimeline(state), send };
}
