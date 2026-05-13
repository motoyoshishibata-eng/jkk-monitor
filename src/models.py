"""データモデル定義。"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Listing(BaseModel):
    """JKK東京 あき家物件1件分。"""

    name: str
    room: str
    rent: int

    address: Optional[str] = None
    layout: Optional[str] = None
    area_m2: Optional[float] = None
    url: Optional[str] = None
    jkk_id: Optional[str] = None

    first_seen_at: datetime = Field(default_factory=datetime.now)

    @property
    def key(self) -> str:
        """物件の一意キー。jkk_id があればそれを優先、なければ属性ハッシュ。"""
        if self.jkk_id:
            return f"jkk:{self.jkk_id}"
        raw = f"{self.name}|{self.room}|{self.rent}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


class State(BaseModel):
    """state.json のスキーマ。"""

    last_checked_at: Optional[datetime] = None
    known_listings: dict[str, Listing] = Field(default_factory=dict)

    # 連続fetch失敗の追跡。5回連続でメール通知（=GitHub Actions失敗）し、
    # その後は復旧するまで通知を抑制する。
    consecutive_failures: int = 0
    failure_notified: bool = False
