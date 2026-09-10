"""
Health Check Writer.

Writes a JSON health file that external monitoring tools can poll.
File is updated every cycle, so if the timestamp goes stale,
the bot is dead.

Usage:
    External tools (cron, Nagios, Datadog) read /tmp/nq_strategy_health.json
    If timestamp is older than 60s, the bot is hung/dead.
"""

import json
import os
from datetime import datetime
from typing import Optional, Dict
from ..logger import get_logger
from ..timezone.market_calendar import get_default_tz

EST = get_default_tz()


class HealthCheckWriter:
    """Writes a health status file for external monitoring tools."""

    def __init__(self, health_file: str = "/tmp/nq_strategy_health.json"):
        self.health_file = health_file
        self.logger = get_logger("HealthCheck")

    def write(
        self,
        is_connected: bool,
        has_position: bool,
        position_direction: str = "FLAT",
        position_pnl: float = 0.0,
        kill_switch_active: bool = False,
        last_trade_time: Optional[str] = None,
        session_trades: int = 0,
        session_pnl: float = 0.0
    ) -> None:
        """Write health status to file. Called every execution cycle."""
        status = {
            "timestamp": datetime.now(EST).isoformat(),
            "status": "KILLED" if kill_switch_active else ("TRADING" if is_connected else "DISCONNECTED"),
            "connected": is_connected,
            "has_position": has_position,
            "position": position_direction,
            "unrealized_pnl": round(position_pnl, 2),
            "kill_switch": kill_switch_active,
            "last_trade": last_trade_time,
            "session_trades": session_trades,
            "session_pnl": round(session_pnl, 2),
            "pid": os.getpid()
        }
        try:
            tmp = self.health_file + ".tmp"
            with open(tmp, 'w') as f:
                json.dump(status, f, indent=2)
            os.replace(tmp, self.health_file)
        except Exception as e:
            self.logger.error(f"Failed to write health check: {e}")

    def clear(self) -> None:
        """Remove health file on clean shutdown."""
        try:
            if os.path.exists(self.health_file):
                os.unlink(self.health_file)
        except Exception:
            pass
