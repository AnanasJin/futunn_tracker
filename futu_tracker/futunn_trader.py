from dataclasses import dataclass
from typing import List

from futu_tracker.futunn_monitor import PortfolioSnapshot


@dataclass
class RebalanceResult:
    changed: bool
    actions: List[str]


class FutunnTrader:
    """Placeholder for future Futunn trading implementation."""

    def connect(self) -> None:
        return

    def disconnect(self) -> None:
        return

    def rebalance_to_snapshot(self, snapshot: PortfolioSnapshot) -> RebalanceResult:
        _ = snapshot
        return RebalanceResult(
            changed=False,
            actions=["Futunn trader is not implemented yet."],
        )
