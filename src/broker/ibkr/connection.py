"""
IBKR Connection Manager
Handles connection to Interactive Brokers TWS/Gateway using ib_insync.
Implements BaseConnection interface.
"""

import asyncio
from datetime import datetime
from typing import Optional, Callable, List, Any
from contextlib import asynccontextmanager

from ib_insync import IB, Contract, Future, util

from ...logger import get_logger, StrategyLogger
from ..base import BaseConnection


# Human-readable IBKR error translations
IBKR_ERROR_TRANSLATIONS = {
    # Connection errors
    502: "Cannot connect to TWS - is TWS/Gateway running?",
    504: "Connection lost - will attempt reconnection",
    509: "Connection reset - reconnecting",
    1100: "Connectivity lost - waiting for reconnection",
    1101: "Connectivity restored - data lost during disconnect",
    1102: "Connectivity restored - data maintained",

    # Order errors
    103: "Duplicate order ID - order already submitted",
    104: "Cannot modify filled order",
    105: "Order being modified is not open",
    110: "Price out of range - order price too far from market",
    135: "Cannot cancel order - already filled or cancelled",
    136: "Cannot cancel order - not found",
    161: "Order cancelled - account closed",
    201: "Order rejected - insufficient margin",
    202: "Order cancelled by user",
    203: "Security not available for trading",

    # Market data errors
    354: "Market data request failed - no subscription",
    10167: "Delayed market data only - no real-time subscription",
    10168: "Market data farm connection OK",
    10182: "No market data during market hours",
    10187: "Historical data request pacing violation",

    # Account errors
    321: "Server error - temporary issue, will retry",
    399: "Order message error - check order parameters",
    10147: "Invalid order - missing required field",

    # Info messages (not errors)
    2104: "Market data farm connected (info)",
    2106: "Historical data farm connected (info)",
    2107: "Historical data farm disconnected",
    2108: "Market data farm connection inactive",
    2119: "Market data server OK (info)",
    2158: "Security definition farm OK (info)",
}


def translate_ibkr_error(error_code: int, error_string: str) -> str:
    """
    Translate IBKR error code to human-readable message.

    Args:
        error_code: IBKR error code
        error_string: Original IBKR error message

    Returns:
        Human-readable error message
    """
    if error_code in IBKR_ERROR_TRANSLATIONS:
        return IBKR_ERROR_TRANSLATIONS[error_code]
    return error_string


class IBKRConnectionError(ConnectionError):
    """Custom exception for IBKR connection errors. Inherits ConnectionError for broker-agnostic catching."""
    pass


