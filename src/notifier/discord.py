"""Discord Webhook 通知。"""
from __future__ import annotations

import httpx

from ..models import Listing
from .base import Notifier


class DiscordNotifier(Notifier):
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send(self, listing: Listing) -> None:
        fields: list[dict] = [
            {"name": "部屋", "value": listing.room, "inline": True},
            {"name": "家賃", "value": f"¥{listing.rent:,}", "inline": True},
        ]
        if listing.address:
            fields.append({"name": "住所", "value": listing.address, "inline": False})
        layout_parts: list[str] = []
        if listing.layout:
            layout_parts.append(listing.layout)
        if listing.area_m2:
            layout_parts.append(f"{listing.area_m2}m²")
        if layout_parts:
            fields.append(
                {"name": "間取り", "value": " / ".join(layout_parts), "inline": True}
            )

        embed: dict = {
            "title": f"🏠 新規あき家: {listing.name}",
            "color": 0x00BFFF,
            "fields": fields,
            "timestamp": listing.first_seen_at.isoformat(),
        }
        if listing.url:
            embed["url"] = listing.url

        payload: dict = {"embeds": [embed]}
        if listing.url:
            payload["content"] = f"👉 {listing.url}"

        with httpx.Client(timeout=10) as client:
            response = client.post(self.webhook_url, json=payload)
            response.raise_for_status()
