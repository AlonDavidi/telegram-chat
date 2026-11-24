import { useState } from "react";

const MAX_LENGTH = 4096; // Telegram's limit; the server validates too

export default function MessageInput({ disabled, placeholder, onSend }) {
  const [text, setText] = useState("");

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    if (onSend(trimmed) !== false) setText("");
  };

  return (
    <div className="chat-input">
      <input
        value={text}
        dir="auto"
        maxLength={MAX_LENGTH}
        disabled={disabled}
        placeholder={placeholder}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.nativeEvent.isComposing) submit();
        }}
      />
      <button onClick={submit} disabled={disabled || !text.trim()}>
        Send
      </button>
    </div>
  );
}
