# ADR 0001: direkt online als persönliches Depot-Dashboard

- **Datum:** 2026-10-23
- **Status:** Angenommen
- **Herkunft:** Nutzerwünsche; Abgrenzung des ersten Umfangs als technische Planung.

## Kontext

Der Prototyp erzeugt eine lokale HTML-Aktienkarte. Der Nutzer möchte sein eigenes Depot sehen und hat die zunächst vorgeschlagene lokale Auslieferung durch „direkt online, aber mit Passwort oder Login“ konkretisiert. iPhone-Nutzung wurde als Idee genannt.

## Entscheidung

Die erste ausgelieferte Version ist eine private, online erreichbare Webanwendung mit Login für das Depot eines Besitzers. Sie funktioniert unabhängig von dessen Mac. Lokale Ausführung dient Entwicklung und Prüfung.

Standard ist reine Kursentwicklung heute, zusätzlich 1 Woche, 1 Monat und 1 Jahr. „Meine Entwicklung“ zeigt Gewinn/Verlust der noch gehaltenen Positionen seit Kauf in Prozent. Die Kachelfläche entspricht dem eigenen Positionswert.

Mac- und iPhone-Browser werden von Beginn an berücksichtigt. Eine Home-Bildschirm-Web-App bleibt Ausbauoption. Native iOS-App, Offline-Depotkopien, Push-Nachrichten, mehrere Benutzerdepots, Handel und historische Transaktionsrenditen gehören nicht zur ersten Version.

## Alternativen

- Nur lokale HTML-Datei: erfüllt den bestätigten Online-Wunsch nicht.
- Native iOS-App zuerst: zusätzlicher Auslieferungsweg vor geklärtem Datenfluss.
- Öffentliches Depot: widerspricht dem Zugriffsschutz.

## Konsequenzen

Hosting und Login gehören zur Abnahme. Eine lokale Vorschau allein schließt das Projekt nicht ab. Persönliche Entwicklung heißt „Seit Kauf“ und folgt nicht den Kursperioden. Siehe [0003](0003-login-und-zugriff.md), [0007](0007-bewertung-und-einstand.md) und [0009](0009-betrieb-und-qualitaet.md).

## Referenz

[Apple: Website auf dem iPhone als Web-App öffnen](https://support.apple.com/de-de/guide/iphone/iphea86e5236/ios). Das beschreibt eine Ausbauoption, keine bereits umgesetzte PWA.
