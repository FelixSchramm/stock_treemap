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

## Referenzen

- [Streamlit: OIDC, Login und Session-Verhalten](https://docs.streamlit.io/develop/concepts/connections/authentication)
- [Google OpenID Connect](https://developers.google.com/identity/openid-connect/openid-connect)
