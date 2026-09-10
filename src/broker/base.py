"""
Abstract Base Classes for Broker Integration.

Any broker must implement these 2 interfaces:
- BaseConnection: Connect/disconnect/reconnect lifecycle
- BaseMarketData: Subscribe to prices, get historical bars, check market hours

Trade management is handled by assembled components (BrokerComponents dataclass
in broker_factory.py) rather than a monolithic abstract class.

The strategy and runner code ONLY use these interfaces.
Broker-specific details stay inside the broker implementation.
"""

from abc import ABC, abstractmethod


class BrokerConnectionError(ConnectionError):
    """Base exception for broker connection errors. All broker implementations should raise this."""
    pass


class TradeExecutionError(Exception):
    """Base exception for trade execution errors. All broker implementations should raise this."""
    pass
from datetime import datetime
from typing import Optional, List, Callable, Any

from ..data_models import Candle, DayLevels


# ============================================================
# CONNECTION INTERFACE
# ============================================================

class BaseConnection(ABC):
    """Abstract broker connection."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected to broker."""
        ...

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection. Returns True on success."""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from broker."""
        ...

    @abstractmethod
    def reconnect(self) -> bool:
        """Reconnect after disconnect. Returns True on success."""
        ...

    @abstractmethod
    def sleep(self, seconds: float) -> None:
        """Sleep while allowing broker message processing."""
        ...

    @abstractmethod
    def get_contract(self, symbol: str, expiry: str = "") -> Any:
        """Get a qualified futures contract object."""
        ...

    @abstractmethod
    def add_disconnect_callback(self, callback: Callable) -> None:
        """Register a callback for disconnect events."""
        ...

    @abstractmethod
    def add_error_callback(self, callback: Callable) -> None:
        """Register a callback for error events."""
        ...


# ============================================================
# MARKET DATA INTERFACE
# ============================================================

class BaseMarketData(ABC):
    """Abstract market data handler."""

    @property
    @abstractmethod
    def current_price(self) -> Optional[float]:
        """Get the current market price."""
        ...

    @abstractmethod
    def subscribe_ticker(self) -> None:
        """Subscribe to real-time price updates."""
        ...

    @abstractmethod
    def unsubscribe_all(self) -> None:
        """Unsubscribe from all market data."""
        ...

    @abstractmethod
    def get_historical_bars(
        self, duration: str = "2 D", bar_size: str = "5 mins",
        what_to_show: str = "TRADES", use_rth: bool = False
    ) -> List[Candle]:
        """Get historical bar data."""
        ...

    @abstractmethod
    def get_previous_day_levels(self) -> Optional[DayLevels]:
        """Get previous day's high and low."""
        ...

    @abstractmethod
    def get_5min_candle_at_time(self, target_time: datetime) -> Optional[Candle]:
        """Get the 5-minute candle at a specific time."""
        ...

    @abstractmethod
    def is_market_open(self) -> bool:
        """Check if the futures market is currently open."""
        ...

    @abstractmethod
    def get_next_6pm_reopen(self, log_countdown: bool = False) -> datetime:
        """Get the next 6 PM EST market reopen time."""
        ...

    @abstractmethod
    def get_current_session_6pm(self) -> Optional[datetime]:
        """Get current session's 6 PM start, or None if not in session."""
        ...

    @abstractmethod
    def get_current_timestamp(self) -> datetime:
        """Get current timestamp in strategy timezone."""
        ...
