import { useEffect, useRef } from "react";

function formatTime(timestamp) {
  return new Date(timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function MessageList({ items }) {
  const containerRef = useRef(null);

  useEffect(() => {
    const el = containerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [items.length]);

  return (
    <div className="chat-messages" ref={containerRef}>
      {items.length === 0 && <div className="chat-empty">No messages yet</div>}
      {items.map((item) => (
        <div key={item.key} className={`chat-message ${item.direction} ${item.state}`}>
          <div className="chat-bubble">
            <div className="chat-text" dir="auto">
              {item.text}
            </div>
            <div className="chat-timestamp">
              {item.state === "pending" ? "Sending…" : formatTime(item.timestamp)}
            </div>
            {item.error && <div className="chat-error">{item.error}</div>}
          </div>
        </div>
      ))}
    </div>
  );
}
