import pytest

from discount import apply_discount


def test_quarter_off():
    assert apply_discount(200, 25) == 150


def test_no_discount_keeps_price():
    assert apply_discount(80, 0) == 80


def test_full_discount_is_free():
    assert apply_discount(50, 100) == 0


def test_rejects_bad_percent():
    with pytest.raises(ValueError):
        apply_discount(10, 120)
