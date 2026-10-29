# Architecture Decision Records

Stand: 23. Oktober 2026. **Angenommen** bedeutet für die Planung festgelegt, nicht bereits implementiert. Die Herkunft unterscheidet bestätigte Nutzerwünsche von technischen Entscheidungen. Offene Konfiguration ist ausdrücklich benannt.

| ADR | Thema | Status | Herkunft |
| --- | --- | --- | --- |
| [0001](0001-produktumfang.md) | Produktumfang, Online-Nutzung und iPhone | Angenommen | Nutzervorgaben; Ausbauabgrenzung technisch |
| [0002](0002-python-uv-streamlit.md) | Python, uv, Streamlit und Plotly | Angenommen | uv/Python vom Nutzer; Bibliotheken technisch |
| [0003](0003-login-und-zugriff.md) | Login und Besitzerfreigabe | Angenommen; Konto offen | Login vom Nutzer; Verfahren technisch |
| [0004](0004-google-sheets.md) | Google Sheets als lesende Bestandsquelle | Angenommen; Laufzeitzugang offen | Quelle vom Nutzer; Integration technisch |
| [0005](0005-instrumente-und-kursdaten.md) | Instrumentidentität, Kursanbieter und Cache | Angenommen; Abdeckung zu prüfen | Technisch, aus Datenprüfung abgeleitet |
| [0006](0006-kursentwicklung.md) | Reine Kursentwicklung und Perioden | Angenommen | Zeiträume vom Nutzer; Rechenregeln technisch |
| [0007](0007-bewertung-und-einstand.md) | EUR-Bewertung und Gewinn/Verlust seit Kauf | Angenommen | Kennzahl vom Nutzer; EUR aus dem Sheet |
| [0008](0008-treemap-und-bedienung.md) | Treemap, Ansichtswechsel und Gestaltung | Angenommen | Gewichtung/Umschalter vom Nutzer; UI technisch |
| [0009](0009-betrieb-und-qualitaet.md) | Hosting-Anforderungen, Versionierung und Prüfung | Angenommen; Anbieter offen | Online vom Nutzer; Betriebsregeln technisch |
| [0010](0010-innerer-wert-fcff.md) | FCFF-DCF, Datenanforderungen und Modellgrenzen (M8) | Fachvertrag; Implementierung/Live-Abdeckung offen | Epic #69 / Issue #70; technische Modellregeln |
| [0011](0011-watchlist.md) | Watchlist aus eigenem Sheet-Blatt, reine Kursentwicklung (M9) | Angenommen; Kursabruf/Ansicht offen | Epic #82 / Issue #83: Nutzerentscheidungen 25.10.2026; Parser- und Prüfregeln technisch |

## Verbindliche Nutzerentscheidungen

- Kachelgröße nach eigener Positionsgröße in Geld.
- Standardansicht Kursentwicklung heute; weitere Auswahl für Woche, Monat und Jahr.
- Umschalter für persönliche Entwicklung: Gewinn/Verlust seit Kauf in Prozent.
- Direkt online mit Passwort oder Login; keine rein lokale erste Auslieferung.
- Python-Projektverwaltung mit uv und Bestände aus dem vorhandenen Google Sheet.
- iPhone-Nutzung als Idee berücksichtigen; Git-Versionierung wurde optional zugelassen.

## Technische Festlegungen

Streamlit/Plotly, Google-OIDC-Login mit Besitzerprüfung, serverseitiger lesender Sheet-Zugriff, austauschbare Kursanbieter, EUR-Bewertung, gekennzeichnete Datenlücken, synthetische Tests und ein Python-Container bilden die geplante Umsetzung. Diese Festlegungen sind keine wörtlichen Nutzerentscheidungen.

## Offen

Hosting-Anbieter/Konto/Budget/URL, Login-Identität und Laufzeit-Credentials sowie einzelne Produktkennungen, Einstandswerte und Kurszeitpunkte. Details: [Umsetzungsplan](../../PROJECT_PLAN.md) und [Datenquelle](../DATA_SOURCE.md).

## Pflege

Nummern bleiben stabil. Eine geänderte Entscheidung erhält ein neues ADR mit Kontext, Entscheidung, Alternativen, Konsequenzen und Verweis auf das ersetzte ADR. Das alte Dokument wird als „Ersetzt durch ADR …“ markiert. Ein angenommenes ADR belegt keine erfolgreiche Umsetzung oder Prüfung.
