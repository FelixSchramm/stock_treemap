# Betrieb auf Streamlit Community Cloud

Einrichtung des Produktivmodus auf der in #30 gewählten Streamlit Community Cloud (0 €, Region USA, Ruhezustand nach zwölf Stunden ohne Zugriff). Dieses Dokument legt keine Konten an und enthält keine echten Werte; alle Kennungen gehören ausschließlich in die Secret-Einstellungen der App, nie in Repository, Issues, PRs oder Logs.

## 1. App anlegen (#34)

1. Auf share.streamlit.io mit GitHub anmelden, **Create app**: Repository `FelixSchramm/stock_treemap`, Branch `main`, Datei `app.py`.
2. Unter **Advanced settings** Python **3.12** wählen und eine neutrale Subdomain festlegen. Community Cloud installiert nach `uv.lock`.
3. Ohne Secrets startet die App im Demo-Modus; damit ist der Build geprüft.
4. Sofort **Settings → Sharing → „Only specific people can view this app“** und nur das eigene Konto einladen. Das Repository ist öffentlich, die App wäre sonst für alle erreichbar (Auflage aus #30). Die Besitzerprüfung der Anwendung bleibt zusätzlich Pflicht.

## 2. Google-Login (#31)

In der Google Cloud Console:

1. **Google Auth Platform** (früher OAuth-Zustimmungsbildschirm): Zielgruppe **External**, Status **Testing**, als Testnutzer nur das eigene Konto.
2. **Clients → Create client → Web application**, autorisierte Redirect-URI `https://<subdomain>.streamlit.app/oauth2callback`. Client-ID und Client-Secret gehören nur in den Abschnitt `[auth]`.
3. Besitzer-Subject (`sub`) ermitteln, ohne dass die App es anzeigt: im Google OAuth Playground Scope `openid` autorisieren, Code eintauschen und `https://openidconnect.googleapis.com/v1/userinfo` abrufen. Google vergibt `sub` pro Konto, nicht pro Client. Danach den Playground-Zugriff unter myaccount.google.com/permissions entfernen.
4. Cookie-Secret erzeugen: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.

## 3. Secrets eintragen

App → **Settings → Secrets**, Aufbau wie [`secrets.example.toml`](../.streamlit/secrets.example.toml): oben die `PORTFOLIO_*`-Werte, darunter `[auth]`, `[sheets_service_account]` und optional `[[listing]]`.

Den Service-Account-Schlüssel als Abschnitt in die Zwischenablage übernehmen, ohne ihn im Terminal auszugeben:

```bash
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print("[sheets_service_account]"); [print(k, "=", json.dumps(v)) for k, v in d.items()]' "$HOME/<Pfad außerhalb des Repos>/service-account.json" | pbcopy
```

Für Live-Kurse anschließend die eigene Zuordnungsdatei anhängen (`pbcopy < "$HOME/<Pfad>/listings.toml"`); Format siehe README. Ohne `[[listing]]` zeigt die App nur Sheet-Werte und für Kursperioden „n. v.“.

## 4. Prüfen

- Anonymes Fenster: Streamlit verlangt die eingeladene Anmeldung; ohne sie kein Zugang.
- Eigenes Konto, **Mit Google anmelden**: eigenes Depot mit Datenstand, Live-Kurse für zugeordnete Positionen.
- **Von dieser App abmelden**, neu laden: wieder gesperrt.
- Fehlender oder ungültiger Schlüssel: nur „Service-Account-Schlüssel fehlt oder ist ungültig.“, keine Daten.

Der Neustart nach dem Ruhezustand beginnt ohne Cache; die App lädt dann neu oder zeigt „Daten nicht verfügbar“ (ADR 0009). Die weitere Live-Abnahme folgt in #35–#38.
