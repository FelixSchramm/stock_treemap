# EUR-Bewertung und Gewichtung

`portfolio_dashboard.valuations` berechnet aus den unveränderten
[Fachmodellen](DOMAIN_MODELS.md) und [geprüften Listings](INSTRUMENT_MAPPING.md)
aktive Long-Werte in EUR. Das Modul verwendet nur die Standardbibliothek, liest
keine Konfiguration oder Datenquelle und berechnet keine Renditen oder Einstände.

## Eingaben und Umrechnung

`value_position(position, listings, fx_rate=None)` liefert eine
`PositionValuation` mit dem ursprünglichen `Position`-Objekt, `amount_eur`,
einem `EurValuationStatus` und gegebenenfalls der relevanten FX-Beobachtung.
Der Aufrufer hängt die tatsächlich zum Instrument gehörende Preisbeobachtung an;
dieses Modul sucht oder lädt keinen Kurs.

```text
Wert in EUR = Menge × Preis × scale_to_currency × FX_nach_EUR
```

- Nur positive Mengen sind aktive Long-Positionen. Null- und Negativbestände
  bleiben als `INACTIVE` sichtbar und tragen weder Wert noch Gewicht bei.
- Provider- und Cache-Preise benötigen ein aufgelöstes geprüftes Listing,
  denselben Provider und genau dessen geprüfte Preiseinheit. Widersprüche
  bleiben ausdrücklich nicht bewertbar.
- Sheet-Preise verwenden ihre eigene ausdrücklich angegebene Einheit. Ein
  Sheet-Ersatzpreis mit `QuoteUnit("EUR", Decimal("1"))` erhält keinen weiteren
  FX-Faktor, auch bei einer fremden Handelswährung oder fehlendem Listing.
- Eine unbekannte Währung oder Untereinheit bleibt `UNKNOWN_UNIT`; EUR und
  Faktor 1 werden nicht ergänzt. GBP/Pence verwendet den Einheitenfaktor 0,01
  vor der Umrechnung von GBP nach EUR.
- `FxRate(base_currency, quote_currency, rate, provenance, warnings=())`
  beschreibt Haupteinheiten: `FxRate("USD", "EUR", Decimal("0.8"), provenance)`
  bedeutet 0,8 EUR je USD. Der Kurs muss endlich und positiv sein. Der Aufrufer
  verantwortet den Nachweis der Beobachtung und ihrer Herkunft. Fehlendes FX
  bleibt `MISSING_FX`; die Gegenrichtung wird nicht automatisch invertiert.
  Ein abgelehnter Richtungskandidat bleibt mit Herkunft und Warnungen am Ergebnis.
- Ein belegter Preis null bleibt nach den gleichen Prüfungen ein berechenbarer
  Nullwert. Fehlende oder ungültige Preise bleiben `None` mit eigenem Status.

Produkte und Summen behalten alle Dezimalstellen, auch bei kleinerer
Aufruferpräzision. Es gibt keine Cent-Rundung und keine Float-Konvertierung.
Die Rechnung nutzt den gesamten darstellbaren Decimal-Exponentenbereich.
Überschreitet ein Ergebnis die darstellbaren Grenzen, wird eine Ausnahme
ausgelöst, auch bei deaktivierten Aufrufer-Traps; ein gerundeter Wert, Unendlich
oder ein durch Unterlauf entstandener Nullwert wird nicht als Bewertung ausgegeben.

## Summen, Abdeckung und Quellenstatus

`value_portfolio(snapshot, listings, fx_rates=None)` erhält FX-Kandidaten über
einen Währungsschlüssel, beispielsweise `{"USD": usd_to_eur}`. Die ausgewählte
Beobachtung muss trotzdem exakt von dieser Währung nach EUR zeigen.
Das Ergebnis enthält alle Positionsversuche und Gruppen gemäß
`group_positions`; nur bestätigte identische Instrumente werden zusammengeführt.
Die ursprünglichen Teilpositionen bleiben in jeder Gruppe zugänglich.

`total_eur`, Gruppenwerte und alle `weight_fraction` verwenden genau dieselben
bewertbaren aktiven Teilpositionen. Ein Gewicht ist `Teilwert / total_eur`, kein
Prozentwert. Quotienten verwenden Aufruferpräzision und Rundungsmodus mit vollem
Exponentenbereich; periodische Dezimalbrüche bleiben dadurch präzisionsabhängig.
Bei einer positiven Summe hat ein belegter Nullwert Gewicht null. Bei Summe null
oder fehlender Summe sind Gewichte `None`.

Ohne bewertbare aktive Positionen bleibt die Summe `None`. Ein vollständiger
Import ohne aktive Positionen ergibt dagegen die bekannte leere Summe null.
Ein fehlgeschlagener oder leerer Teilimport wird nicht zum erfolgreichen Nullwert.

`active_count`, `valued_count` und `coverage_fraction` beschreiben die Anzahl
akzeptierter aktiver Zeilen, keine geschätzte Wertabdeckung. Bei null aktiven
Zeilen ist die Abdeckungsquote `None`. Importstatus `FAILED` und `PARTIAL`
bleiben erhalten; fehlende Bewertungen machen einen vollständigen Import im
Bewertungsergebnis zu `PARTIAL`.

Die ursprünglichen Positionen und der Snapshot erhalten Einstand, Warnungen und
alle Herkunfts- und Zeitangaben. `PositionValuation.warnings` verbindet Positions-,
Preis- und relevante FX-Warnungen. Manuelle oder zwischengespeicherte Preise und
unterschiedliche Preis-/FX-Zeitpunkte bleiben sichtbar; der Vollständigkeitsstatus
behauptet keine Aktualität. Einstand wird weder umgedeutet noch ergänzt.

Prüfung mit ausschließlich synthetischen Eingaben und gesperrtem IP-Netzwerk:
`uv run --locked pytest tests/test_valuations.py`.
