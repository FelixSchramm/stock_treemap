# Container und Betriebsparameter

Bereitet den einzelnen Python-Container aus [ADR 0009](adr/0009-betrieb-und-qualitaet.md) vor. Dieses Dokument bestellt, veröffentlicht und konfiguriert keinen Hosting-Dienst; Anbieter, Konto, URL und Budget sind Besitzerentscheidungen (#34).

## Bauen und lokal starten (Demo)

```bash
docker build -t portfolio-dashboard:local .
docker run --rm -p 127.0.0.1:8501:8501 portfolio-dashboard:local
```

Das Image installiert mit `uv sync --locked --no-dev --no-editable` exakt die Versionen aus `uv.lock` (uv 0.12.23 wie in der CI, Python 3.12). Es enthält nur `app.py`, `.streamlit/config.toml` und das installierte Paket `portfolio_dashboard`, läuft als Nicht-Root-Benutzer und startet standardmäßig mit `PORTFOLIO_MODE=demo` ohne Secrets und ohne Volumes.

## Parameter

| Parameter | Wert / Bedeutung |
| --- | --- |
| Port | `8501` (`STREAMLIT_SERVER_PORT`) |
| Host | `0.0.0.0` im Container (`STREAMLIT_SERVER_ADDRESS`); Veröffentlichung nur über den Reverse-Proxy des Hosts |
| Startbefehl | `streamlit run app.py` |
| Healthcheck | `GET /_stcore/health` auf `127.0.0.1:8501`; Streamlits eigener Liveness-Endpunkt, antwortet `ok` ohne Depotdaten und ohne Login |
| Modus | `PORTFOLIO_MODE=demo` (Standard) oder `production` |
| Startmeldung | `STREAMLIT_LOGGER_HIDE_WELCOME_MESSAGE=true`: Streamlit ermittelt sonst im Headless-Modus beim Start die externe IP über `checkip.amazonaws.com` und schreibt sie als "External URL" ins Log |
| Weitere Einstellungen | Variablen aus [`.env.example`](../.env.example); der Container liest keine `.env`-Datei |

## Reverse-Proxy und WebSockets

- HTTPS-Terminierung durch den Host; der OIDC-Callback `/oauth2callback` muss über dieselbe HTTPS-URL erreichbar sein.
- Streamlit benötigt WebSocket-Weiterleitung (`Upgrade`/`Connection`-Header) für `/_stcore/stream` und lange Leerlauf-Timeouts.
- Der Proxy muss den originalen `Host`-Header durchreichen (nicht auf die Upstream-Adresse umschreiben, nginx: `proxy_set_header Host $host;`); sonst lehnt Streamlit die WebSocket-Verbindung ab. Alternativ `STREAMLIT_BROWSER_SERVER_ADDRESS` auf den öffentlichen Hostnamen setzen.
- XSRF-Schutz bleibt aktiv; ihn zur Fehlerbehebung abzuschalten oder den Login zu umgehen ist nicht zulässig (ADR 0009).
- Ausgehend sind HTTPS-Aufrufe zu Google (Sheets, OIDC) und zum Kursanbieter erforderlich.

## Secret-Injektion (Produktion)

Es gibt keine Secrets im Image und keine im Build-Kontext. Der Host injiziert zur Laufzeit:

- `PORTFOLIO_*`-Variablen als Umgebungsvariablen über seine Secret-Verwaltung (nicht in Kommandozeilen oder Logs).
- Den Streamlit-Abschnitt `[auth]` als schreibgeschützte Datei `/app/.streamlit/secrets.toml` (Vorlage: [`secrets.example.toml`](../.streamlit/secrets.example.toml)).
- Die Service-Account-Schlüsseldatei als schreibgeschützte Datei außerhalb von `/app`; `PORTFOLIO_SHEETS_CREDENTIALS_FILE` zeigt auf deren Pfad.

Der Container hat keine Datenbank und kein persistentes Volume; der Cache ist rekonstruierbar. Rollback: vorheriges funktionierendes Image-Tag behalten.

## Build-Kontext

`.dockerignore` schließt Git-Historie, `.env*`, Schlüssel-/Credential-Dateien, `secrets`, `data`, `exports`, Caches, Logs, `docs/private`, `tickers.txt`, `heatmap.html` und `secrets.toml` (jeweils in jedem Verzeichnis, auch unter `src/`) sowie Tests aus. Zusätzlich kopiert das Dockerfile nur eine ausdrückliche Dateiliste, nie `COPY . .`.

## Prüfung des Images

```bash
docker build -t portfolio-dashboard:check .
docker run -d --name pd-check -p 127.0.0.1:8501:8501 portfolio-dashboard:check
docker inspect --format '{{.State.Health.Status}}' pd-check   # healthy
docker run --rm --entrypoint sh portfolio-dashboard:check -c 'find /app -not -path "/app/.venv/*"'
docker rm -f pd-check && docker rmi portfolio-dashboard:check
```

Nur Basis-Image und Paketinstallation benötigen Netzwerk. Der Demo-Lauf ruft weder Sheets noch Kursanbieter auf. Healthcheck und Image-Inhalt am Zielhost bleiben Teil von #34.
