"""Trade state persistence for crash recovery."""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict

from ..logger import get_logger
from ..timezone.market_calendar import get_default_tz

EST = get_default_tz()


class TradeStatePersistence:
    """
    Persists critical trade state to disk so the bot can recover after a crash.
    Writes a JSON snapshot after every state change.
    """

    def __init__(self, state_file: str = "trading_data/trade_state.json"):
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.logger = get_logger("StatePersist")

    def save_state(self, state: Dict) -> bool:
        """Atomically write the state dictionary to disk as JSON."""
        try:
            tmp_file = self.state_file.with_suffix('.tmp')
            with open(tmp_file, 'w') as f:
                json.dump(state, f, indent=2, default=str)
            os.replace(str(tmp_file), str(self.state_file))
            return True
        except Exception as e:
            self.logger.error(f"Failed to save trade state: {e}")
            return False

    def load_state(self) -> Optional[Dict]:
        """Load and return the persisted state from disk, or None if unavailable."""
        try:
            if not self.state_file.exists():
                return None
            with open(self.state_file, 'r') as f:
                state = json.load(f)
            self.logger.info(f"Loaded trade state from {self.state_file}")
            return state
        except Exception as e:
            self.logger.error(f"Failed to load trade state: {e}")
            return None

    def clear_state(self):
        """Delete the persisted state file from disk."""
        if self.state_file.exists():
            os.unlink(str(self.state_file))
            self.logger.info("Trade state file cleared")

    def build_snapshot(
        self,
        entry_triggered: bool,
        losses_today: int,
        reentries_used: int,
        active_trade_id: Optional[str],
        active_trade_direction: Optional[str],
        active_trade_entry_price: Optional[float],
        active_trade_stop_loss: Optional[float],
        session_date: str
    ) -> Dict:
        """Build a state snapshot dictionary from the current trade parameters."""
        return {
            'timestamp': datetime.now(EST).isoformat(),
            'session_date': session_date,
            'entry_triggered': entry_triggered,
            'losses_today': losses_today,
            'reentries_used': reentries_used,
            'active_trade_id': active_trade_id,
            'active_trade_direction': active_trade_direction,
            'active_trade_entry_price': active_trade_entry_price,
            'active_trade_stop_loss': active_trade_stop_loss,
        }
