# ADR 0012: Sheet als Tabellenmodell mit ISIN-Schlüssel und Transaktionsjournal

- **Datum:** 2026-11-08
- **Status:** Angenommen; am 08.11.2026 umgesetzt (siehe Nachtrag)
- **Herkunft:** Besitzerwunsch nach eindeutigen IDs und einem aufgeräumten Datenschema; Besitzerentscheidung vom 08.11.2026 für eine einzige Zuordnungsablage im Sheet (Variante A). Tabellenschnitt und Prüfregeln technisch.
- **Ersetzt teilweise:** [0005](0005-instrumente-und-kursdaten.md) (Ablageort der geprüften Kurszuordnung), [0007](0007-bewertung-und-einstand.md) (Herkunft des Einstands), [0011](0011-watchlist.md) (Zuordnung im Watchlist-Blatt)

## Kontext

Das Besitzer-Sheet ist über die Zeit gewachsen. Dasselbe Wertpapier wird je Blatt anders erkannt: im Depotblatt über den Namen und optional die ISIN, in den Käufen über die ISIN, in den Verkäufen über Name und Google-Finance-Symbol, in der Watchlist über das Yahoo-Symbol. Namen sind kein stabiler Schlüssel; bei Open-End-Derivaten ändert sich etwa die Knock-out-Schwelle im Namen.

Die Kurszuordnung eines Wertpapiers kann an bis zu vier Stellen stehen: `[[listing]]`, `[[fundamental]]`, Depotspalten U–X und Watchlist-Spalten. Stückzahl und Einstand werden von Hand gepflegt, obwohl sie aus Käufen und Verkäufen folgen, und können davon abweichen. Eingaben, Formeln, Summenzeilen und Auswertungen stehen im selben Blatt, weshalb der Parser Summenzeilen heuristisch erkennen muss. Verknüpfungen im Code laufen teilweise über Zeilennummern.

Bestände lassen sich vollständig aus einem Journal ableiten, sofern Umtausch, Kapitalmaßnahmen und Knock-outs als eigene Ereignisse erfasst sind.

## Entscheidung

**Ein Blatt ist eine Tabelle.** Zeile 1 enthält Spaltennamen in Kleinschreibung mit Unterstrich, wie bereits in den `agent_*`-Blättern. Eingabeblätter enthalten keine Formeln, keine Summen und keine Trennzeilen. Ansichten und Auswertungen liegen in eigenen Blättern und werden von der App nicht gelesen.

**Schlüssel.**

- Wertpapier: die **ISIN**. Sie ist die `instrument_id` und Pflicht für Aktien, ETFs und Derivate. Es gibt keine zusätzliche Wertpapier-ID und keine `yahoo-symbol:`-Ersatz-IDs mehr.
- Transaktion: **`txn_id`**, fortlaufend (`T00001`, `T00002`, …), nie wiederverwendet oder umnummeriert.
- Position: keine eigene ID; sie ist die Summe der Transaktionen je ISIN.
- Zeilennummern dienen nur der Fehlerlokalisierung in Warnungen, nie als Schlüssel im Code.

**Gelesene Tabellen.**

1. `Instrumente`: eine Zeile je Wertpapier, ob gehalten, verkauft oder beobachtet.

   | Spalte | Pflicht | Inhalt |
   | --- | --- | --- |
   | `isin` | ja | Primärschlüssel |
   | `name` | ja | nur Anzeige; bei Derivaten ohne Knock-out-Schwelle |
   | `typ` | ja | `Aktie`, `ETF` oder `Derivat` |
   | `aktienform` | bei Aktien | `share` oder `adr` |
   | `anteilsklasse` | bei ETFs | geprüfte Anteilsklasse, z. B. `Acc` |
   | `basiswert_isin` | bei Derivaten | ISIN des Basiswerts; Information, nie Kursersatz |
   | `kategorie` | nein | Anzeige und Gruppierung |
   | `google_symbol` | nein | nur für Formeln im Sheet; in der App ohne Wirkung |
   | `yahoo_symbol`, `yahoo_handelsplatz`, `waehrung` | nein | das eine Listing für Kurse und Historie |
   | `geprueft` | nein | Datum der Besitzerprüfung dieses Listings |

