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
        self._open_orders_event = threading.Event()
        self._open_orders_lock = threading.Lock()
        self._open_orders: Dict[int, Dict[str, object]] = {}
        self._contract_details_events: Dict[int, threading.Event] = {}
        self._contract_details_data: Dict[int, List[object]] = {}
        self._contract_details_lock = threading.Lock()

    def nextValidId(self, orderId: int) -> None:  # noqa: N802
        self._next_order_id = orderId
        self._next_order_id_event.set()

    def error(self, reqId: int, errorCode: int, errorString: str, advancedOrderRejectJson: str = "") -> None:  # noqa: N803
        info_codes = {2104, 2106, 2107, 2108, 2119, 2134, 2158}
        warning_codes = {399, 2109, 10329}
        reject_extra = f" reject={advancedOrderRejectJson}" if advancedOrderRejectJson else ""
        if errorCode in info_codes:
            print(f"[IBKR][info] reqId={reqId} code={errorCode} msg={errorString}{reject_extra}")
            return
        if errorCode == 10167:
            print(
                f"[IBKR][error] reqId={reqId} code={errorCode} msg={errorString} "
                f"(realtime market data subscription may be missing).{reject_extra}"
            )
            return
        if errorCode in warning_codes:
            print(f"[IBKR][warn] reqId={reqId} code={errorCode} msg={errorString}{reject_extra}")
            return
        print(f"[IBKR][error] reqId={reqId} code={errorCode} msg={errorString}{reject_extra}")

    def position(self, account: str, contract: Contract, position: float, avgCost: float) -> None:  # noqa: N803
        symbol = (contract.symbol or "").upper()
        if not symbol:
            return
        with self._positions_lock:
            self._positions[symbol] = self._positions.get(symbol, 0.0) + position

    def positionEnd(self) -> None:  # noqa: N802
        self._positions_event.set()

    def openOrder(self, orderId: int, contract: Contract, order: Order, orderState) -> None:  # noqa: N802, N803
        symbol = (contract.symbol or "").upper()
        if not symbol:
            return
        status = (getattr(orderState, "status", "") or "").upper()
        total_qty = float(getattr(order, "totalQuantity", 0.0) or 0.0)
        action = (getattr(order, "action", "") or "").upper()
        order_type = (getattr(order, "orderType", "") or "").upper()
        parent_id = int(getattr(order, "parentId", 0) or 0)
        tif = (getattr(order, "tif", "") or "")
        outside_rth = bool(getattr(order, "outsideRth", False))
        with self._open_orders_lock:
            existing = self._open_orders.get(orderId, {})
            remaining = float(existing.get("remaining", total_qty))
            self._open_orders[orderId] = {
                "symbol": symbol,
                "status": status,
                "remaining": remaining,
                "action": action,
                "order_type": order_type,
                "parent_id": parent_id,
                "tif": tif,
                "outside_rth": outside_rth,
            }

    def orderStatus(  # noqa: N802, N803, PLR0913
        self,
        orderId: int,
        status: str,
        filled: float,
        remaining: float,
        avgFillPrice: float,
        permId: int,
        parentId: int,
        lastFillPrice: float,
        clientId: int,
        whyHeld: str,
        mktCapPrice: float,
    ) -> None:
        del filled, avgFillPrice, permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice
        with self._open_orders_lock:
            existing = self._open_orders.get(orderId, {})
            self._open_orders[orderId] = {
                **existing,
                "symbol": str(existing.get("symbol", "")),
                "status": (status or "").upper(),
                "remaining": float(remaining or 0.0),
            }

    def openOrderEnd(self) -> None:  # noqa: N802
        self._open_orders_event.set()

    def tickPrice(self, reqId: int, tickType: int, price: float, attrib) -> None:  # noqa: N803
        with self._market_lock:
            data = self._market_data.get(reqId)
            if data is None or price <= 0:
                return
            if tickType in (TickTypeEnum.BID, TickTypeEnum.DELAYED_BID):
                data["bid"] = price
            elif tickType in (TickTypeEnum.ASK, TickTypeEnum.DELAYED_ASK):
                data["ask"] = price
            elif tickType in (TickTypeEnum.LAST, TickTypeEnum.DELAYED_LAST) and "last" in data:
                data["last"] = price
            elif tickType in (TickTypeEnum.CLOSE, TickTypeEnum.DELAYED_CLOSE) and "close" in data:
                data["close"] = price

    def tickSnapshotEnd(self, reqId: int) -> None:  # noqa: N803
        with self._market_lock:
            event = self._market_events.get(reqId)
        if event is not None:
            event.set()

    def contractDetails(self, reqId: int, contractDetails) -> None:  # noqa: N802, N803
        with self._contract_details_lock:
            data = self._contract_details_data.get(reqId)
            if data is not None:
                data.append(contractDetails)

    def contractDetailsEnd(self, reqId: int) -> None:  # noqa: N802, N803
        with self._contract_details_lock:
            event = self._contract_details_events.get(reqId)
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

    def next_order_id(self, timeout_seconds: int, refresh_from_ib: bool = True) -> int:
        if refresh_from_ib:
            self._next_order_id_event.clear()
            self.reqIds(1)
            if not self._next_order_id_event.wait(timeout_seconds):
                raise TimeoutError("Timed out waiting for IBKR reqIds/nextValidId callback.")
        elif self._next_order_id is None:
            raise RuntimeError("IBKR next order id not initialized.")
        with self._next_order_id_lock:
            if self._next_order_id is None:
                raise RuntimeError("IBKR next order id not initialized.")
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

        with self._market_lock:
            data = self._market_data.pop(req_id, None) or {}
            self._market_events.pop(req_id, None)

        bid = float(data.get("bid", 0.0) or 0.0)
        ask = float(data.get("ask", 0.0) or 0.0)
        last = float(data.get("last", 0.0) or 0.0)
        close = float(data.get("close", 0.0) or 0.0)
        if bid > 0 and ask > 0:
            return round((bid + ask) / 2, 4)
        if ask > 0:
            return ask
        if bid > 0:
            return bid
        if last > 0:
            return last
        if close > 0:
            return close
        return None

    def request_open_order_symbols(self, timeout_seconds: int) -> Set[str]:
        with self._open_orders_lock:
            self._open_orders = {}
        self._open_orders_event.clear()
        self.reqOpenOrders()
        if not self._open_orders_event.wait(timeout_seconds):
            raise TimeoutError("Timed out waiting for IBKR open orders.")
        active_symbols: Set[str] = set()
        active_statuses = {"PENDINGSUBMIT", "APIPENDING", "PRESUBMITTED", "SUBMITTED"}
        with self._open_orders_lock:
            for item in self._open_orders.values():
                symbol = str(item.get("symbol", "")).upper()
                status = str(item.get("status", "")).upper()
                remaining = float(item.get("remaining", 0.0) or 0.0)
                if symbol and status in active_statuses and remaining > 0:
                    active_symbols.add(symbol)
        return active_symbols

    def refresh_open_orders(self, timeout_seconds: int) -> None:
        with self._open_orders_lock:
            self._open_orders = {}
        self._open_orders_event.clear()
        self.reqOpenOrders()
        if not self._open_orders_event.wait(timeout_seconds):
            raise TimeoutError("Timed out waiting for IBKR open orders.")

    def get_unfilled_lmt_orders(self) -> List[Dict]:
        active_statuses = {"PENDINGSUBMIT", "APIPENDING", "PRESUBMITTED", "SUBMITTED"}
        with self._open_orders_lock:
            result = []
            for order_id, item in self._open_orders.items():
                status = str(item.get("status", "")).upper()
                remaining = float(item.get("remaining", 0.0) or 0.0)
                order_type = str(item.get("order_type", "")).upper()
                symbol = str(item.get("symbol", "")).upper()
                if symbol and status in active_statuses and remaining > 0 and order_type == "LMT":
                    result.append({"order_id": order_id, **item})
            return result

    def get_active_orders_for_symbol(self, symbol: str) -> List[Dict]:
        active_statuses = {"PENDINGSUBMIT", "APIPENDING", "PRESUBMITTED", "SUBMITTED"}
        normalized_symbol = symbol.upper()
        with self._open_orders_lock:
            result = []
            for order_id, item in self._open_orders.items():
                item_symbol = str(item.get("symbol", "")).upper()
                status = str(item.get("status", "")).upper()
                remaining = float(item.get("remaining", 0.0) or 0.0)
                if item_symbol == normalized_symbol and status in active_statuses and remaining > 0:
                    result.append({"order_id": order_id, **item})
            return result

    def get_active_orders(self) -> List[Dict]:
        active_statuses = {"PENDINGSUBMIT", "APIPENDING", "PRESUBMITTED", "SUBMITTED"}
        with self._open_orders_lock:
            result = []
            for order_id, item in self._open_orders.items():
                status = str(item.get("status", "")).upper()
                remaining = float(item.get("remaining", 0.0) or 0.0)
                if status in active_statuses and remaining > 0:
                    result.append({"order_id": order_id, **item})
            return result

    def cancel_order(self, order_id: int) -> None:
        try:
            # ibapi>=10.19 uses cancelOrder(orderId, manualOrderCancelTime)
            self.cancelOrder(order_id, "")
        except TypeError:
            # Backward compatibility for older ibapi signature.
            self.cancelOrder(order_id)

    def request_bid_ask(self, contract: Contract, timeout_seconds: int) -> tuple:
        req_id = self.next_req_id()
        event = threading.Event()
        with self._market_lock:
            self._market_events[req_id] = event
            self._market_data[req_id] = {"bid": 0.0, "ask": 0.0}

        self.reqMktData(req_id, contract, "", True, False, [])
        event.wait(timeout_seconds)

        with self._market_lock:
            data = self._market_data.pop(req_id, None) or {}
            self._market_events.pop(req_id, None)

        bid = float(data.get("bid", 0.0) or 0.0)
        ask = float(data.get("ask", 0.0) or 0.0)
        return (bid if bid > 0 else None, ask if ask > 0 else None)

    def request_primary_exchange(self, contract: Contract, timeout_seconds: int) -> Optional[str]:
        req_id = self.next_req_id()
        event = threading.Event()
        with self._contract_details_lock:
            self._contract_details_events[req_id] = event
            self._contract_details_data[req_id] = []

        self.reqContractDetails(req_id, contract)
        event.wait(timeout_seconds)

        with self._contract_details_lock:
            details = self._contract_details_data.pop(req_id, [])
            self._contract_details_events.pop(req_id, None)

        for item in details:
            item_contract = getattr(item, "contract", None)
            primary_exchange = (getattr(item_contract, "primaryExchange", "") or "").strip().upper()
            if primary_exchange:
                return primary_exchange
        return None

