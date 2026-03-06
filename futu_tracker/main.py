import os
import time
from dataclasses import dataclass
from typing import Optional, Set

from futu_tracker.futunn_monitor import FutunnMonitor
from futu_tracker.futunn_trader import FutunnTrader
from futu_tracker.ibkr_trader import IBKRTrader
from futu_tracker.manager import TelegramManager


@dataclass
class AppConfig:
    trader: str
    portfolio_id: str
    total_amount: float
    whitelist: Set[str]
    poll_interval_seconds: int
    ib_host: str
    ib_port: int
    ib_client_id: int
    ib_timeout_seconds: int
    stop_loss_percent: float
    telegram_bot_token: Optional[str]
    telegram_chat_id: Optional[str]

    @staticmethod
    def from_env() -> "AppConfig":
        trader = os.getenv("TRADER", "ibkr").strip().lower()
        portfolio_id = _required_env("PORTFOLIO_ID")
        total_amount = float(_required_env("TOTAL_AMOUNT"))
        whitelist = _parse_csv_symbols(os.getenv("STOCK_WHITELIST", ""))
        poll_interval_seconds = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))
        ib_host = os.getenv("IB_HOST", "ib-gateway")
        ib_port = int(os.getenv("IB_PORT", "4002"))
        ib_client_id = int(os.getenv("IB_CLIENT_ID", "101"))
        ib_timeout_seconds = int(os.getenv("IB_TIMEOUT_SECONDS", "30"))
        stop_loss_percent = float(os.getenv("STOP_LOSS_PERCENT", "3"))
        telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        return AppConfig(
            trader=trader,
            portfolio_id=portfolio_id,
            total_amount=total_amount,
            whitelist=whitelist,
            poll_interval_seconds=poll_interval_seconds,
            ib_host=ib_host,
            ib_port=ib_port,
            ib_client_id=ib_client_id,
            ib_timeout_seconds=ib_timeout_seconds,
            stop_loss_percent=stop_loss_percent,
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=telegram_chat_id,
        )


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required env: {name}")
    return value


def _parse_csv_symbols(raw: str) -> Set[str]:
    symbols = set()
    for token in raw.split(","):
        symbol = token.strip().upper()
        if symbol:
            symbols.add(symbol)
    return symbols


def _format_symbols(symbols: Set[str]) -> str:
    if not symbols:
        return "(empty)"
    return ",".join(sorted(symbols))


def _weights_signature(weights: dict) -> tuple:
    return tuple(sorted((symbol.upper(), round(float(value), 8)) for symbol, value in weights.items()))


def main() -> None:
    config = AppConfig.from_env()
    monitor = FutunnMonitor(portfolio_id=config.portfolio_id)
    if config.trader == "ibkr":
        trader = IBKRTrader(
            host=config.ib_host,
            port=config.ib_port,
            client_id=config.ib_client_id,
            total_amount=config.total_amount,
            whitelist=config.whitelist,
            timeout_seconds=config.ib_timeout_seconds,
            stop_loss_percent=config.stop_loss_percent,
        )
    elif config.trader == "futunn":
        trader = FutunnTrader()
    else:
        raise RuntimeError(f"Unsupported TRADER: {config.trader}. Use ibkr or futunn.")
    telegram_manager: Optional[TelegramManager] = None
    if (config.telegram_bot_token or "").strip():
        telegram_manager = TelegramManager(
            bot_token=config.telegram_bot_token,
            chat_id=config.telegram_chat_id,
            trader=trader,
            poll_interval_seconds=1.0,
        )

    def _send_message(text: str) -> None:
        if telegram_manager is not None:
            telegram_manager.send(text)

    previous_symbols: Optional[Set[str]] = None
    previous_weights_signature: Optional[tuple] = None
    trader.connect()
    if telegram_manager is not None:
        telegram_manager.start()
    _send_message(
        "futunn_tracker started.\n"
        f"trader={config.trader}\n"
        f"portfolio_id={config.portfolio_id}\n"
        f"total_amount={config.total_amount}\n"
        f"whitelist={_format_symbols(config.whitelist)}\n"
        f"stop_loss_percent={config.stop_loss_percent}"
    )

    try:
        while True:
            try:
                snapshot = monitor.fetch_snapshot()
                current_symbols = set(snapshot.symbols)
                current_weights_signature = _weights_signature(snapshot.weights)

                symbols_changed = previous_symbols is None or current_symbols != previous_symbols
                weights_changed = previous_weights_signature is None or current_weights_signature != previous_weights_signature
                if symbols_changed or weights_changed:
                    result = trader.rebalance_to_snapshot(snapshot)
                    if previous_symbols is None:
                        event = "initial sync"
                    elif symbols_changed:
                        event = "symbol changed"
                    else:
                        event = "weight changed"
                    action_text = "\n".join(result.actions) if result.actions else "No order needed."
                    msg = (
                        f"[{event}]\n"
                        f"symbols={_format_symbols(current_symbols)}\n"
                        f"actions:\n{action_text}"
                    )
                    print(msg)
                    _send_message(msg)
                    previous_symbols = current_symbols
                    previous_weights_signature = current_weights_signature

                # Check and reprice any unfilled LMT orders every loop iteration.
                if isinstance(trader, IBKRTrader):
                    try:
                        reprice_actions = trader.update_unfilled_order_prices()
                        if reprice_actions:
                            reprice_text = "\n".join(reprice_actions)
                            msg = f"[reprice unfilled orders]\n{reprice_text}"
                            print(msg)
                            _send_message(msg)
                    except Exception as reprice_exc:
                        print(f"[reprice error] {reprice_exc}")

            except Exception as exc:
                error_msg = f"[loop error] {exc}"
                print(error_msg)
                _send_message(error_msg)
            time.sleep(config.poll_interval_seconds)
    except KeyboardInterrupt:
        print("Exit by keyboard interrupt.")
    finally:
        if telegram_manager is not None:
            telegram_manager.stop()
        trader.disconnect()


if __name__ == "__main__":
    main()
