import threading
from datetime import datetime, time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set
from zoneinfo import ZoneInfo

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.ticktype import TickTypeEnum
from ibapi.wrapper import EWrapper

from futu_tracker.futunn_monitor import PortfolioSnapshot


class _IBApp(EWrapper, EClient):
    def __init__(self) -> None:
        EClient.__init__(self, self)
        self._next_order_id: Optional[int] = None
        self._next_order_id_event = threading.Event()
        self._next_order_id_lock = threading.Lock()
        self._positions_event = threading.Event()
        self._positions_lock = threading.Lock()
        self._positions: Dict[str, float] = {}
        self._next_req_id = 10_000
        self._next_req_id_lock = threading.Lock()
        self._market_events: Dict[int, threading.Event] = {}
        self._market_data: Dict[int, Dict[str, float]] = {}
        self._market_lock = threading.Lock()

    def nextValidId(self, orderId: int) -> None:  # noqa: N802
        self._next_order_id = orderId
        self._next_order_id_event.set()

    def error(self, reqId: int, errorCode: int, errorString: str, advancedOrderRejectJson: str = "") -> None:  # noqa: N803
        print(f"[IBKR][error] reqId={reqId} code={errorCode} msg={errorString}")

    def position(self, account: str, contract: Contract, position: float, avgCost: float) -> None:  # noqa: N803
        symbol = (contract.symbol or "").upper()
        if not symbol:
            return
        with self._positions_lock:
            self._positions[symbol] = self._positions.get(symbol, 0.0) + position

    def positionEnd(self) -> None:  # noqa: N802
        self._positions_event.set()

    def tickPrice(self, reqId: int, tickType: int, price: float, attrib) -> None:  # noqa: N803
        with self._market_lock:
            if reqId not in self._market_data or price <= 0:
                return
            if tickType == TickTypeEnum.BID:
                self._market_data[reqId]["bid"] = price
            elif tickType == TickTypeEnum.ASK:
                self._market_data[reqId]["ask"] = price
            elif tickType == TickTypeEnum.LAST:
                self._market_data[reqId]["last"] = price
            elif tickType == TickTypeEnum.CLOSE:
                self._market_data[reqId]["close"] = price

    def tickSnapshotEnd(self, reqId: int) -> None:  # noqa: N803
        with self._market_lock:
            event = self._market_events.get(reqId)
        if event is not None:
            event.set()

    def wait_until_ready(self, timeout_seconds: int) -> None:
        if not self._next_order_id_event.wait(timeout_seconds):
            raise TimeoutError("Timed out waiting for IBKR nextValidId callback.")

    def request_positions(self, timeout_seconds: int) -> Dict[str, float]:
        with self._positions_lock:
            self._positions = {}
        self._positions_event.clear()
        self.reqPositions()
        if not self._positions_event.wait(timeout_seconds):
            raise TimeoutError("Timed out waiting for IBKR positions.")
        self.cancelPositions()
        with self._positions_lock:
            return dict(self._positions)

    def next_order_id(self) -> int:
        if self._next_order_id is None:
            raise RuntimeError("IBKR next order id not initialized.")
        with self._next_order_id_lock:
            order_id = self._next_order_id
            self._next_order_id += 1
            return order_id

    def next_req_id(self) -> int:
        with self._next_req_id_lock:
            req_id = self._next_req_id
            self._next_req_id += 1
            return req_id

    def request_reference_price(self, contract: Contract, timeout_seconds: int) -> Optional[float]:
        req_id = self.next_req_id()
        event = threading.Event()
        with self._market_lock:
            self._market_events[req_id] = event
            self._market_data[req_id] = {"bid": 0.0, "ask": 0.0, "last": 0.0, "close": 0.0}

        self.reqMktData(req_id, contract, "", True, False, [])
        event.wait(timeout_seconds)
        self.cancelMktData(req_id)

        with self._market_lock:
            data = self._market_data.pop(req_id, None) or {}
            self._market_events.pop(req_id, None)

        bid = data.get("bid", 0.0)
        ask = data.get("ask", 0.0)
        last = data.get("last", 0.0)
        close = data.get("close", 0.0)
        if ask > 0:
            return ask
        if last > 0:
            return last
        if close > 0:
            return close
        if bid > 0:
            return bid
        return None

@dataclass
class RebalanceResult:
    changed: bool
    actions: List[str]


