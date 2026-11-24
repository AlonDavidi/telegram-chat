import { useChatSocket } from "./chat/useChatSocket";
import MessageInput from "./components/MessageInput";
import MessageList from "./components/MessageList";
import StatusBar from "./components/StatusBar";
import "./index.css";

function App() {
  const { state, timeline, send } = useChatSocket();
  const canSend = state.socket === "open" && state.status.connected;
  const placeholder = canSend
    ? "Type a message..."
    : state.socket !== "open"
      ? "Connecting to the server..."
      : "Waiting for a Telegram user to send /start";

  return (
    <div className="chat-page">
      <div className="chat-container">
        <header className="chat-header">
          <h2>Telegram Chat</h2>
          <StatusBar socket={state.socket} status={state.status} />
        </header>
        <MessageList items={timeline} />
        <MessageInput disabled={!canSend} placeholder={placeholder} onSend={send} />
      </div>
    </div>
  );
}

export default App;
