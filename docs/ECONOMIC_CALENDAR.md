# Wirtschaftstermine auf der Startseite

Der Bereich **Nächste Termine USA & Europa** steht unter der Börsenuhr. Er zeigt,
wann die nächsten marktbewegenden Daten erscheinen, jeweils in Berliner Zeit,
mit Abstand („morgen“, „in 5 Tagen“) und, wenn bekannt, dem Vorwert. Prognosen
zeigt er nicht.

Code: `src/portfolio_dashboard/economic_calendar.py` (Fachlogik, ohne Streamlit),
Anzeige in `start_view._render_events`.

## Quellen

| Quelle | Inhalt | Reichweite |
|---|---|---|
| `economic_events.csv` (gepflegt) | Zinsentscheide Fed und EZB, US-Inflation (CPI), US-Arbeitsmarktbericht, US-PCE-Inflation | bis Ende 2027 |
| Yahoo-Wirtschaftskalender (`yfinance`) | weitere Termine (BIP, ISM, ZEW, Inflation Eurozone/DE, Arbeitslosenquoten), Vorwert und tatsächlicher Wert | etwa 4 Wochen voraus |

Die Liste sorgt dafür, dass der nächste Fed- und EZB-Entscheid immer erscheint,
auch wenn er weiter als vier Wochen entfernt ist oder Yahoo ausfällt.
Gibt es einen Termin in beiden Quellen am selben Tag, gelten Uhrzeit und
„vorläufig“ aus der Liste, die Werte kommen von Yahoo.

Verfolgte Yahoo-Reihen: `YAHOO_EVENTS` im Modul. Yahoo führt keinen ifo-Index,
deshalb fehlt er.

## Terminliste pflegen

Einmal im Jahr, spätestens wenn die Seite „Terminliste nur bis …“ zeigt
(60 Tage vor dem letzten Eintrag):

1. Termine aus den offiziellen Kalendern übernehmen:
   - Fed: <https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm>
     (Entscheid am zweiten Sitzungstag, 14:00 New York; noch nicht bestätigte Termine `tentative=1`)
   - EZB: <https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html> (14:15 Frankfurt)
   - US-Inflation: <https://www.bls.gov/schedule/news_release/cpi.htm> (08:30 New York)
   - US-Arbeitsmarkt: <https://www.bls.gov/schedule/news_release/empsit.htm> (08:30 New York)
   - US-PCE: <https://www.bea.gov/news/schedule/full> (08:30 New York)
2. `SOURCE_DATE` in `economic_calendar.py` anpassen.
3. `uv run pytest tests/test_economic_calendar.py` prüft Spalten, Schlüssel,
   Zeitzonen, Quellen-Domains, Sortierung und Dubletten.

## Grenzen

- Der Yahoo-Kalender ist eine inoffizielle Schnittstelle. Der Länderfilter
  (US, EU, DE) nutzt `yfinance.Calendars._get_data`, weil die öffentliche Methode
  nicht nach Land filtert (sonst etwa 600 statt 50 Zeilen pro Woche). Jeder Fehler
  ergibt ein leeres Ergebnis; die Seite zeigt dann nur die Liste und einen Hinweis.
- Yahoo liefert keine Prognosen und ersetzt den Wert 0 durch „fehlt“.
- Ändert Yahoo die Namen der Reihen, fallen diese Termine still weg; die
  Zinsentscheide und US-Daten aus der Liste bleiben.
- Alles wird für alle Besucher eine Stunde zwischengespeichert.
