"""
Broker Factory
Creates the correct broker implementation based on config.

Usage in strategy_config.yaml:
    broker: "ibkr"   # or "alpaca", "tradier", etc. when implemented

To add a new broker:
1. Create src/broker/your_broker/ with connection.py, order_placer.py, order_tracker.py, etc.
2. Each must implement the abstract classes from src/broker/base.py
3. Add the broker to BROKER_REGISTRY below
"""

from dataclasses import dataclass
from typing import Tuple, Any

from .base import BaseConnection, BaseMarketData
from ..logger import get_logger
from ..timezone.market_calendar import MarketCalendar

logger = get_logger("BrokerFactory")


@dataclass
class BrokerComponents:
    """Assembled broker components: orders, tracker, positions, and the contract."""
    orders: Any      # IBKROrderPlacer
    tracker: Any     # IBKROrderTracker
    positions: Any   # IBKRPositionReader
    contract: Any    # ib_insync Future contract


def create_broker(config: dict) -> Tuple[BaseConnection, Any]:
    """
    Create broker connection from config.

    Args:
        config: Full strategy config dict

    Returns:
        Tuple of (connection, contract)
    """
    broker_name = config.get('broker', 'ibkr').lower()

    if broker_name == 'ibkr':
        from .ibkr.connection import IBKRConnection, create_connection
        connection = create_connection(config)
        connection.connect()

        contract_config = config.get('contract', {})
        contract = connection.get_contract(
            symbol=contract_config.get('symbol', 'NQ'),
            expiry=contract_config.get('expiry', '')
        )
        return connection, contract

    else:
        raise ValueError(
            f"Unknown broker: '{broker_name}'. "
            f"Available brokers: ibkr. "
            f"Set 'broker: ibkr' in strategy_config.yaml"
        )


def create_trade_manager(
    broker_name: str,
    connection: BaseConnection,
    contract: Any,
    config: dict
) -> BrokerComponents:
    """Create assembled broker components for the specified broker."""
    if broker_name == 'ibkr':
        from .ibkr.order_placer import IBKROrderPlacer
        from .ibkr.order_tracker import IBKROrderTracker
        from .ibkr.position_reader import IBKRPositionReader
        from .rate_limiter import RateLimiter
        from ..utils.defaults import RATE_LIMIT_MAX_CALLS, RATE_LIMIT_PERIOD_SECONDS

        ib = connection.ib
        ibkr_config = (config or {}).get('ibkr', {})

        rate_limiter = RateLimiter(
            max_calls=ibkr_config.get('rate_limit_calls', RATE_LIMIT_MAX_CALLS),
            period_seconds=ibkr_config.get('rate_limit_period', RATE_LIMIT_PERIOD_SECONDS)
        )

        tracker = IBKROrderTracker(ib, contract)
        orders = IBKROrderPlacer(
            ib=ib, contract=contract,
            throttle_fn=rate_limiter.wait_if_needed,
            track_fn=tracker.track_order,
            expected_prices=tracker._expected_fill_prices
        )
        positions = IBKRPositionReader(ib, contract, orders)

        return BrokerComponents(
            orders=orders,
            tracker=tracker,
            positions=positions,
            contract=contract,
        )
    else:
        raise ValueError(f"Unknown broker: '{broker_name}'")


def create_market_data(
    broker_name: str,
    connection: BaseConnection,
    contract: Any,
    config: dict
) -> BaseMarketData:
    """Create market data handler for the specified broker."""
    if broker_name == 'ibkr':
        from .ibkr.market_data import IBKRMarketData
        cal = MarketCalendar(config)
        ibkr_config = (config or {}).get('ibkr', {})
        mdt = ibkr_config.get('market_data_type', 3)
        return IBKRMarketData(
            connection=connection,
            contract=contract,
            market_calendar=cal,
            market_data_type=mdt
        )
    else:
        raise ValueError(f"Unknown broker: '{broker_name}'")
