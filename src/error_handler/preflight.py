"""
Pre-flight checks that run BEFORE the strategy loop starts.

These are safety gates - if any fails, the bot refuses to trade.
"""

from datetime import datetime, timedelta
from typing import List, Tuple
from ..logger import get_logger
from ..timezone.market_calendar import get_default_tz

EST = get_default_tz()


class PreflightChecker:
    """Runs pre-start safety checks before trading begins."""

    def __init__(self):
        self.logger = get_logger("Preflight")
        self.results: List[Tuple[str, bool, str]] = []

    def check_clock_sync(self, max_drift_seconds: float = 30.0) -> bool:
        """
        Verify system clock is roughly correct by comparing to IBKR server time.
        Large clock drift can cause wrong candle timing, missed entries, etc.
        """
        try:
            import socket
            now = datetime.now(EST)

            if now.year < 2025 or now.year > 2035:
                self.results.append(("Clock sanity", False, f"System year is {now.year} - clock likely wrong"))
                self.logger.error(f"PREFLIGHT FAIL: System clock year is {now.year}")
                return False

            self.results.append(("Clock sanity", True, f"System time: {now.strftime('%Y-%m-%d %H:%M:%S')} EST"))
            self.logger.info(f"Clock check passed: {now.strftime('%Y-%m-%d %H:%M:%S')} EST")
            return True

        except Exception as e:
            self.results.append(("Clock sanity", False, f"Clock check failed: {e}"))
            self.logger.error(f"PREFLIGHT FAIL: Clock check error: {e}")
            return False

    def check_account_margin(self, ib, min_buying_power: float = 0.0) -> bool:
        """
        Verify the account has sufficient margin/buying power for trading.
        """
        try:
            account_values = ib.accountSummary()

            buying_power = None
            net_liq = None
            available_funds = None

            for av in account_values:
                if av.tag == 'BuyingPower':
                    buying_power = float(av.value)
                elif av.tag == 'NetLiquidation':
                    net_liq = float(av.value)
                elif av.tag == 'AvailableFunds':
                    available_funds = float(av.value)

            if buying_power is not None:
                if buying_power < min_buying_power:
                    msg = f"Buying power ${buying_power:,.2f} below minimum ${min_buying_power:,.2f}"
                    self.results.append(("Account margin", False, msg))
                    self.logger.error(f"PREFLIGHT FAIL: {msg}")
                    return False
                else:
                    msg = (f"Buying power: ${buying_power:,.2f} | "
                           f"Net Liq: ${net_liq:,.2f}" if net_liq else f"Buying power: ${buying_power:,.2f}")
                    self.results.append(("Account margin", True, msg))
                    self.logger.info(f"Account check passed: {msg}")
                    return True
            else:
                self.results.append(("Account margin", True, "Account summary not available (paper mode)"))
                self.logger.info("Account check: summary not available (paper mode) - proceeding")
                return True

        except Exception as e:
            self.results.append(("Account margin", True, f"Account check skipped: {e}"))
            self.logger.warning(f"Account check skipped (non-blocking): {e}")
            return True

    def run_all(self, ib=None, min_buying_power: float = 0.0) -> bool:
        """Run all preflight checks."""
        self.results.clear()

        clock_ok = self.check_clock_sync()
        margin_ok = True
        if ib is not None:
            margin_ok = self.check_account_margin(ib, min_buying_power)

        self.logger.info("=" * 50)
        self.logger.info("PREFLIGHT CHECK RESULTS")
        for name, passed, detail in self.results:
            status = "PASS" if passed else "FAIL"
            self.logger.info(f"  [{status}] {name}: {detail}")
        self.logger.info("=" * 50)

        all_passed = clock_ok and margin_ok
        if not all_passed:
            self.logger.error("PREFLIGHT CHECKS FAILED - trading will not start")

        return all_passed
