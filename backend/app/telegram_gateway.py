"""python-telegram-bot wiring: long polling in, sendMessage out.

The gateway only translates between Telegram and ChatService; all rules live in
the service. PTB processes updates one at a time (its default), which keeps
incoming messages in order.
"""

import logging
from datetime import datetime

import telegram.error
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters

from app.models import Participant
from app.service import ChatService, GatewayError, GatewayForbidden

logger = logging.getLogger(__name__)


def participant_from(update: Update) -> Participant:
    user = update.effective_user
    return Participant(
        chat_id=update.effective_chat.id,
        first_name=user.first_name,
        username=user.username,
    )


class TelegramGateway:
    def __init__(self, token: str) -> None:
        self._token = token
        self._app: Application | None = None
        self._service: ChatService | None = None

    def attach(self, service: ChatService) -> None:
        self._service = service
        self._app = ApplicationBuilder().token(self._token).build()
        self._send_message = self._app.bot.send_message

        private = filters.ChatType.PRIVATE
        self._app.add_handlers(
            [
                CommandHandler("start", self._on_start, filters=private),
                CommandHandler("stop", self._on_stop, filters=private),
                # Any other text, including unknown /commands, is a chat message.
                MessageHandler(private & filters.TEXT, self._on_text),
                MessageHandler(private & ~filters.TEXT, self._on_non_text),
            ]
        )

    async def start(self) -> None:
        await self._app.initialize()
        await self._app.start()
        # Updates queued while we were down belong to a session that no longer
        # exists in memory; edited messages are out of scope.
        await self._app.updater.start_polling(drop_pending_updates=True, allowed_updates=["message"])

    async def stop(self) -> None:
        if self._app.updater.running:
            await self._app.updater.stop()
        if self._app.running:
            await self._app.stop()
        await self._app.shutdown()

    async def send_text(self, chat_id: int, text: str) -> datetime:
        try:
            msg = await self._send_message(chat_id=chat_id, text=text)
        except telegram.error.Forbidden as exc:
            raise GatewayForbidden(str(exc)) from exc
        except telegram.error.TelegramError as exc:  # TimedOut, NetworkError, BadRequest, ...
            raise GatewayError(str(exc)) from exc
        return msg.date

    # ---- update handlers --------------------------------------------------------

    async def _on_start(self, update: Update, context: object) -> None:
        await self._reply(update, await self._service.on_start(participant_from(update)))

    async def _on_stop(self, update: Update, context: object) -> None:
        await self._reply(update, await self._service.on_stop(update.effective_chat.id))

    async def _on_text(self, update: Update, context: object) -> None:
        message = update.message
        reply = await self._service.on_text(update.effective_chat.id, message.text, message.date)
        await self._reply(update, reply)

    async def _on_non_text(self, update: Update, context: object) -> None:
        await self._reply(update, await self._service.on_non_text(update.effective_chat.id))

    @staticmethod
    async def _reply(update: Update, text: str | None) -> None:
        if text is None:
            return
        try:
            await update.message.reply_text(text)
        except telegram.error.TelegramError:
            logger.warning("could not reply to chat %s", update.effective_chat.id, exc_info=True)
