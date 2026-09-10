"""
Central defaults for all configurable values.

Every magic number in the system lives here as a named constant.
Classes use these as their default parameter values.
Config values override them at runtime.

This is the SINGLE SOURCE OF TRUTH for all default values.
"""

# --- Safety defaults ---
MAX_DAILY_LOSS = 1000.0
MAX_CONSECUTIVE_ERRORS = 10
MAX_SLIPPAGE_POINTS = 5.0
MAX_CANDLE_RANGE_POINTS = 100.0
MIN_CANDLE_BODY_RATIO = 0.10
STALE_DATA_TIMEOUT_SECONDS = 30.0
POSITION_MISMATCH_CHECK_SECONDS = 60.0

# --- Rate limiting ---
RATE_LIMIT_MAX_CALLS = 45
RATE_LIMIT_PERIOD_SECONDS = 1.0

# --- Contract defaults ---
DEFAULT_TICK_SIZE = 0.25
DEFAULT_SYMBOL = "NQ"

# --- Contract specifications by symbol ---
CONTRACT_SPECS = {
    "MNQ": {"tick_value": 0.50, "point_value": 2.0},
    "NQ":  {"tick_value": 5.0,  "point_value": 20.0},
}

# --- Snapshot / monitoring ---
POSITION_SNAPSHOT_INTERVAL_MINUTES = 15

# --- Fill timeouts ---
MARKET_ORDER_FILL_TIMEOUT = 30
LIMIT_ORDER_FILL_TIMEOUT = 120