class IBKRTrader:
    def __init__(
        self,
        host: str,
        port: int,
        client_id: int,
        total_amount: float,
        whitelist: Set[str],
        stop_loss_percent: float = 3.0,
        timeout_seconds: int = 10,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.total_amount = total_amount
        self.whitelist = {symbol.upper() for symbol in whitelist}
        self.stop_loss_percent = stop_loss_percent
        self.timeout_seconds = timeout_seconds
        self._app: Optional[_IBApp] = None
        self._network_thread: Optional[threading.Thread] = None

    def connect(self) -> None:
        if self._app is not None and self._app.isConnected():
            return

        self._app = _IBApp()
        self._app.connect(self.host, self.port, self.client_id)
        self._network_thread = threading.Thread(target=self._app.run, daemon=True)
        self._network_thread.start()
        self._app.wait_until_ready(self.timeout_seconds)

    def disconnect(self) -> None:
        if self._app is None:
            return
        if self._app.isConnected():
            self._app.disconnect()
        self._app = None
        self._network_thread = None

    def rebalance_to_snapshot(self, snapshot: PortfolioSnapshot) -> RebalanceResult:
        if not snapshot.symbols:
            return RebalanceResult(changed=False, actions=["No target symbols from Futunn; skipped rebalance."])
        self._ensure_connected()
        assert self._app is not None

        positions = self._app.request_positions(self.timeout_seconds)
        actions: List[str] = []
        per_symbol_amount = self.total_amount / len(snapshot.symbols)

        target_symbols = {symbol.upper() for symbol in snapshot.symbols}
        current_symbols = set(positions.keys())

        removable_symbols = {
            symbol for symbol in current_symbols if symbol not in target_symbols and symbol not in self.whitelist
        }
        for symbol in sorted(removable_symbols):
            qty = int(round(positions.get(symbol, 0.0)))
            if qty == 0:
                continue
            if qty > 0:
                mode = self._place_smart_order(symbol, "SELL", qty)
                actions.append(f"SELL {qty} {symbol} via {mode} (remove non-target).")
            else:
                mode = self._place_smart_order(symbol, "BUY", abs(qty))
                actions.append(f"BUY {abs(qty)} {symbol} via {mode} (cover short non-target).")
            positions[symbol] = 0.0

        for symbol in sorted(target_symbols):
            if symbol in self.whitelist:
                actions.append(f"KEEP {symbol} (whitelist).")
                continue
            price = snapshot.prices.get(symbol, 0.0)
            if price <= 0:
                actions.append(f"SKIP {symbol} (invalid price: {price}).")
                continue

            desired_qty = int(per_symbol_amount / price)
            current_qty = int(round(positions.get(symbol, 0.0)))
            delta = desired_qty - current_qty
            if delta == 0:
                continue
            if delta > 0:
                mode = self._place_smart_order(symbol, "BUY", delta, place_stop_loss=True)
                actions.append(f"BUY {delta} {symbol} via {mode} (target {desired_qty}, current {current_qty}).")
            else:
                mode = self._place_smart_order(symbol, "SELL", abs(delta))
                actions.append(
                    f"SELL {abs(delta)} {symbol} via {mode} (target {desired_qty}, current {current_qty})."
                )
            positions[symbol] = float(desired_qty)

        return RebalanceResult(changed=bool(actions), actions=actions)

    def _ensure_connected(self) -> None:
        if self._app is None or not self._app.isConnected():
            self.connect()

    @staticmethod
    def _is_regular_trading_hours() -> bool:
        ny_tz = ZoneInfo("America/New_York")
        now_et = datetime.now(ny_tz)
        if now_et.weekday() >= 5:
            return False
        current = now_et.time()
        return time(9, 30) <= current <= time(16, 0)

    def _place_smart_order(self, symbol: str, action: str, quantity: int, place_stop_loss: bool = False) -> str:
        if quantity <= 0:
            return "SKIP"
        assert self._app is not None
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"

        order = Order()
        order.action = action
        order.totalQuantity = quantity
        order.tif = "DAY"
        is_rth = self._is_regular_trading_hours()

        if is_rth:
            order.orderType = "MKT"
            order.outsideRth = False
            mode = "MKT"
        else:
            reference_price = self._app.request_reference_price(contract, self.timeout_seconds)
            if reference_price is None:
                raise RuntimeError(f"Cannot get market price for {symbol} outside RTH.")
            order.orderType = "LMT"
            order.lmtPrice = round(reference_price, 4)
            order.outsideRth = True
            mode = f"LMT@{order.lmtPrice}"

        order_id = self._app.next_order_id()
        self._app.placeOrder(order_id, contract, order)
        if place_stop_loss and action == "BUY" and self.stop_loss_percent > 0:
            self._place_trailing_stop_order(contract, quantity)
            return f"{mode}+TRAIL({self.stop_loss_percent}%)"
        return mode

    def _place_trailing_stop_order(self, contract: Contract, quantity: int) -> None:
        assert self._app is not None
        stop_order = Order()
        stop_order.action = "SELL"
        stop_order.orderType = "TRAIL"
        stop_order.totalQuantity = quantity
        stop_order.trailingPercent = self.stop_loss_percent
        stop_order.tif = "GTC"
        stop_order.outsideRth = True
        order_id = self._app.next_order_id()
        self._app.placeOrder(order_id, contract, stop_order)