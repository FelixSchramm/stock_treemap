"""Synthetic starter data, independent of UI libraries and external services."""


def demo_rows() -> list[dict[str, str | float]]:
    """Return fresh synthetic rows; never read configuration or contact providers."""
    return [
        {"Instrument": "Demo-Aktie A", "Wert (EUR)": 1200.0},
        {"Instrument": "Demo-Fonds B", "Wert (EUR)": 800.0},
    ]
