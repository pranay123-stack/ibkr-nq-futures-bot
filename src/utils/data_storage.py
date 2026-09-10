"""
Data Storage Module
Handles CSV file storage for trades and signals.
"""

import csv
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from dataclasses import fields

from ..data_models import Signal, Trade
from ..logger import get_logger


class DataStorageError(Exception):
    """Custom exception for data storage errors."""
    pass


class CSVStorage:
    """
    Handles CSV file storage for trades and signals.

    Features:
    - Automatic file creation with headers
    - Append new records
    - Read historical data
    - File rotation support
    """

    def __init__(
        self,
        data_dir: str = "trading_data",
        base_path: Optional[str] = None
    ):
        self.base_path = Path(base_path) if base_path else Path.cwd()
        self.data_dir = self.base_path / data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.logger = get_logger("DataStorage")

        # Current session date (set on first write, updates at session rollover)
        self._current_session_dir: Optional[Path] = None
        self._ensure_session_dir()

    def _get_session_date(self) -> str:
        """Get trading session date based on 6PM EST boundary."""
        from ..timezone.market_calendar import get_default_tz
        from datetime import datetime
        tz = get_default_tz()
        now = datetime.now(tz)
        if now.hour < 18:
            session_date = (now - timedelta(days=1)).date()
        else:
            session_date = now.date()
        return session_date.strftime("%Y-%m-%d")

    def _ensure_session_dir(self) -> None:
        """Create today's session directory and initialize CSV files."""
        session_date = self._get_session_date()
        session_dir = self.data_dir / session_date

        if self._current_session_dir == session_dir:
            return

        session_dir.mkdir(parents=True, exist_ok=True)
        self._current_session_dir = session_dir
        self.trades_file = session_dir / "trades.csv"
        self.signals_file = session_dir / "signals.csv"

        self._initialize_files()
        self.logger.info(f"Trading data dir: {session_dir}")

    def _ensure_directories(self) -> None:
        """Ensure current session directory exists."""
        self._ensure_session_dir()

    def _initialize_files(self) -> None:
        """Initialize CSV files with headers if they don't exist."""
        # Trade file headers
        trade_headers = [
            'id', 'entry_time', 'exit_time', 'direction', 'entry_price',
            'exit_price', 'quantity', 'exit_quantity', 'stop_loss',
            'current_stop_loss', 'tp1', 'tp2', 'tp3', 'tp4', 'highest_tp_hit',
            'realized_pnl', 'unrealized_pnl', 'risk_amount', 'status',
            'is_reentry', 'original_trade_id'
        ]

        # Signal file headers
        signal_headers = [
            'id', 'timestamp', 'signal_type', 'direction', 'price',
            'stop_loss', 'take_profit_1', 'take_profit_2', 'take_profit_3',
            'take_profit_4', 'risk_amount', 'reason', 'is_reentry',
            'candle_open', 'candle_high', 'candle_low', 'candle_close'
        ]

        self._create_file_if_not_exists(self.trades_file, trade_headers)
        self._create_file_if_not_exists(self.signals_file, signal_headers)

    def _create_file_if_not_exists(self, filepath: Path, headers: List[str]) -> None:
        """Create a CSV file with headers if it doesn't exist."""
        if not filepath.exists():
            try:
                with open(filepath, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(headers)
                self.logger.info(f"Created {filepath}")
            except Exception as e:
                self.logger.error(f"Failed to create {filepath}: {e}")
                raise DataStorageError(f"Failed to create file: {e}")

    def save_signal(self, signal: Signal) -> bool:
        """
        Save a signal to the signals CSV file.

        Args:
            signal: Signal object to save

        Returns:
            True if successful
        """
        try:
            self._ensure_session_dir()
            signal_dict = signal.to_dict()

            with open(self.signals_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=signal_dict.keys())
                writer.writerow(signal_dict)

            self.logger.debug(f"Saved signal {signal.id} to {self.signals_file}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to save signal: {e}")
            return False

    def save_trade(self, trade: Trade) -> bool:
        """
        Save a trade to the trades CSV file.

        Args:
            trade: Trade object to save

        Returns:
            True if successful
        """
        try:
            self._ensure_session_dir()
            trade_dict = trade.to_dict()

            with open(self.trades_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=trade_dict.keys())
                writer.writerow(trade_dict)

            self.logger.debug(f"Saved trade {trade.id} to {self.trades_file}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to save trade: {e}")
            return False

    def update_trade(self, trade: Trade) -> bool:
        """
        Update an existing trade in the CSV file.
        Reads all trades, updates the matching one, and rewrites the file.

        Args:
            trade: Trade object to update

        Returns:
            True if successful
        """
        try:
            # Read all trades
            trades = self.load_trades_raw()

            # Find and update the matching trade
            updated = False
            for i, t in enumerate(trades):
                if t.get('id') == trade.id:
                    trades[i] = trade.to_dict()
                    updated = True
                    break

            if not updated:
                # Trade not found, append it
                return self.save_trade(trade)

            # Atomic rewrite: write to temp file first, then rename
            trade_dict = trade.to_dict()
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=str(self.trades_file.parent),
                suffix='.tmp'
            )
            try:
                with os.fdopen(tmp_fd, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=trade_dict.keys())
                    writer.writeheader()
                    for t in trades:
                        writer.writerow(t)
                # Atomic rename (on POSIX systems)
                os.replace(tmp_path, str(self.trades_file))
            except Exception:
                # Clean up temp file on error
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise

            self.logger.debug(f"Updated trade {trade.id}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to update trade: {e}")
            return False

    def load_trades_raw(self) -> List[dict]:
        """
        Load all trades from CSV as raw dictionaries.

        Returns:
            List of trade dictionaries
        """
        try:
            if not self.trades_file.exists():
                return []

            with open(self.trades_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                return list(reader)

        except Exception as e:
            self.logger.error(f"Failed to load trades: {e}")
            return []

    def load_signals_raw(self) -> List[dict]:
        """
        Load all signals from CSV as raw dictionaries.

        Returns:
            List of signal dictionaries
        """
        try:
            if not self.signals_file.exists():
                return []

            with open(self.signals_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                return list(reader)

        except Exception as e:
            self.logger.error(f"Failed to load signals: {e}")
            return []

    def get_trades_for_date(self, date: datetime) -> List[dict]:
        """
        Get all trades for a specific date.

        Args:
            date: Date to filter by

        Returns:
            List of trade dictionaries
        """
        all_trades = self.load_trades_raw()
        date_str = date.strftime('%Y-%m-%d')

        filtered = []
        for trade in all_trades:
            entry_time = trade.get('entry_time', '')
            if entry_time.startswith(date_str):
                filtered.append(trade)

        return filtered

    def get_signals_for_date(self, date: datetime) -> List[dict]:
        """
        Get all signals for a specific date.

        Args:
            date: Date to filter by

        Returns:
            List of signal dictionaries
        """
        all_signals = self.load_signals_raw()
        date_str = date.strftime('%Y-%m-%d')

        filtered = []
        for signal in all_signals:
            timestamp = signal.get('timestamp', '')
            if timestamp.startswith(date_str):
                filtered.append(signal)

        return filtered

    def get_trade_statistics(self) -> dict:
        """
        Calculate basic trade statistics from historical data.

        Returns:
            Dictionary with statistics
        """
        trades = self.load_trades_raw()

        if not trades:
            return {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0.0,
                'total_pnl': 0.0,
                'avg_pnl': 0.0
            }

        total = len(trades)
        winning = 0
        losing = 0
        total_pnl = 0.0

        for trade in trades:
            pnl = float(trade.get('realized_pnl', 0) or 0)
            total_pnl += pnl

            if pnl > 0:
                winning += 1
            elif pnl < 0:
                losing += 1

        return {
            'total_trades': total,
            'winning_trades': winning,
            'losing_trades': losing,
            'win_rate': (winning / total * 100) if total > 0 else 0.0,
            'total_pnl': round(total_pnl, 2),
            'avg_pnl': round(total_pnl / total, 2) if total > 0 else 0.0
        }

    def backup_files(self, suffix: Optional[str] = None) -> bool:
        """
        Create backup copies of trade and signal files.

        Args:
            suffix: Optional suffix for backup files (default: timestamp)

        Returns:
            True if successful
        """
        if suffix is None:
            suffix = datetime.now().strftime('%Y%m%d_%H%M%S')

        try:
            import shutil

            if self.trades_file.exists():
                backup = self.trades_file.with_name(f"trades_{suffix}.csv")
                shutil.copy(self.trades_file, backup)
                self.logger.info(f"Backed up trades to {backup}")

            if self.signals_file.exists():
                backup = self.signals_file.with_name(f"signals_{suffix}.csv")
                shutil.copy(self.signals_file, backup)
                self.logger.info(f"Backed up signals to {backup}")

            return True

        except Exception as e:
            self.logger.error(f"Failed to backup files: {e}")
            return False

    def clear_files(self) -> bool:
        """
        Clear all data from trade and signal files (keeps headers).

        Returns:
            True if successful
        """
        self.logger.warning("Clearing all trade and signal data")

        try:
            # Reinitialize files (overwrites with just headers)
            self._initialize_files()
            return True

        except Exception as e:
            self.logger.error(f"Failed to clear files: {e}")
            return False


def create_data_storage(config: dict, base_path: Optional[str] = None) -> CSVStorage:
    """
    Create a CSVStorage instance from configuration.

    Args:
        config: Configuration dictionary
        base_path: Optional base path for files

    Returns:
        CSVStorage instance
    """
    data_config = config.get('data', {})

    return CSVStorage(
        data_dir=data_config.get('data_dir', 'trading_data'),
        base_path=base_path
    )
