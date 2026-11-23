def apply_discount(price: float, percent: float) -> float:
    """Return `price` after taking `percent` percent off."""
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    return round(price * percent / 100, 2)