2. `Transaktionen`: Journal, ersetzt `Käufe` und `Verkäufe`. Zeilen werden angehängt, nicht überschrieben.

   | Spalte | Pflicht | Inhalt |
   | --- | --- | --- |
   | `txn_id` | ja | Primärschlüssel |
   | `datum` | ja | Ausführungstag (`TT.MM.JJJJ`, ISO oder Sheets-Datum) |
   | `isin` | ja | muss in `Instrumente` stehen |
   | `art` | ja | `Kauf`, `Sparplan`, `Verkauf`, `Einbuchung` oder `Ausbuchung` |
   | `stueck` | ja | positiv; Zu- oder Abgang folgt aus `art` |
   | `kurs_eur`, `gebuehr_eur` | nein | laut Abrechnung |
   | `betrag_eur` | bei Kauf, Sparplan, Verkauf | Zugang: Belastung inklusive Gebühren; Verkauf: Erlös nach Gebühren, vor Steuern |
   | `steuern_eur` | nein | bei Verkäufen laut Abrechnung |
   | `bezug_txn_id` | nein | bei einer Einbuchung aus einem Umtausch: die zugehörige Ausbuchung |
   | `broker_ref` | nein | Auftragsnummer des Brokers; wenn gesetzt, eindeutig |
   | `notiz` | nein | Freitext |

3. `Kurse_manuell`: `isin`, `datum`, `kurs_eur`. Gilt für Instrumente ohne Anbieterkurs, etwa Derivate. Der jüngste Eintrag je ISIN ist der gekennzeichnete Sheet-Wert, `datum` sein Kursstand. Ersetzt die getippten Kurse und die Kursdatum-Spalte des Depotblatts.

4. `Watchlist`: `isin` (Primär- und Fremdschlüssel) und `notiz`. Name und Listing kommen aus `Instrumente`. Weitere Spalten rechts davon, etwa GOOGLEFINANCE-Formeln, ignoriert die App.

**Ansichten**, nicht von der App gelesen: `Depot` (das bisherige Depotblatt) mit Stückzahl per Formel aus `Transaktionen` und Stammdaten per Nachschlagen aus `Instrumente`; `Auswertung` mit Kategorieanteilen, Summen und dem Abgleich gegen den Gesamtwert des Brokers.

**Geprüfte Kurszuordnung im Sheet (Variante A).** `Instrumente` ist die einzige Ablage der Kurszuordnung. `listings.toml` bzw. `[[listing]]`, die Depotspalten U–X und die Zuordnungsspalten des Watchlist-Blatts entfallen.

- Anbieterkurse gibt es nur, wenn `geprueft` ein gültiges Datum enthält und Symbol, Handelsplatz und Währung vollständig sind.
- `geprueft` setzt ausschließlich der Besitzer. Automationen (Skill, Agenten) dürfen Zeilen anlegen und Listing-Spalten vorschlagen, lassen `geprueft` aber leer. Wer Symbol, Handelsplatz oder Währung ändert, leert `geprueft`.
- Der strikte Laufzeitabgleich gegen die Anbieterantwort nach [0005](0005-instrumente-und-kursdaten.md) und [MARKET_DATA.md](../MARKET_DATA.md) bleibt unverändert: Symbol, Handelsplatz, Währung, Einheit und Zeitzone.
- Unterstützte Handelsplätze bleiben unverändert: im Depot die deutschen EUR-Plätze, in der Watchlist zusätzlich die US-Plätze aus [0011](0011-watchlist.md). Derivate erhalten weiterhin keine Anbieterkurse und nie Kurse des Basiswerts.
- Je Instrument gibt es genau ein Listing. Der Primär-/Ersatzmechanismus aus #101 entfällt; der Besitzer trägt das nutzbare Listing direkt ein.
- Eine geprüfte Zeile ist eine geprüfte Identität, auch ohne Depotbestand. Das beantwortet die offene Frage aus #95: Watchlist-Werte mit geprüfter `Instrumente`-Zeile erhalten einen Bewertungsschlüssel.
- `[[fundamental]]` bleibt mit seinen fachlichen Bestätigungen (Berichtswährung, Geschäftsjahresende, Belege) bestehen. Seine Angaben `isin`, `venue` und `symbol` müssen mit der geprüften `Instrumente`-Zeile übereinstimmen, sonst wird der Eintrag abgelehnt.

**Bestand und Einstand aus dem Journal.** Die App rechnet je ISIN in Datumsreihenfolge, bei gleichem Datum nach `txn_id`:

