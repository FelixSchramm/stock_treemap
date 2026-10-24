# Kursperioden (Heute, Woche, Monat, Jahr)

`portfolio_dashboard.price_periods` setzt [ADR 0006](adr/0006-kursentwicklung.md)
als reine Fachlogik um: kein Provider-, Streamlit- oder Systemzeitzugriff.
`price_period_returns(history, quote, sessions, now)` liefert für alle vier
`PricePeriod`-Werte ein `PeriodReturn` mit Prozent `(P_aktuell / P_Referenz − 1) × 100`
in Handelswährung oder „n. v.“ (`percent is None`) mit stabilem `reason`.
Kurs-, Referenz- und Zieldatum sowie die Einheit bleiben im Ergebnis sichtbar.

## Eingaben

- `history`/`quote`: `PriceHistory`/`PriceObservation` aus `market_data`. Beide
  müssen `YAHOO_SPLIT_ADJUSTED_PRICE` tragen (Split-bereinigt, ohne Dividenden);
  die Schlusskurse werden unverändert verglichen und nie ein zweites Mal
  adjustiert. `split_ratio` wird nur als Ereignis geführt, nicht angewendet.
  FX und Dividenden fließen nicht ein.
- `sessions`: `SessionCalendar` mit erwarteten regulären Handelstagen
  (lokale Börsendaten, explizit vom Aufrufer, keine Kalenderbibliothek) sowie
  Öffnungs-/Schlusszeit. Vorzeitige Schlüsse werden nicht modelliert.
- `now`: zeitzonenbewusster Zeitpunkt (injiziert); die Börsenzeitzone stammt
  aus der Historie.

## Regeln

- Läuft die Sitzung (Handelstag, Öffnung <= lokale Zeit < Schluss), zählt der
  Kurs der Quote (muss vom heutigen Tag und innerhalb der Sitzung sein, gleiche
  Einheit/Basis), der unvollständige heutige Historienbalken wird ignoriert.
  Sonst gilt die letzte abgeschlossene Sitzung (Wochenende, Feiertag, vor
  Öffnung, nach Schluss) mit ihrem Schlusskurs.
- Heute: Referenz ist der Schluss der vorherigen erwarteten Sitzung.
- Woche: 7 Kalendertage vor dem Kursdatum; Monat/Jahr: ein Kalendermonat/-jahr
  davor, Tag auf Monatsende begrenzt (29. Februar wird zum 28. Februar).
- Referenz ist der Schluss der letzten erwarteten Sitzung am oder vor dem
  Zieltag. Fehlen Balken erwarteter Sitzungen, werden höchstens
  `MAX_MISSING_SESSIONS` (2) übersprungen; danach `unexpected_gap`. Nicht
  erwartete Tage (Feiertage) zählen nicht als Lücke.

## Gründe für „n. v.“

`history_unavailable`, `unknown_adjustment_basis`, `missing_quote`,
`missing_quote_time`, `stale_quote`, `unit_mismatch`, `calendar_incomplete`,
`missing_current_close`, `missing_reference_close` (Heute), `insufficient_history`
(Historie beginnt nach dem Zieltag, z. B. neu gelistet), `unexpected_gap`,
`zero_reference`.
