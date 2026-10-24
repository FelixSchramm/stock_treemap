# Fachlicher Datenvertrag

`portfolio_dashboard.models` definiert unveränderliche Python-Dataclasses und
Enums für Import, spätere Berechnungen und Oberfläche. Das Modul verwendet nur
die Standardbibliothek; es parst keine Zellen, lädt keine Daten, ordnet keine
Instrumente automatisch zu und berechnet keine Werte oder Renditen.

- `Position` enthält Name, Menge, Kategorie, Instrument, EUR-Einstand,
  Quellenzeile und Importzeitpunkt. Mengen und Finanzwerte sind endliche
  `Decimal`-Werte ohne automatische Rundung oder Float-Konvertierung.
  `activity` unterscheidet positive, Null- und negative Bestände. Negative
  Mengen behalten ihr Vorzeichen; nur `ACTIVE` bezeichnet aktive Long-Bestände.
- `Instrument` trennt Anzeige-Ticker, Quellensymbol, Handelsplatz und geprüfte
  `instrument_id`. Eine unbekannte ID bleibt `None`. Namen, Ticker und
  Zeilennummern erzeugen keine Identität und keine Zusammenführung. Eine
  `ProviderMapping` enthält nur eine explizit geprüfte Provider-/Symbol-Zuordnung
  und benötigt eine geprüfte ID. Die Prüfung selbst erfolgt außerhalb des Modells.
- `QuoteUnit` enthält Währung und Faktor zur Währungs-Haupteinheit. GBP mit
  `Decimal('0.01')` bezeichnet Pence; GBP mit `Decimal('1')` Pfund. Unbekannte
  Währungen bleiben `None`; ist nur die Währung bekannt, bleibt deren
  `scale_to_currency=None`. EUR und Faktor 1 werden niemals stillschweigend
  angenommen. Dieser Faktor ist kein FX-Kurs.
- `Valuation` ist eine einzelne Preisbeobachtung pro Stück. `AVAILABLE` mit
  `Decimal('0')` ist ein belegter Nullpreis. `MISSING` und `INVALID` enthalten
  keinen nutzbaren Preis (`None`). Die Herkunft unterscheidet Provider,
  zwischengespeicherten Providerwert und Sheet. `retrieved_at` bezeichnet den
  Abruf, `observed_at` den tatsächlichen Kursstand; ein unbekannter Kursstand
  bleibt `None`. Ein EUR-Sheet-Ersatzkurs trägt explizit die Einheit EUR und kann
  von der Handelswährung des Instruments abweichen. `Position.valuation=None`
  bedeutet, dass noch keine Preisbeobachtung angehängt wurde.
- `CostBasis` unterscheidet fehlenden, ungültigen, ungeprüften und als
  verbleibenden Einstand bestätigten EUR-Einstand. Ein bekannter Importwert wird
  als `UNVERIFIED` übergeben. Erst `VERIFIED_REMAINING` mit positivem Betrag
  erfüllt die Kostenvoraussetzung für „Seit Kauf“. Menge, Preis, Einheit und FX
  benötigen danach weiterhin eigene Prüfungen. Einstand wird nicht aus Kursen
  erfunden. Die tatsächliche Einstandssemantik des Besitzers ist weiterhin
  ungeprüft; reale Seit-Kauf-Renditen bleiben damit nicht berechenbar.
- `DataWarning` transportiert einen Code, eine bereinigte Erklärung und optional
  Feld/Quellenzeile. Rohzellen, Dokumentkennungen und Credentials gehören nicht
  in die Erklärung. Warnungen können an Position, Preis und Snapshot hängen.
- `PortfolioSnapshot` unterscheidet vollständigen Import, Teilimport und
  fehlgeschlagenen Abruf. Ein vollständiger Import darf leer sein. Teilimporte
  enthalten nur akzeptierte Positionen und benötigen eine Erklärung; ein
  fehlgeschlagener Abruf enthält keine neu importierten Positionen und ebenfalls
  eine Erklärung. Ein vorheriger erfolgreicher Stand wird außerhalb dieses
  Vertrags im Adapter/Cache gehalten. Positionen werden niemals automatisch
  gruppiert, auch bei gleicher bestätigter Instrument-ID (mehrere Teilpositionen).

Alle bekannten Zeitpunkte müssen eine Zeitzone enthalten. Die Modelle setzen
keine Zeitpunkte aus der aktuellen Uhr, der Sheet-Zeitzone oder dem Abrufdatum
als tatsächlichen Kurszeitpunkt ein. Die reine
[EUR-Bewertung](EUR_VALUATION.md) ergänzt gerichtetes FX, Summen und Gewichte;
Providerzugriff, Parser und Renditen folgen in ihren eigenen Issues.

Synthetisches Beispiel, vollständig offline:

```python
from datetime import datetime, timezone
from decimal import Decimal
from portfolio_dashboard.models import (
    CostBasis, CostBasisStatus, Instrument, InstrumentType, Position,
    Provenance, QuoteUnit, Valuation, ValuationSource, ValueStatus,
)

imported_at = datetime(2026, 1, 5, 15, 30, tzinfo=timezone.utc)
position = Position(
    name="Synthetische Aktie A",
    quantity=Decimal("1.125"),
    instrument=Instrument(InstrumentType.STOCK, display_ticker="DEMO"),
    category="Demo",
    source_row=3,
    imported_at=imported_at,
    cost_basis=CostBasis(Decimal("100"), CostBasisStatus.UNVERIFIED),
    valuation=Valuation(
        Decimal("0"), ValueStatus.AVAILABLE,
        Provenance(ValuationSource.SHEET, imported_at),
        QuoteUnit("EUR", Decimal("1")),
    ),
)
assert position.instrument.instrument_id is None
assert position.valuation.price == Decimal("0")
assert position.valuation.provenance.observed_at is None
assert not position.cost_basis.usable_for_since_purchase
```

Prüfung: `uv run --locked pytest tests/test_models.py`. Die synthetischen Tests
prüfen auch den Import in einem frischen Prozess mit gesperrtem Netzwerk sowie
gesperrten UI-/Provider-Paketen.
