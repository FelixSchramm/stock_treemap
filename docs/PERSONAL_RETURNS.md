# Persönliche Rendite seit Kauf

`portfolio_dashboard.returns` berechnet ausschließlich unrealisierte EUR-Gewinne
und -Verluste aktueller aktiver Long-Bestände gemäß [ADR 0007](adr/0007-bewertung-und-einstand.md).
Das reine Modul übernimmt unveränderte Ergebnisse der [EUR-Bewertung](EUR_VALUATION.md).
Es lädt keine Daten und führt weder Preisperioden noch weitere Umrechnungen ein.

## Kostenfreigabe und Einzelwerte

`personal_return(position_valuation)` liefert eine `PositionReturn` mit der
ursprünglichen `valuation`, einem `PersonalReturnStatus`, `gain_eur` und
`return_percent`. Prozentwerte sind Prozentpunkte: 20 bedeutet 20 %.

```text
Gewinn/Verlust in EUR = EUR-Wert − verbleibender EUR-Einstand
Seit Kauf in Prozent = (EUR-Wert / verbleibender EUR-Einstand − 1) × 100
```

Nur positive Bestände mit verfügbarer EUR-Bewertung und positivem
`CostBasisStatus.VERIFIED_REMAINING` sind berechenbar. Fehlende, ungültige,
ungeprüfte und nicht positive Einstände haben einen eigenen Nichtverfügbarkeitsgrund;
Gewinn und Prozentwert bleiben `None`. Negative Einstände werden bereits vom
Fachmodell abgelehnt. Ein belegter EUR-Wert null bei positivem geprüftem Einstand
ist dagegen gültig und ergibt −100 %. Fehlende Preise werden nicht zu null.

**Die Bedeutung tatsächlicher Einstandswerte nach Teilverkäufen ist weiterhin
ungeklärt. Reale Einstände bleiben ungeprüft und reale Seit-Kauf-Renditen damit
nicht verfügbar, bis die Kostenbasis fachlich bestätigt wurde.** Die vorhandene
Zahl oder ein aktueller Marktwert begründet keine Kostenfreigabe.

Dividenden, realisierte Verkäufe und Steuern werden nicht hinzugefügt. Gebühren
wirken nur dann mit, wenn sie nachweislich bereits Bestandteil des explizit
geprüften verbleibenden EUR-Einstands sind; es gibt keinen separaten Zuschlag.

## Gemeinsame Summen und Instrumentgruppen

`portfolio_returns(portfolio_valuation)` liefert `PortfolioReturns` mit
`valuation`, allen `positions`, den vorhandenen geprüften Instrument-`groups`
und `total`. Jeder `ReturnAggregate` enthält:

- `members`: sämtliche ursprünglichen Teilpositionsergebnisse, auch unbekannte
  und inaktive;
- `eligible_market_value_eur` und `eligible_cost_basis_eur`: Summen **derselben**
  berechenbaren aktiven Teilmenge, sowie deren `gain_eur`;
- `return_percent`: aus diesen Summen berechnet, ohne Mittelung der
  Einzelprozente oder Verwendung von Marktwertgewichten;
- `active_count`, `eligible_count` und `coverage_fraction`: Anzahlabdeckung der
  akzeptierten aktiven Quellenzeilen. Die Quote behauptet keine Wertabdeckung.

Die Gesamtrendite darf diese gültige Teilmenge beschreiben. Der Gesamtmarktwert
aus `valuation.total_eur` kann weitere Positionen ohne Einstand enthalten und
ist daher separat zu behandeln. Eine Instrumentgruppenrendite erfordert dagegen
gültige Werte und Einstände **aller aktiven Mitglieder**. Sonst ist ihr
`return_percent=None`; die ausdrücklich als Teilmengensummen benannten Felder
und fehlenden Mitglieder bleiben sichtbar. Inaktive Mitglieder blockieren nicht.
Ungeprüfte Identitäten werden auch bei gleichen Zeilennummern oder behaupteten
Instrument-IDs nicht neu zusammengeführt. Wiederholte Quellenpositionen bleiben
einzeln erhalten; dieses Modul dedupliziert keinen Import.

Ohne berechenbare aktive Mitglieder sind Rendite und beide Teilmengensummen
`None`, bei null aktiven Mitgliedern auch die Abdeckungsquote. Vollständige leere,
partielle und fehlgeschlagene Quellen behalten ihren jeweiligen Status.
`PortfolioReturns.status` übernimmt `FAILED` und `PARTIAL`; fehlende
Kostenabdeckung macht auch einen vollständigen Import im Renditeergebnis partiell.
100 % Abdeckung akzeptierter Zeilen macht einen Teilimport nicht vollständig.

Die unveränderte Bewertung hält Kurs-/FX-Quelle, Beobachtungs- und Abrufzeiten
sowie Warnungen zugänglich. Der Vollständigkeitsstatus behauptet keine Aktualität.
Summen und EUR-Differenzen bleiben exakt ohne Cent-Rundung. Die Rechnung teilt
die exakte Differenz durch den Einstand mit Aufruferpräzision/Rundungsmodus und
skaliert dann exakt auf Prozentpunkte; so gehen kleine Gewinne nicht bei
`Wert/Einstand − 1` verloren. Es gelten dieselben Decimal-Bereichsprüfungen wie
bei der EUR-Bewertung; nicht darstellbare Ergebnisse lösen Ausnahmen aus.

Prüfung mit synthetischen Eingaben und gesperrtem IP-Netzwerk:
`uv run --locked pytest tests/test_returns.py`. Das Kernbeispiel 120/100 und
330/300 ergibt 20 %, 10 % und zusammen 12,5 %. Eine weitere Position ohne
Einstand verändert die Gesamtrendite nicht, senkt die Anzahlabdeckung und macht
die Rendite ihrer gemeinsamen Instrumentgruppe nicht verfügbar.
