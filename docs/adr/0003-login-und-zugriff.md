# ADR 0003: OIDC-Login mit expliziter Besitzerfreigabe

- **Datum:** 2026-10-23
- **Status:** Angenommen; Konto und Credentials noch offen
- **Herkunft:** Zugriffsschutz vom Nutzer; OIDC/Google technisch.

## Kontext

Der Nutzer erlaubt Passwort oder Login. Eine erfolgreiche Anmeldung allein bestimmt nicht, wer das Depot sehen darf. Sheet-Zugriff und Dashboard-Anmeldung sind getrennte Berechtigungen.

## Entscheidung

Die Anwendung nutzt OIDC über die unterstützte Streamlit-Anmeldung, zunächst mit Google. Zusätzlich prüft der Server die explizite Freigabe der Besitzeridentität anhand validierter Issuer-/Subject-Claims (`iss`, `sub`). Eine E-Mail dient höchstens einem kontrollierten, verifizierten Erstabgleich. Das erste beliebige Konto wird nicht automatisch freigegeben.

Anmeldung und Besitzerprüfung erfolgen vor dem Abruf oder der Ausgabe von Depotdaten. Ohne vollständig konfigurierte Freigabe bleibt der Zugang geschlossen. Das gilt auch für Exporte und künftige Datenendpunkte. Andere angemeldete Google-Konten erhalten keinen Zugriff.

Produktivbetrieb verwendet HTTPS. Client-Secret, Cookie-Secret und Freigabekonfiguration liegen serverseitig in der Secret-Verwaltung. Abmelden ist möglich. Mehrere Tabs und erneutes Öffnen werden geprüft; App-Abmeldung wird nicht als globale Abmeldung beim Google-Konto dargestellt. Die Besitzerfreigabe wird bei jedem App-Lauf erneut geprüft.

## Alternativen

- Eigenes Passwort: zusätzlicher Aufwand für Hashing, Missbrauchsschutz, Reset und Sessions.
- Alle Google-Konten zulassen: Anmeldung ohne passende Berechtigungsprüfung.
- Browser-Login vor öffentlich erreichbaren Daten: schützt den Datenpfad nicht.

## Konsequenzen

Konkrete Besitzeridentität, OIDC-Client und Callback-URL werden noch eingerichtet; kein Konto wird aus dem Sheet-Namen abgeleitet. Login-Credentials bleiben vom Sheet-Service-Account getrennt, siehe [0004](0004-google-sheets.md).

## Nachtrag 2026-10-25: Lokaler Modus ohne Login

Auf Wunsch des Besitzers zeigt `PORTFOLIO_MODE=local` die eigenen Sheet-Daten auf dem eigenen Rechner ohne OIDC-Login. Der Netzwerkzugang ersetzt hier die Anmeldung: Die App lädt nur, wenn Streamlit ausschließlich auf `127.0.0.1`, `localhost` oder `::1` lauscht, und sperrt sonst vor dem ersten Sheet-Abruf. Der Modus liest keine Auth-Secrets und ist nicht für Container oder Hosting vorgesehen; jeder erreichbare Betrieb bleibt beim Produktivmodus mit Besitzerprüfung.

## Nachtrag 2026-11-09: Öffentliche Startseite vor dem Login

Auf Wunsch des Besitzers sehen nicht angemeldete Besucher statt einer Fehlermeldung die Startseite **Märkte im Überblick** mit **Login** rechts oben (`start_view.py`). Sie zeigt ausschließlich öffentliche Marktdaten zu einer fest im Code hinterlegten Symbolliste (`market_overview.py`: Weltindizes, Branchen-ETFs, Devisen und Rohstoffe). Die Seite liest keine Secrets außer der bestehenden Login-Konfiguration, greift nicht auf das Sheet zu und startet keine Depot-, Watchlist-, Index- oder Fundamentaldaten-Lader; die Besitzerprüfung für alles Weitere bleibt unverändert. Ein angemeldetes, aber nicht freigegebenes Konto erhält weiterhin die Fehlermeldung. Die Kurse werden für alle Besucher gemeinsam bis zu 15 Minuten zwischengespeichert, damit die Zahl der Yahoo-Abrufe nicht mit der Besucherzahl wächst. Die rechtliche Frage zur Anzeige von Yahoo-Daten in einer öffentlich erreichbaren App (Epic #115) bleibt offen; solange die Community-Cloud-App nur für eingeladene Konten sichtbar ist, sehen die Startseite nur diese.

## Referenzen

- [Streamlit: OIDC, Login und Session-Verhalten](https://docs.streamlit.io/develop/concepts/connections/authentication)
- [Google OpenID Connect](https://developers.google.com/identity/openid-connect/openid-connect)
