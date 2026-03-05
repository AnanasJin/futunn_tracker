from typing import Optional

import requests


class TelegramMessager:
    def __init__(self, bot_token: Optional[str], chat_id: Optional[str], timeout_seconds: int = 10) -> None:
        self.bot_token = (bot_token or "").strip()
        self.chat_id = (chat_id or "").strip()
        self.timeout_seconds = timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text}
        try:
            response = requests.post(url, json=payload, timeout=self.timeout_seconds)
            response.raise_for_status()
            return True
        except requests.RequestException as exc:
            print(f"[Telegram][error] {exc}")
            return False