- Stückzahl = Zugänge (`Kauf`, `Sparplan`, `Einbuchung`) minus Abgänge (`Verkauf`, `Ausbuchung`).
- Einstand der verbleibenden Stücke nach FIFO; Anschaffungskosten eines Zugangs sind sein `betrag_eur`.
- Realisierter Gewinn/Verlust eines Verkaufs = `betrag_eur` minus FIFO-Einstand der abgegangenen Stücke.
- `Ausbuchung` ohne Bezug, etwa ein Knock-out, ist ein Abgang ohne Erlös; der FIFO-Einstand wird zum realisierten Verlust.
- `Einbuchung` mit `bezug_txn_id` übernimmt den FIFO-Einstand der referenzierten Ausbuchung; beide zusammen erzeugen keinen realisierten Gewinn oder Verlust. `Einbuchung` ohne Bezug hat `betrag_eur` als Anschaffungskosten, bei Gratiszuteilung 0.
- Ein fehlerfreies Journal ergibt `CostBasisStatus.VERIFIED_REMAINING`. Die pauschale Besitzerfeststellung `cost_basis_verified_remaining` entfällt.

**Prüfregeln** mit festen Warntexten ohne Zellwerte: doppelte `txn_id` oder `broker_ref`, ISIN fehlt in `Instrumente`, unbekannte `art`, nicht positive `stueck`, fehlender `betrag_eur` wo Pflicht, Bestand würde negativ, `bezug_txn_id` verweist nicht auf eine Ausbuchung. Ein fehlerhaftes Journal macht Stückzahl und Einstand **dieser ISIN** „n. v.“, nicht den gesamten Import.

**Unverändert** bleiben die Index-Blätter (eigene Universen mit Yahoo-Ticker als Schlüssel), die `agent_*`-Blätter (bereits ISIN), die Bewertungs- und Periodenregeln aus [0006](0006-kursentwicklung.md) und [0007](0007-bewertung-und-einstand.md) sowie die private SQLite-Ablage, deren Szenarien bereits an der ISIN als `instrument_id` hängen.

## Umsetzung in Stufen

1. **Sheet und Skill, ohne Code:** ISIN in allen Blättern ergänzen, auch bei Derivaten und Verkäufen; die nicht gelesene Spalte `Yahoo-Ticker` entfernen; fehlende Umtausch- und Einbuchungsereignisse nachtragen; der Skill findet Zeilen über die ISIN. Das Dashboard liest unverändert.
2. **Neue Blätter und Journal:** `Instrumente`, `Transaktionen` und `Kurse_manuell` anlegen und aus Käufen, Verkäufen und Depotblatt befüllen. Vor der Umstellung berechnete gegen gepflegte Stückzahlen abgleichen. Neue reine Module lesen die Blätter und rechnen das Journal; Lader und Konfiguration lesen die neuen Bereiche; das Depotblatt wird zur Ansicht. Der Skill meldet Käufe und Verkäufe als neue Journalzeile mit Dublettenprüfung.
3. **Aufräumen:** `listings.toml`/`[[listing]]`, die Sheet-Ersatzzuordnung aus #101 und die `yahoo-symbol:`-IDs entfernen; Zeilennummer-Schlüssel durch die ISIN ersetzen; die Watchlist auf das neue Schema umstellen.

## Alternativen

- **Getrennte Zuordnung beibehalten (Variante B):** Kurszuordnung nur in `listings.toml` bzw. den Secrets. Klare Schreibgrenze, aber zwei Pflegeorte je neuem Wertpapier und eine umständliche Secrets-Änderung samt Neustart. Vom Besitzer verworfen.
- **Eigene Wertpapier-ID neben der ISIN:** zusätzlicher Pflegeaufwand ohne Gewinn; die ISIN ist stabil und steht auf jeder Abrechnung.
- **Datenbank statt Sheet:** verliert die Pflege von Hand und den Zugriff des Skills. Ein tabellarisch geführtes Sheet lässt sich später ohne Umbau migrieren.
- **Stückzahl und Einstand weiter von Hand pflegen:** zwei Wahrheiten, die auseinanderlaufen.

## Konsequenzen

