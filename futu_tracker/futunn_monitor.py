import time
from dataclasses import dataclass
from typing import Dict, List, Set

import requests


FUTUNN_PORTFOLIO_API = "https://portfolio.futunn.com/portfolio-api/get-portfolio-position"
PRICE_SCALE = 1_000_000_000
FUTUNN_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/143.0.7499.193 Safari/537.36"
    )
}


@dataclass
class PortfolioSnapshot:
    symbols: Set[str]
    prices: Dict[str, float]
    weights: Dict[str, float]


class FutunnMonitor:
    def __init__(self, portfolio_id: str, language: int = 0, timeout_seconds: int = 10) -> None:
        self.portfolio_id = portfolio_id
        self.language = language
        self.timeout_seconds = timeout_seconds

    def fetch_snapshot(self) -> PortfolioSnapshot:
        params = {
            "portfolio_id": self.portfolio_id,
            "language": self.language,
            "_": int(time.time() * 1000),
        }

        response = requests.get(
            FUTUNN_PORTFOLIO_API,
            params=params,
            headers=FUTUNN_REQUEST_HEADERS,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()

        if payload.get("code") != 0:
            raise RuntimeError(f"Futunn API returned error: {payload}")

        record_items: List[dict] = payload.get("data", {}).get("record_items", [])

        symbols: Set[str] = set()
        prices: Dict[str, float] = {}
        weights: Dict[str, float] = {}
        for item in record_items:
            symbol = str(item.get("stock_code", "")).strip().upper()
            if not symbol:
                continue
            symbols.add(symbol)
            prices[symbol] = self._normalize_price(item.get("current_price", 0))
            weights[symbol] = self._normalize_ratio(item.get("total_ratio", 0))

        normalized_weights = self._normalize_weights(weights, symbols)
        return PortfolioSnapshot(symbols=symbols, prices=prices, weights=normalized_weights)

    @staticmethod
    def _normalize_price(raw_price: int) -> float:
        if not raw_price:
            return 0.0
        return float(raw_price) / PRICE_SCALE

    @staticmethod
    def _normalize_ratio(raw_ratio: int) -> float:
        if not raw_ratio:
            return 0.0
        return max(float(raw_ratio) / PRICE_SCALE, 0.0)

    @staticmethod
    def _normalize_weights(weights: Dict[str, float], symbols: Set[str]) -> Dict[str, float]:
        positive_sum = sum(value for value in weights.values() if value > 0)
        if positive_sum > 0:
            return {symbol: max(weights.get(symbol, 0.0), 0.0) / positive_sum for symbol in symbols}

        if not symbols:
            return {}
        equal_weight = 1.0 / len(symbols)
        return {symbol: equal_weight for symbol in symbols}
