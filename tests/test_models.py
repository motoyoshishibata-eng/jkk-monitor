from src.models import Listing


def test_key_uses_jkk_id_when_present():
    listing = Listing(name="○○ハイツ", room="201", rent=85000, jkk_id="ABC123")
    assert listing.key == "jkk:ABC123"


def test_key_falls_back_to_hash():
    listing = Listing(name="○○ハイツ", room="201", rent=85000)
    assert len(listing.key) == 16


def test_key_is_stable_for_same_attributes():
    a = Listing(name="○○ハイツ", room="201", rent=85000)
    b = Listing(name="○○ハイツ", room="201", rent=85000)
    assert a.key == b.key


def test_key_changes_when_attributes_differ():
    a = Listing(name="○○ハイツ", room="201", rent=85000)
    b = Listing(name="○○ハイツ", room="202", rent=85000)
    assert a.key != b.key
