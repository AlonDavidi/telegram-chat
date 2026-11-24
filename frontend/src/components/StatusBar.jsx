const SOCKET_LABELS = {
  connecting: "Connecting…",
  open: "Online",
  reconnecting: "Reconnecting…",
};

export function participantLabel(status) {
  if (!status.connected || !status.participant) return "Waiting for a Telegram user to send /start";
  const { username, first_name } = status.participant;
  return username ? `Connected to @${username}` : `Connected to ${first_name}`;
}

export default function StatusBar({ socket, status }) {
  return (
    <div className="chat-status">
      <span className={`chat-status-dot ${socket}`} aria-hidden="true" />
      <span className="chat-status-socket">{SOCKET_LABELS[socket] ?? socket}</span>
      <span className="chat-status-participant">{participantLabel(status)}</span>
    </div>
  );
}
