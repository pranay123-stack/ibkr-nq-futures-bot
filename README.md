# IBKR NQ Futures Bot

NASDAQ-100 E-mini futures automation on the Interactive Brokers TWS API — an overnight-reopen
strategy that trades the 6 PM session open.

## The strategy

Documented in `docs/NASDAQ 100 E-mini Futures 6PM Reopen Strategy.pdf`: the thesis is that the
overnight reopen starts moves that continue through the London and New York sessions, so the
system is built to hold through them rather than scalp. Partial exits at prior highs and lows
are supported but deliberately not the default — taking them usually forfeits the move the
strategy exists to capture.

## Structure

```
src/execution/     order placement and lifecycle
src/               strategy, risk and connection layers
config/            strategy parameters
deploy/            systemd unit for running it as a service
docs/              strategy specification
tests/
```

60 Python source files. `run_strategy.py` is the entry point.

## Running as a service

`deploy/nq_strategy.service` is a systemd unit with `Restart=on-failure` — it recovers from
crashes without restarting after a deliberate shutdown. Set paths and `EnvironmentFile` for
your own host; the committed version uses neutral placeholders.

## Honest status

Delivered under a commercial engagement and published with **client identifiers, runtime
session logs, live trade records and account references removed**. The repository is source,
configuration and documentation only.

**2 test files across 60 source files.** For futures automation placing live orders, the
execution and risk paths are what should carry real coverage — that is the gap here, stated
rather than glossed.

No performance results are published. The strategy document describes intent, not outcomes.

**Tech:** Python, ib_insync / IBKR TWS API, systemd
