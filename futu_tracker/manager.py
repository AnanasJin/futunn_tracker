import threading
import time
from datetime import datetime, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

from telegram import Bot, ReplyKeyboardMarkup, Update
from telegram.error import TelegramError, TimedOut


class TelegramManager:
    _BJ_TZ = ZoneInfo("Asia/Shanghai")

    def __init__(
        self,
        bot_token: Optional[str],
        chat_id: Optional[str],
        trader: object,
        timeout_seconds: int = 10,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self.bot_token = (bot_token or "").strip()
        self.chat_id = (chat_id or "").strip()
        self.trader = trader
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self._offset: Optional[int] = None
        self._bot: Optional[Bot] = Bot(token=self.bot_token) if self.bot_token else None
        self._polling_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._next_daily_positions_push_at: Optional[datetime] = None
        self._command_keyboard = ReplyKeyboardMarkup(
            [["持仓查询", "订单查询"]],
            resize_keyboard=True,
            one_time_keyboard=False,
        )

    @property
    def enabled(self) -> bool:
        return bool(self._bot and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        if self._bot is None:
            return False
        try:
            self._bot.send_message(chat_id=self.chat_id, text=text)
            return True
        except TelegramError as exc:
            print(f"[TelegramManager][error] sendMessage failed: {exc}")
            return False

    def start(self) -> None:
        if not self.enabled:
            return
        if self._polling_thread is not None and self._polling_thread.is_alive():
            return
        self._stop_event.clear()
        self._next_daily_positions_push_at = self._get_next_daily_push_time()
        self._polling_thread = threading.Thread(target=self._polling_loop, daemon=True)
        self._polling_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._polling_thread is not None and self._polling_thread.is_alive():
            self._polling_thread.join(timeout=2)
        self._polling_thread = None

    def poll(self) -> None:
        if not self.enabled:
            return
        updates = self._fetch_updates()
        for item in updates:
            self._offset = item.update_id + 1
            message = item.effective_message
            chat = item.effective_chat
            text = (message.text or "").strip() if message else ""
            incoming_chat_id = str(chat.id).strip() if chat else ""
            if not text or incoming_chat_id != self.chat_id:
                continue
            self._handle_command(text)

    def _polling_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll()
                self._maybe_send_daily_positions()
            except Exception as exc:  # noqa: BLE001
                print(f"[TelegramManager][error] polling loop failed: {exc}")
            self._stop_event.wait(self.poll_interval_seconds)

    def _fetch_updates(self) -> List[Update]:
        if self._bot is None:
            return []
        try:
            return self._bot.get_updates(
                offset=self._offset,
                timeout=0,
                allowed_updates=["message"],
            )
        except TimedOut:
            # Polling timeout is expected in unstable networks; skip noisy logs.
            return []
        except TelegramError as exc:
            print(f"[TelegramManager][error] getUpdates failed: {exc}")
            return []

    def _handle_command(self, text: str) -> None:
        normalized = text.strip().lower()
        if normalized in {"/start", "/menu", "菜单"}:
            self._reply("请选择快捷命令：持仓查询 或 订单查询")
            return
        if normalized in {"持仓查询", "/positions", "/position"}:
            self._reply(self._build_positions_text())
            return
        if normalized in {"订单查询", "/orders", "/order"}:
            self._reply(self._build_orders_text())
            return
        self._reply("支持命令：持仓查询、订单查询\n可发送 /menu 打开快捷按键。")

    def _build_positions_text(self) -> str:
        if not hasattr(self.trader, "get_positions_with_pnl"):
            return "当前交易后端不支持持仓查询。"
        try:
            rows = self.trader.get_positions_with_pnl()
        except Exception as exc:  # noqa: BLE001
            return f"持仓查询失败: {exc}"
        if not rows:
            return "当前无持仓。"
        lines = ["当前持仓与盈亏："]
        total_pnl = 0.0
        for row in rows:
            symbol = row.get("symbol", "")
            quantity = float(row.get("quantity", 0.0) or 0.0)
            market_price = float(row.get("market_price", 0.0) or 0.0)
            avg_cost = float(row.get("avg_cost", 0.0) or 0.0)
            unrealized_pnl = float(row.get("unrealized_pnl", 0.0) or 0.0)
            pnl_percent = float(row.get("pnl_percent", 0.0) or 0.0)
            total_pnl += unrealized_pnl
            lines.append(
                f"{symbol} qty={quantity:g} "
                f"price={market_price:.2f} cost={avg_cost:.2f} "
                f"pnl={unrealized_pnl:+.2f} ({pnl_percent:+.2f}%)"
            )
        lines.append(f"总未实现盈亏: {total_pnl:+.2f}")
        return "\n".join(lines)

    def _build_orders_text(self) -> str:
        if not hasattr(self.trader, "get_unfilled_orders"):
            return "当前交易后端不支持订单查询。"
        try:
            rows = self.trader.get_unfilled_orders()
        except Exception as exc:  # noqa: BLE001
            return f"订单查询失败: {exc}"
        if not rows:
            return "当前无未完成订单。"
        lines = ["当前未完成订单："]
        for row in rows:
            order_id = int(row.get("order_id", 0) or 0)
            symbol = str(row.get("symbol", "")).upper()
            action = str(row.get("action", "")).upper()
            order_type = str(row.get("order_type", "")).upper()
            status = str(row.get("status", "")).upper()
            remaining = float(row.get("remaining", 0.0) or 0.0)
            lmt_price = float(row.get("lmt_price", 0.0) or 0.0)
            if order_type == "LMT" and lmt_price > 0:
                lines.append(
                    f"#{order_id} {symbol} {action} {remaining:g} {order_type}@{lmt_price:.2f} {status}"
                )
            else:
                lines.append(f"#{order_id} {symbol} {action} {remaining:g} {order_type} {status}")
        return "\n".join(lines)

    def _reply(self, text: str) -> None:
        if self._bot is None:
            return
        try:
            self._bot.send_message(chat_id=self.chat_id, text=text, reply_markup=self._command_keyboard)
        except TelegramError as exc:
            print(f"[TelegramManager][error] sendMessage failed: {exc}")

    @classmethod
    def _get_next_daily_push_time(cls, now: Optional[datetime] = None) -> datetime:
        current = now or datetime.now(cls._BJ_TZ)
        today_target = current.replace(hour=8, minute=0, second=0, microsecond=0)
        if current < today_target:
            return today_target
        return today_target + timedelta(days=1)

    def _maybe_send_daily_positions(self) -> None:
        if self._next_daily_positions_push_at is None:
            self._next_daily_positions_push_at = self._get_next_daily_push_time()
        now_bj = datetime.now(self._BJ_TZ)
        if now_bj < self._next_daily_positions_push_at:
            return

        text = "[每日08:00持仓]\n" + self._build_positions_text()
        sent = self.send(text)
        if sent:
            self._next_daily_positions_push_at = self._get_next_daily_push_time(now_bj + timedelta(seconds=1))
            return

        # Retry in 5 minutes if sending failed.
        self._next_daily_positions_push_at = now_bj + timedelta(minutes=5)
