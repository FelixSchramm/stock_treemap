# Geprüfte Instrument- und Provider-Zuordnung

`portfolio_dashboard.instruments` ergänzt die [Fachmodelle](DOMAIN_MODELS.md)
um einen kleinen, vollständig lokalen Zuordnungsvertrag. Grundlage sind
[ADR 0005](adr/0005-instrumente-und-kursdaten.md) und das allgemeine
[Importschema](DATA_SOURCE.md). Das Modul liest keine Konfiguration oder echten
Bestände und führt keine Provider-Abfragen aus.

## Registry aus ausdrücklich geprüften Einträgen

Die Registry ist ein Tuple von unveränderlichen `VerifiedListing`-Einträgen.
Jeder Eintrag verwendet das bestehende `Instrument` mit einer geprüften,
stabilen `instrument_id` und einem ausdrücklich benannten `trading_venue`.
Eine ID bezeichnet genau ein Wertpapier, eine ETF-Anteilsklasse oder ein
Derivateprodukt. Verschiedene Listings erhalten dieselbe ID nur bei belegter
identischer Instrumentidentität. Zeilennummer, Name und Ticker erzeugen keine ID.

Die Prüfung umfasst folgende Merkmale:

- Aktien verlangen `EquityForm.SHARE` oder `EquityForm.ADR`. Eine ADR ist eine
  eigene Identität und ersetzt niemals automatisch die lokale Aktie.
- ETFs verlangen `share_class` sowie `isin` oder `wkn`. Unterschiedliche
  Anteilsklassen bleiben unterschiedliche Identitäten.
- Derivate verlangen eine eigene `product_id`. Das Quellensymbol kann ein
  Underlying nennen und begründet ausdrücklich keine Provider-Zuordnung.
- `provider_mapping` verbindet genau ein Provider-/Symbol-Paar mit diesem
  Instrument und Listing. Automatische Versorgung verlangt außerdem geprüfte
  Währung und Einheit: `QuoteUnit` einschließlich `scale_to_currency`.
  GBP/Pence und GBP/Pfund sind verschiedene Einheiten; der Faktor ist kein FX-Kurs.

Das Erstellen eines Eintrags ist die ausdrückliche Erklärung des Aufrufers,
dass diese Merkmale extern geprüft wurden. Der Code kann diesen fachlichen
Nachweis nicht selbst erbringen. Es gibt keine automatische Symbolsuche,
ISIN-Ermittlung oder Zuordnung über ähnliche Namen. Tatsächliche Identitäten,
Prüfbelege und Zuordnungen gehören in geschützte Laufzeitkonfiguration oder
ignorierte lokale Dokumentation, niemals in öffentliche Fixtures oder Logs.
Die tatsächlichen Zuordnungen des Besitzers sind weiterhin ungeprüft; ihre
Anbieterabdeckung bleibt in #17 zu klären.

## Lookup und offene Zuordnungen

`resolve_listing(instrument, listings)` vergleicht ausschließlich die genaue ID,
den Instrumenttyp und den Handelsplatz. Bekannte widersprüchliche Einheiten oder
Provider-Zuordnungen verhindern die Auflösung. Ticker und Quellensymbole werden
ignoriert. Texte werden exakt verglichen; der Aufrufer verwendet konsistente,
explizite Provider- und Handelsplatzkennungen.

`lookup_provider_mapping(instrument, listings)` liefert nur die zugehörige
`ProviderMapping`, andernfalls `None`. Fehlende ID, fehlender Handelsplatz,
unbekanntes Listing oder widersprüchliche Metadaten bleiben damit offen.
Ein bereits geprüftes Instrument ohne Anbieterabdeckung darf als Eintrag mit
`provider_mapping=None` bestehen. Das berechtigt zu keinem geratenen Abruf.
Fehlende Einheiten werden auch dann nicht als EUR oder Faktor 1 angenommen.
Der Lookup verändert weder das Eingabeinstrument noch angehängte Bewertungen.

Vor jedem öffentlichen Lookup bzw. jeder Gruppierung wird die gesamte Registry
geprüft. Doppelte ID-/Listing-Paare, widersprüchliche Identitätsmerkmale derselben
ID und mehrfach vergebene Provider-/Symbol-Paare lösen `ValueError` aus.
Es gibt keine von Reihenfolge oder erster Fundstelle abhängige Auswahl.

## Zusammenführung mit erhaltenen Teilpositionen

`group_positions(positions, listings)` liefert ein Tuple von Gruppen; jede Gruppe
ist wiederum ein Tuple der ursprünglichen `Position`-Objekte. Nur Positionen mit
erfolgreich aufgelöster identischer ID dürfen eine Gruppe teilen. Das gilt auch
für extern bestätigte identische Instrumente auf mehreren Listings.
Unaufgelöste Positionen bleiben einzeln, selbst bei gleicher behaupteter ID.

Die vollständigen Teilpositionen dokumentieren Quellenzeile, Importzeit,
Kategorie, Menge, Einstand und einzelne Bewertung samt Quelle und Kurszeitpunkt.
Die Reihenfolge folgt dem ersten Auftreten der Gruppe bzw. der Teilposition.
Es werden keine Mengen, Preise oder Kosten addiert und keine Einheiten über
Listings hinweg verrechnet. Ein manueller Sheet-Preis behält insbesondere
`observed_at=None`, wenn sein tatsächlicher Kursstand unbekannt ist.

Prüfung mit ausschließlich synthetischen Einträgen:
`uv run --locked pytest tests/test_instruments.py`. Die Tests sperren beim
Import und beim Offline-Lookup in einem frischen Prozess Netzwerkzugriffe sowie
UI- und Provider-Pakete. Providerzugriff, Parser, Bewertung und Oberfläche folgen
in ihren eigenen Issues.
