"""
Telegram Notification Module
Sends trade alerts, errors, and session summaries to Telegram.

Setup:
1. Message @BotFather on Telegram, create a bot, get the token
2. Get your chat_id by messaging @userinfobot
3. Add to config/strategy_config.yaml:
   notifications:
     enabled: true
     telegram_token: "YOUR_BOT_TOKEN"
     telegram_chat_id: "YOUR_CHAT_ID"
"""

import urllib.request
import urllib.parse
import json
from typing import Optional
from ..logger import get_logger


class TelegramNotifier:
    """
    Sends notifications to Telegram via Bot API.
    Uses urllib (no extra dependencies needed).
    """

    def __init__(self, token: str, chat_id: str, enabled: bool = True):
        self.token = token
        self.chat_id = chat_id
        self.enabled = enabled
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.logger = get_logger("Notifier")

        if enabled and token and chat_id:
            self.logger.info("Telegram notifications enabled")
        else:
            self.enabled = False
            self.logger.info("Telegram notifications disabled")

    def _send(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.enabled:
            return False

        try:
            url = f"{self.base_url}/sendMessage"
            data = urllib.parse.urlencode({
                'chat_id': self.chat_id,
                'text': text,
                'parse_mode': parse_mode,
                'disable_web_page_preview': 'true'
            }).encode('utf-8')

            req = urllib.request.Request(url, data=data, method='POST')
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200

        except Exception as e:
            self.logger.error(f"Telegram send failed: {e}")
            return False

    # =========================================================
    # Trade lifecycle notifications
    # =========================================================

    def signal_generated(self, direction: str, entry: float, sl: float,
                         risk: float, tp1: float) -> bool:
        """Send a Telegram notification for a new trading signal."""
        msg = (
            f"<b>SIGNAL</b>\n"
            f"Direction: <b>{direction}</b>\n"
            f"Entry: {entry:.2f}\n"
            f"Stop Loss: {sl:.2f}\n"
            f"TP1: {tp1:.2f}\n"
            f"Risk: ${risk:.2f}"
        )
        return self._send(msg)

    def trade_filled(self, trade_id: str, direction: str, price: float,
                     qty: int, slippage: float = 0) -> bool:
        """Send a Telegram notification when an order is filled."""
        slip_str = f" (slip: {slippage:+.2f})" if abs(slippage) >= 0.01 else ""
        msg = (
            f"<b>FILLED</b>\n"
            f"Trade: {trade_id}\n"
            f"{direction} {qty} @ {price:.2f}{slip_str}"
        )
        return self._send(msg)

    def trade_stopped(self, trade_id: str, direction: str, entry: float,
                      exit_price: float, pnl: float) -> bool:
        """Send a Telegram notification when a trade is stopped out."""
        emoji = "+" if pnl >= 0 else ""
        msg = (
            f"<b>STOPPED OUT</b>\n"
            f"Trade: {trade_id}\n"
            f"{direction} {entry:.2f} -> {exit_price:.2f}\n"
            f"P&L: <b>${emoji}{pnl:.2f}</b>"
        )
        return self._send(msg)

    def tp_hit(self, trade_id: str, tp_level: int, price: float,
               new_sl: Optional[float] = None) -> bool:
        """Send a Telegram notification when a take-profit level is hit."""
        sl_str = f"\nNew SL: {new_sl:.2f}" if new_sl else ""
        msg = (
            f"<b>TP{tp_level} HIT</b>\n"
            f"Trade: {trade_id}\n"
            f"Price: {price:.2f}{sl_str}"
        )
        return self._send(msg)

    def reentry_triggered(self, direction: str, price: float) -> bool:
        """Send a Telegram notification when a re-entry is triggered."""
        msg = (
            f"<b>RE-ENTRY</b>\n"
            f"Direction: {direction}\n"
            f"Limit @ {price:.2f}"
        )
        return self._send(msg)

    # =========================================================
    # Session notifications
    # =========================================================

    def session_summary(self, date: str, trades: int, wins: int,
                        losses: int, pnl: float, reentries: int) -> bool:
        """Send a Telegram notification with the end-of-session summary."""
        emoji = "+" if pnl >= 0 else ""
        win_rate = (wins / trades * 100) if trades > 0 else 0
        msg = (
            f"<b>SESSION SUMMARY</b>\n"
            f"Date: {date}\n"
            f"Trades: {trades} | W:{wins} L:{losses}\n"
            f"Win Rate: {win_rate:.0f}%\n"
            f"P&L: <b>${emoji}{pnl:.2f}</b>\n"
            f"Re-entries: {reentries}"
        )
        return self._send(msg)

    def bot_started(self, mode: str, symbol: str, account: str = "") -> bool:
        """Send a Telegram notification that the bot has started."""
        msg = (
            f"<b>BOT STARTED</b>\n"
            f"Mode: {mode}\n"
            f"Symbol: {symbol}\n"
            f"Account: {account}"
        )
        return self._send(msg)

    def bot_stopped(self, reason: str = "Manual shutdown") -> bool:
        """Send a Telegram notification that the bot has stopped."""
        msg = f"<b>BOT STOPPED</b>\n{reason}"
        return self._send(msg)

    # =========================================================
    # Error / alert notifications
    # =========================================================

    def error(self, message: str) -> bool:
        """Send a Telegram notification for an error."""
        msg = f"<b>ERROR</b>\n{message}"
        return self._send(msg)

    def kill_switch_triggered(self, reason: str) -> bool:
        """Send a Telegram notification when the kill switch is triggered."""
        msg = f"<b>KILL SWITCH TRIGGERED</b>\n{reason}"
        return self._send(msg)

    def connection_lost(self) -> bool:
        """Send a Telegram notification that the connection has been lost."""
        return self._send("<b>CONNECTION LOST</b>\nAttempting reconnection...")

    def connection_restored(self) -> bool:
        """Send a Telegram notification that the connection has been restored."""
        return self._send("<b>CONNECTION RESTORED</b>")

    def position_mismatch(self, strategy_pos: str, broker_pos: str) -> bool:
        """Send a Telegram notification for a position mismatch."""
        msg = (
            f"<b>POSITION MISMATCH</b>\n"
            f"Strategy: {strategy_pos}\n"
            f"Broker: {broker_pos}"
        )
        return self._send(msg)

    def trade_skipped(self, reason: str) -> bool:
        """Send a Telegram notification that a trade was skipped."""
        msg = f"<b>TRADE SKIPPED</b>\n{reason}"
        return self._send(msg)


def create_notifier(config: dict) -> TelegramNotifier:
    """Create notifier from config."""
    notif_config = config.get('notifications', {})
    return TelegramNotifier(
        token=notif_config.get('telegram_token', ''),
        chat_id=notif_config.get('telegram_chat_id', ''),
        enabled=notif_config.get('enabled', False)
    )