- Der Schutz gegen falsche Kurse beruht auf `geprueft` und dem Laufzeitabgleich, nicht mehr auf einem getrennten Ablageort. Ändert eine Automation ein Listing, ohne `geprueft` zu leeren, ist das technisch nicht erkennbar; die Skill-Regel und der Laufzeitabgleich begrenzen dieses Risiko.
- Ein neues Wertpapier braucht vor seiner ersten Transaktion eine Zeile in `Instrumente`.
- Das Journal muss vollständig sein; Umtausch, Kapitalmaßnahmen und Knock-outs sind eigene Ereignisse.
- Realisierte Gewinne werden verfügbar. Dividenden sind nicht Teil dieser Entscheidung.
- `sheet_parser.py` verliert Summenzeilen-Erkennung, Referenz- und Ersatzspalten; `local_source.py` verliert den doppelten Identitätsweg.
- Tests verwenden weiterhin ausschließlich synthetische Daten. Echte Blattinhalte gehören nicht ins Repository.

## Offen

- Splits und Zusammenlegungen, bei denen sich die Stückzahl ohne Zahlung ändert, brauchen eine Regel, bevor sie im Journal vorkommen.
- Ob `[[fundamental]]` ebenfalls in ein Blatt wandert, ist eine eigene Entscheidung.

## Nachtrag zur Umsetzung (08.11.2026)

Besitzerentscheidungen bei der Umsetzung:

- **EUR-Listing gewinnt.** Das Depot nutzt nur geprüfte EUR-Listings deutscher Handelsplätze (`GER`, `FRA`, `STU`, `MUN`, `DUS`). Ist für ein Wertpapier nur ein US-Listing eingetragen, nutzt es die Watchlist weiter; im Depot bleibt es beim Sheet-Kurs, bis das EUR-Listing eingetragen ist.
- **Zuordnungen aus dem Sheet abgeleitet.** `Instrumente` wurde aus Depotblatt, Watchlist, Käufen und Verkäufen befüllt; `geprueft` ist für alle übernommenen Zuordnungen auf den Migrationstag gesetzt. `[[listing]]`, `PORTFOLIO_LISTINGS_FILE` und `PORTFOLIO_SHEET_RANGE` werden nicht mehr gelesen.
- **Alte Blätter bleiben** mit Namen und Spalten erhalten, weil externe Agenten sie lesen könnten. Im Depotblatt sind Anzahl, Kurs und Kursdatum Formeln aus `Transaktionen` und `Kurse_manuell`; in der Watchlist sind Yahoo-Symbol, Handelsplatz und Währung Formeln aus `Instrumente` (nur bei gesetztem `geprueft`). Käufe und Verkäufe sind ein unverändertes Archiv. Die App liest weiterhin die Watchlist über ihren konfigurierten Bereich.

Technische Festlegungen:

- Die App liest die drei Tabellen mit festen, begrenzten Bereichen in einem `values:batchGet`; neue Secrets sind nicht nötig.
- `Kurse_manuell` darf Formeln enthalten, etwa GOOGLEFINANCE für Wertpapiere ohne EUR-Listing. Ohne `datum` gilt „Kursstand unbekannt“; ein datierter Eintrag hat Vorrang vor einem undatierten.
- `basiswert_isin` ist optional.
- Die Anzeigekürzel leiten sich aus `google_symbol` ab (`NASDAQ:MSFT` → `MSFT`).
- Der Primär-/Ersatzmechanismus aus #101 und die privaten Listing-Dateien sind entfernt.

Weiter offen:

- Watchlist-Zeilen ohne ISIN (Krypto, Notizen) bleiben ohne Zuordnung; eine Regel für Werte ohne ISIN fehlt.
- Der frühere A–M-Parser (`sheet_parser.parse_sheet_rows`) ist nicht mehr im Ladepfad und wird nur noch von Regressionstests genutzt; seine Entfernung ist offen.
- Die Unternehmensauswahl verknüpft Depotpositionen noch über die Zeilennummer in `Instrumente` statt über die ISIN.

## Referenzen

- [0004](0004-google-sheets.md), [0005](0005-instrumente-und-kursdaten.md), [0007](0007-bewertung-und-einstand.md), [0010](0010-innerer-wert-fcff.md), [0011](0011-watchlist.md)
- [Datenquelle](../DATA_SOURCE.md), [Kurs- und Zuordnungsvertrag](../MARKET_DATA.md), [Watchlist-Zuordnung](../PRIVATE_WATCHLIST.md)
