"""通知バックエンドの基底クラス。"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Listing


class Notifier(ABC):
    @abstractmethod
    def send(self, listing: Listing) -> None: ...
