"""Raw synthetic development cases; these are not real holdings or mappings.

Strings intentionally preserve missing/zero/negative inputs for importer tests.
Their validation and domain conversion belong to the subsequent import issues.
"""


def synthetic_import_cases() -> list[dict[str, str | None]]:
    """Return independent example rows covering common import boundaries."""
    return [
        {"case": "valid_eur_stock", "type": "stock", "symbol": "DEMO-A",
         "currency": "EUR", "quantity": "10", "cost_eur": "100", "value_eur": "120"},
        {"case": "valid_usd_etf", "type": "etf", "symbol": "DEMO-B",
         "currency": "USD", "quantity": "2.5", "cost_eur": "200", "value_eur": "220"},
        {"case": "manual_derivative", "type": "derivative", "symbol": "DEMO-C",
         "currency": "EUR", "quantity": "3", "cost_eur": None, "value_eur": "45"},
        {"case": "missing_value", "type": "stock", "symbol": "DEMO-D",
         "currency": "GBP", "quantity": "4", "cost_eur": "", "value_eur": None},
        {"case": "zero_holding", "type": "stock", "symbol": "DEMO-E",
         "currency": "EUR", "quantity": "0", "cost_eur": "0", "value_eur": "0"},
        {"case": "negative_holding", "type": "stock", "symbol": "DEMO-F",
         "currency": "EUR", "quantity": "-2", "cost_eur": "20", "value_eur": "-22"},
        {"case": "invalid_cost", "type": "etf", "symbol": "DEMO-G",
         "currency": "EUR", "quantity": "1", "cost_eur": "-10", "value_eur": "30"},
    ]
