"""
Broker Abstraction Layer
========================
Abstract interfaces for broker connections, order management, and market data.

To add a new broker:
1. Create a new directory: src/broker/your_broker/
2. Implement BaseConnection, BaseMarketData, and the component classes (order placer, tracker, position reader)
3. Add your broker to the factory in broker_factory.py
4. Set broker: "your_broker" in strategy_config.yaml

Current brokers:
- ibkr: Interactive Brokers (TWS / Gateway via ib_insync)
"""