class IBKRConnection(BaseConnection):
    """
    Manages connection to IBKR TWS/Gateway.
    Provides connection lifecycle management, reconnection logic, and contract handling.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
        timeout: int = 60,
        readonly: bool = False
    ):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.timeout = timeout
        self.readonly = readonly

        self.ib: Optional[IB] = None
        self._connected = False
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5
        self._reconnect_delay = 5  # seconds

        self._disconnect_callbacks: List[Callable] = []
        self._error_callbacks: List[Callable] = []

        self.logger = get_logger("Connection")

    @property
    def is_connected(self) -> bool:
        """Check if connected to IBKR."""
        return self.ib is not None and self.ib.isConnected()

    def connect(self) -> bool:
        """
        Establish connection to IBKR.

        Returns:
            bool: True if connection successful

        Raises:
            IBKRConnectionError: If connection fails after retries
        """
        if self.is_connected:
            self.logger.warning("Already connected to IBKR")
            return True

        self.ib = IB()
        self._setup_event_handlers()

        self.logger.info(f"Connecting to IBKR at {self.host}:{self.port} (client_id={self.client_id})")

        try:
            self.ib.connect(
                host=self.host,
                port=self.port,
                clientId=self.client_id,
                timeout=self.timeout,
                readonly=self.readonly
            )
            self._connected = True
            self._reconnect_attempts = 0
            self.logger.info("Successfully connected to IBKR")
            self._log_account_info()
            return True

        except Exception as e:
            self.logger.error(f"Failed to connect to IBKR: {e}")
            raise IBKRConnectionError(f"Connection failed: {e}")

    def _setup_event_handlers(self) -> None:
        """Setup IB event handlers."""
        self.ib.disconnectedEvent += self._on_disconnect
        self.ib.errorEvent += self._on_error

    def _on_disconnect(self) -> None:
        """Handle disconnect event."""
        self._connected = False
        self.logger.warning("Disconnected from IBKR")

        # Log heartbeat status
        strategy_logger = StrategyLogger._instance
        if strategy_logger:
            strategy_logger.heartbeat(False)

        for callback in self._disconnect_callbacks:
            try:
                callback()
            except Exception as e:
                self.logger.error(f"Error in disconnect callback: {e}")

    def _on_error(self, reqId: int, errorCode: int, errorString: str, contract: Contract) -> None:
        """Handle error events from IBKR with human-readable translations."""
        # Skip informational/expected messages - they spam the logs
        # 2104/2106/2158/2119/2107/2108: farm connection status
        # 354/10168: delayed data warnings
        # 162: "no data" - expected when querying candle before delayed data arrives
        skip_codes = {2104, 2106, 2158, 2119, 2107, 2108, 10168, 354, 162}

        if errorCode in skip_codes:
            return

        # Translate error to human-readable message
        translated = translate_ibkr_error(errorCode, errorString)

        # Get the strategy logger for enhanced logging if available
        strategy_logger = StrategyLogger._instance

        if errorCode < 1000:
            if strategy_logger:
                strategy_logger.ibkr_error(errorCode, errorString, translated)
            else:
                self.logger.warning(f"[IBKR] Warning {errorCode}: {translated}")
        else:
            if strategy_logger:
                strategy_logger.ibkr_error(errorCode, errorString, translated)
            else:
                self.logger.error(f"[IBKR] Error {errorCode}: {translated}")

        for callback in self._error_callbacks:
            try:
                callback(reqId, errorCode, errorString, contract)
            except Exception as e:
                self.logger.error(f"Error in error callback: {e}")

    def _log_account_info(self) -> None:
        """Log account information after connection."""
        try:
            accounts = self.ib.managedAccounts()
            self.logger.info(f"Managed accounts: {accounts}")

            strategy_logger = StrategyLogger._instance
            for account in accounts:
                self.logger.debug(f"Connected to account: {account}")
                if strategy_logger:
                    strategy_logger.heartbeat(True, account)
        except Exception as e:
            self.logger.warning(f"Could not retrieve account info: {e}")

    def disconnect(self) -> None:
        """Disconnect from IBKR."""
        if self.ib and self.is_connected:
            self.logger.info("Disconnecting from IBKR")
            self.ib.disconnect()
            self._connected = False
            self.logger.info("Disconnected from IBKR")

    def reconnect(self) -> bool:
        """
        Attempt to reconnect to IBKR.

        Returns:
            bool: True if reconnection successful
        """
        self._reconnect_attempts += 1
        self.logger.info(f"Reconnection attempt {self._reconnect_attempts}/{self._max_reconnect_attempts}")

        if self._reconnect_attempts > self._max_reconnect_attempts:
            self.logger.error("Max reconnection attempts reached")
            return False

        try:
            self.disconnect()
            import time
            time.sleep(self._reconnect_delay)
            return self.connect()
        except IBKRConnectionError as e:
            self.logger.error(f"Reconnection failed: {e}")
            return False

    def get_contract(self, symbol: str, expiry: str = "") -> Any:
        """BaseConnection interface: get a qualified contract."""
        return self.get_nq_contract(symbol=symbol, expiry=expiry)

    def get_nq_contract(self, symbol: str = "NQ", expiry: str = "") -> Future:
        """
        Get the NQ/MNQ E-mini futures contract.
        """
        if not expiry:
            expiry = self._get_front_month_expiry(symbol)

        contract = Future(
            symbol=symbol,
            lastTradeDateOrContractMonth=expiry,
            exchange="CME",
            currency="USD"
        )

        qualified = self.ib.qualifyContracts(contract)

        if not qualified:
            raise IBKRConnectionError(f"Failed to qualify {symbol} contract with expiry {expiry}")

        qualified_contract = qualified[0]
        self.logger.info(
            f"Qualified NQ contract: {qualified_contract.localSymbol} "
            f"(expiry: {qualified_contract.lastTradeDateOrContractMonth})"
        )

        return qualified_contract

    def _get_front_month_expiry(self, symbol: str) -> str:
        """Get the front-month expiry for a futures contract."""
        now = datetime.now()
        year = now.year
        month = now.month

        expiry_months = [3, 6, 9, 12]

        for exp_month in expiry_months:
            if exp_month >= month:
                third_friday = self._get_third_friday(year, exp_month)
                if now.date() <= third_friday or exp_month > month:
                    expiry = f"{year}{exp_month:02d}"
                    self.logger.debug(f"Front-month expiry for {symbol}: {expiry}")
                    return expiry

        expiry = f"{year + 1}03"
        self.logger.debug(f"Front-month expiry for {symbol}: {expiry}")
        return expiry

    def _get_third_friday(self, year: int, month: int):
        """Get the third Friday of a given month."""
        from datetime import date, timedelta as td

        first_day = date(year, month, 1)
        first_friday = first_day + td(days=(4 - first_day.weekday() + 7) % 7)
        third_friday = first_friday + td(days=14)
        return third_friday

    def get_mnq_contract(self, expiry: str = "") -> Future:
        """Get the Micro NQ (MNQ) futures contract."""
        if not expiry:
            expiry = self._get_front_month_expiry("MNQ")

        contract = Future(
            symbol="MNQ",
            lastTradeDateOrContractMonth=expiry,
            exchange="CME",
            currency="USD"
        )

        qualified = self.ib.qualifyContracts(contract)

        if not qualified:
            raise IBKRConnectionError(f"Failed to qualify MNQ contract with expiry {expiry}")

        qualified_contract = qualified[0]
        self.logger.info(
            f"Qualified MNQ contract: {qualified_contract.localSymbol} "
            f"(expiry: {qualified_contract.lastTradeDateOrContractMonth})"
        )

        return qualified_contract

    def add_disconnect_callback(self, callback: Callable) -> None:
        """Add a callback to be called on disconnect."""
        self._disconnect_callbacks.append(callback)

    def add_error_callback(self, callback: Callable) -> None:
        """Add a callback to be called on errors."""
        self._error_callbacks.append(callback)

    def sleep(self, seconds: float = 0) -> None:
        """Sleep while allowing IB message processing."""
        if self.ib:
            self.ib.sleep(seconds)

    def wait_on_update(self, timeout: float = 0) -> bool:
        """Wait for any pending updates."""
        if self.ib:
            return self.ib.waitOnUpdate(timeout=timeout)
        return False

    @asynccontextmanager
    async def connection_context(self):
        """Async context manager for connection lifecycle."""
        try:
            self.connect()
            yield self
        finally:
            self.disconnect()

    def __enter__(self):
        """Sync context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Sync context manager exit."""
        self.disconnect()
        return False


def create_connection(config: dict) -> IBKRConnection:
    """Create an IBKRConnection from configuration."""
    ibkr_config = config.get('ibkr', {})

    return IBKRConnection(
        host=ibkr_config.get('host', '127.0.0.1'),
        port=ibkr_config.get('port', 7497),
        client_id=ibkr_config.get('client_id', 1),
        timeout=ibkr_config.get('timeout', 60),
        readonly=ibkr_config.get('readonly', False)
    )
