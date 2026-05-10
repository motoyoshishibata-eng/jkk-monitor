"""新規物件・消失物件の検出。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import Listing, State


@dataclass
class DiffResult:
    new: list[Listing]
    disappeared: list[Listing]
    unchanged: list[Listing]


def detect(current: list[Listing], state: State) -> DiffResult:
    known = state.known_listings
    current_keys = {listing.key for listing in current}

    new: list[Listing] = []
    unchanged: list[Listing] = []
    for listing in current:
        if listing.key in known:
            unchanged.append(listing)
        else:
            new.append(listing)

    disappeared = [v for k, v in known.items() if k not in current_keys]
    return DiffResult(new=new, disappeared=disappeared, unchanged=unchanged)


def update_state(state: State, current: list[Listing]) -> State:
    """検出後のstate更新。既知物件は first_seen_at を維持する。"""
    new_known: dict[str, Listing] = {}
    for listing in current:
        key = listing.key
        if key in state.known_listings:
            existing = state.known_listings[key]
            new_known[key] = listing.model_copy(
                update={"first_seen_at": existing.first_seen_at}
            )
        else:
            new_known[key] = listing
    return State(last_checked_at=datetime.now(), known_listings=new_known)