@dataclass
class RebalanceResult:
    changed: bool
    actions: List[str]


class IBKRTrader:
    _ET_TZ = ZoneInfo("America/New_York")
    _STOP_LOSS_ORDER_TYPES = {"TRAIL", "STP", "STP LMT"}

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
        self._primary_exchange_cache: Dict[str, Optional[str]] = {}
        self._app: Optional[_IBApp] = None
        self._network_thread: Optional[threading.Thread] = None
        self._protective_sell_order_types = {"TRAIL", "STP", "STP LMT"}

    def connect(self) -> None:
        if self._app is not None and self._app.isConnected():
            return

        self._app = _IBApp()
        self._app.connect(self.host, self.port, self.client_id)
        self._network_thread = threading.Thread(target=self._app.run, daemon=True)
        self._network_thread.start()
        self._app.wait_until_ready(self.timeout_seconds)
        # Use realtime market data (type 1).
        self._app.reqMarketDataType(1)

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
        symbols_with_open_orders = self._request_blocking_open_order_symbols()
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
                actions.extend(self._sync_stop_loss_quantity(symbol, 0))
                symbols_with_open_orders = self._request_blocking_open_order_symbols()
            if symbol in symbols_with_open_orders:
                actions.append(f"SKIP {symbol} (existing unfilled order).")
                continue
            if qty > 0:
                mode = self._place_smart_order(symbol, "SELL", qty)
                actions.append(f"SELL {qty} {symbol} via {mode} (remove non-target).")
            else:
                mode = self._place_smart_order(symbol, "BUY", abs(qty))
                actions.append(f"BUY {abs(qty)} {symbol} via {mode} (cover short non-target).")
            symbols_with_open_orders.add(symbol)
            positions[symbol] = 0.0

        for symbol in sorted(target_symbols):
            if symbol in self.whitelist:
                actions.append(f"KEEP {symbol} (whitelist).")
                continue
            contract = self._build_stock_contract(symbol)
            price = self._app.request_reference_price(contract, self.timeout_seconds)
            if price is None or price <= 0:
                actions.append(f"SKIP {symbol} (cannot get IB market price).")
                continue

            desired_qty = int(per_symbol_amount / price)
            current_qty = int(round(positions.get(symbol, 0.0)))
            delta = desired_qty - current_qty
            if symbol in symbols_with_open_orders:
                actions.append(f"SKIP {symbol} (existing unfilled order).")
                continue
            if delta > 0:
                mode = self._place_smart_order(
                    symbol,
                    "BUY",
                    delta,
                    reference_price_hint=price,
                )
                actions.append(f"BUY {delta} {symbol} via {mode} (target {desired_qty}, current {current_qty}).")
            elif delta < 0:
                mode = self._place_smart_order(
                    symbol,
                    "SELL",
                    abs(delta),
                    reference_price_hint=price,
                )
                actions.append(
                    f"SELL {abs(delta)} {symbol} via {mode} (target {desired_qty}, current {current_qty})."
                )
            actions.extend(self._sync_stop_loss_quantity(symbol, desired_qty))
            symbols_with_open_orders = self._request_blocking_open_order_symbols()
            positions[symbol] = float(desired_qty)

        return RebalanceResult(changed=bool(actions), actions=actions)

    def update_unfilled_order_prices(self) -> List[str]:
        """Check all unfilled LMT orders and reprice each to (bid+ask)/2."""
        self._ensure_connected()
        assert self._app is not None

        self._app.refresh_open_orders(self.timeout_seconds)
        unfilled = self._app.get_unfilled_lmt_orders()
        if not unfilled:
            return []

        actions: List[str] = []
        for order_info in unfilled:
            order_id: int = order_info["order_id"]
            symbol: str = order_info["symbol"]
            remaining: float = float(order_info["remaining"])
            action: str = order_info["action"]
            tif: str = order_info.get("tif", "OND") or "OND"
            outside_rth: bool = bool(order_info.get("outside_rth", True))

            contract = self._build_stock_contract(symbol)
            bid, ask = self._app.request_bid_ask(contract, self.timeout_seconds)

            if bid is None or ask is None:
                actions.append(f"SKIP reprice {symbol} order#{order_id} (no bid/ask available).")
                continue

            mid_price = round((bid + ask) / 2, 2)

            new_order = Order()
            new_order.action = action
            new_order.orderType = "LMT"
            new_order.totalQuantity = remaining
            new_order.lmtPrice = mid_price
            new_order.tif = tif
            new_order.outsideRth = outside_rth
            new_order.transmit = True
            self._sanitize_order_for_gateway(new_order)
            self._app.placeOrder(order_id, contract, new_order)

        return actions

    def _ensure_connected(self) -> None:
        if self._app is None or not self._app.isConnected():
            self.connect()

    @staticmethod
    def _now_et() -> datetime:
        # ZoneInfo("America/New_York") automatically handles DST/EST transitions.
        return datetime.now(IBKRTrader._ET_TZ)

    @staticmethod
    def _is_regular_trading_hours() -> bool:
        now_et = IBKRTrader._now_et()
        if now_et.weekday() >= 5:
            return False
        current = now_et.time()
        return time(9, 30) <= current <= time(16, 0)

    @staticmethod
    def _is_overnight_trading_hours() -> bool:
        """Return True only during IBKR overnight session (ET)."""
        now_et = IBKRTrader._now_et()
        current = now_et.time()
        weekday = now_et.weekday()  # Monday=0, Sunday=6

        # Overnight spans across midnight:
        # - evening leg: 20:00~24:00 on Sun-Thu
        # - early-morning leg: 00:00~03:50 on Mon-Fri
        evening_leg = weekday in {6, 0, 1, 2, 3} and current >= time(20, 0)
        early_morning_leg = weekday in {0, 1, 2, 3, 4} and current < time(3, 50)
        return evening_leg or early_morning_leg

    def _place_smart_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        place_stop_loss: bool = False,
        reference_price_hint: Optional[float] = None,
    ) -> str:
        if quantity <= 0:
            return "SKIP"
        assert self._app is not None
        contract = self._build_stock_contract(symbol)

        order = Order()
        order.action = action
        order.totalQuantity = quantity
        is_rth = self._is_regular_trading_hours()
        is_overnight = self._is_overnight_trading_hours()

        reference_price = self._app.request_reference_price(contract, self.timeout_seconds)
        if reference_price is None and reference_price_hint and reference_price_hint > 0:
            reference_price = reference_price_hint
        if reference_price is None:
            raise RuntimeError(f"Cannot get market price for {symbol}.")
        reference_price = round(reference_price, 2)

        if is_rth:
            order.orderType = "MKT"
            order.tif = "DAY"
            order.outsideRth = True
            mode = "MKT"
        else:
            order.orderType = "LMT"
            order.lmtPrice = reference_price
            # Use OND only in the real overnight session; pre/post market stays on SMART with DAY.
            order.tif = "OND"
            order.outsideRth = True
            mode = f"LMT@{order.lmtPrice}"

        self._sanitize_order_for_gateway(order)
        if place_stop_loss and action == "BUY" and self.stop_loss_percent > 0:
            parent_order_id = self._app.next_order_id(self.timeout_seconds)
            order.transmit = False
            self._app.placeOrder(parent_order_id, contract, order)
            self._place_trailing_stop_order(contract, quantity, parent_order_id=parent_order_id)
            return f"{mode}+TRAIL({self.stop_loss_percent}%)"
        order_id = self._app.next_order_id(self.timeout_seconds)
        order.transmit = True
        self._app.placeOrder(order_id, contract, order)
        return mode

    def _resolve_primary_exchange(self, symbol: str) -> Optional[str]:
        normalized_symbol = symbol.upper()
        if normalized_symbol in self._primary_exchange_cache:
            return self._primary_exchange_cache[normalized_symbol]

        assert self._app is not None
        lookup_contract = Contract()
        lookup_contract.symbol = normalized_symbol
        lookup_contract.secType = "STK"
        lookup_contract.exchange = "SMART"
        lookup_contract.currency = "USD"
        primary_exchange = self._app.request_primary_exchange(lookup_contract, self.timeout_seconds)
        self._primary_exchange_cache[normalized_symbol] = primary_exchange
        return primary_exchange

    def _build_stock_contract(self, symbol: str) -> Contract:
        normalized_symbol = symbol.upper()
        contract = Contract()
        contract.symbol = normalized_symbol
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"
        primary_exchange = self._resolve_primary_exchange(normalized_symbol)
        if primary_exchange and self._is_overnight_trading_hours():
            contract.exchange = "OVERNIGHT"
            contract.primaryExchange = primary_exchange
        return contract

    def _place_trailing_stop_order(
        self,
        contract: Contract,
        quantity: int,
        parent_order_id: Optional[int] = None,
        order_id: Optional[int] = None,
    ) -> int:
        assert self._app is not None
        stop_order = Order()
        stop_order.action = "SELL"
        stop_order.orderType = "TRAIL"
        stop_order.totalQuantity = quantity
        stop_order.trailingPercent = self.stop_loss_percent
        stop_order.tif = "GTC"
        # TRAIL + SMART often ignores outsideRth and emits warning 2109, so leave default.
        stop_order.outsideRth = False
        if parent_order_id is not None:
            stop_order.parentId = parent_order_id
        stop_order.transmit = True
        self._sanitize_order_for_gateway(stop_order)
        if order_id is None:
            order_id = self._app.next_order_id(self.timeout_seconds)
        self._app.placeOrder(order_id, contract, stop_order)
        return order_id

    def _request_blocking_open_order_symbols(self) -> Set[str]:
        assert self._app is not None
        self._app.refresh_open_orders(self.timeout_seconds)
        active_symbols: Set[str] = set()
        for order_info in self._app.get_active_orders():
            symbol = str(order_info.get("symbol", "")).upper()
            if not symbol:
                continue
            if self._is_protective_exit_order(order_info):
                continue
            active_symbols.add(symbol)
        return active_symbols

    def _is_protective_exit_order(self, order_info: Dict[str, object]) -> bool:
        action = str(order_info.get("action", "")).upper()
        order_type = str(order_info.get("order_type", "")).upper()
        parent_id = int(order_info.get("parent_id", 0) or 0)
        if action != "SELL":
            return False
        return order_type in self._protective_sell_order_types or parent_id > 0

    def _sync_stop_loss_quantity(self, symbol: str, target_qty: int) -> List[str]:
        assert self._app is not None
        self._app.refresh_open_orders(self.timeout_seconds)
        active_orders = self._app.get_active_orders_for_symbol(symbol)
        stop_orders = [
            order_info
            for order_info in active_orders
            if str(order_info.get("action", "")).upper() == "SELL"
            and str(order_info.get("order_type", "")).upper() in self._STOP_LOSS_ORDER_TYPES
        ]
        actions: List[str] = []
        if target_qty <= 0:
            for order_info in stop_orders:
                order_id = int(order_info["order_id"])
                self._app.cancel_order(order_id)
                actions.append(f"CANCEL {symbol} stop-loss order#{order_id}.")
            return actions

        if not stop_orders:
            contract = self._build_stock_contract(symbol)
            new_order_id = self._place_trailing_stop_order(contract, target_qty)
            actions.append(
                f"PLACE {symbol} stop-loss order#{new_order_id} TRAIL {self.stop_loss_percent}% qty={target_qty}."
            )
            return actions

        primary_order = stop_orders[0]
        primary_order_id = int(primary_order["order_id"])
        contract = self._build_stock_contract(symbol)
        self._place_trailing_stop_order(contract, target_qty, order_id=primary_order_id)
        actions.append(f"UPDATE {symbol} stop-loss order#{primary_order_id} qty={target_qty}.")
        for order_info in stop_orders[1:]:
            extra_order_id = int(order_info["order_id"])
            self._app.cancel_order(extra_order_id)
            actions.append(f"CANCEL {symbol} extra stop-loss order#{extra_order_id}.")
        return actions

    @staticmethod
    def _sanitize_order_for_gateway(order: Order) -> None:
        # Some IB Gateway/TWS versions reject these legacy flags when they are truthy.
        if hasattr(order, "eTradeOnly"):
            order.eTradeOnly = False
        if hasattr(order, "firmQuoteOnly"):
            order.firmQuoteOnly = False