# Optional: review-phase prompt (generates the work-plan issues)

*Optional — use this only if you want an agent to produce the work plan
(activation checklist step 1) instead of writing the issues yourself. Paste
the block below into a fresh Claude Code cloud session on the target
repository. The prompt is written in German; the agent's deliverables (review
document, issues) come out in English.*

---

# Auftrag: Portfolio-Review mit Umsetzungs-Briefings und GitHub-Issues

Du bist ein Agent mit Zugriff auf das Repository in dieser Session. Das Repo ist ein
Machine-Learning-Portfolio-Projekt, das in Bewerbungen gezeigt werden soll (Data
Science, insbesondere Banken-/Finanzsektor). Deine Aufgabe besteht aus drei Phasen:
ein gründliches Review, daraus abgeleitete Umsetzungs-Briefings als Markdown-Dateien,
und deren Überführung in echte GitHub-Issues. Arbeite die Phasen strikt in dieser
Reihenfolge ab.

**Sprache:** Alle Deliverables (Review-Dokument, Briefings, Issues, Commit-Messages,
PR-Beschreibung) auf **Englisch** — das Repo ist öffentlich und richtet sich an
Recruiter/Interviewer. Antworten an mich im Chat gerne auf Deutsch.

---

## Phase 1 — Gründliches Review

Leitfrage: „Ist dieses Repo so, wie es ist, in einer Bewerbung vorzeigbar — und was
würde ein erfahrener Reviewer (z. B. ein Interviewer bei einer Bank) daran kritisieren?"

**Wichtigste Regel: Verifiziere jede Behauptung selbst am Repo, bevor du sie
aufschreibst.** Öffne die Notebooks (auch die Roh-JSON-Struktur: `execution_count`,
Zellreihenfolge), prüfe `git ls-files`, `.gitignore`, `requirements.txt`, die
Git-Historie (`git log`), und gleiche die README Satz für Satz mit dem tatsächlichen
Repo-Inhalt ab. Kein Befund ohne konkreten Beleg (Datei, Zelle, Zeile).

Prüfkatalog (mindestens diese Punkte, ergänze eigene):

1. **Data Leakage / unglaubwürdige Metriken:** Sind die berichteten Metriken
   verdächtig gut (z. B. AUC nahe 1.0)? Gibt es Features, die erst *nach* dem
   vorhergesagten Ereignis bekannt sind (Post-Outcome-Spalten) und nicht gedroppt
   werden? Prüfe die Drop-Listen im Code gegen die Datendokumentation. Achte auch auf
   voll befüllte Leakage-Spalten, die Missing-Value-Filter überleben.
2. **README vs. Realität:** Existiert jede in der README genannte Datei/Struktur
   wirklich? Stimmen die berichteten Metriken mit dem Notebook-Output überein? Gibt es
   eine „How to run"-Anleitung (Datenquelle, Setup, Ausführung)?
3. **Reproduzierbarkeit:** Sind die `execution_count`-Werte der Notebooks monoton
   (= wurde „Restart & Run All" vor dem Commit ausgeführt)? Läuft das Projekt in einer
   frischen Umgebung?
4. **Code-Qualität:** Modularisierung (importierbare Module statt nur Notebook-Zellen),
   Kurs-/Übungsartefakte (kryptische Checklisten-Kommentare, Debug-Reste wie `df.info`
   ohne Klammern), globales `warnings.filterwarnings('ignore')`.
5. **Projekt-Hygiene:** Tests, CI, gepinnte Dependencies, LICENSE, versehentlich
   eingecheckte Dateien (`.DS_Store` etc.), aussagekräftige Git-Historie.
6. **Fachliche Tiefe für den Zielsektor:** Fehlen branchenübliche Metriken und
   Einordnungen (bei Credit Risk z. B. KS-Statistik, Gini, Kalibrierung, SHAP/Feature
   Importance, Scorecard-Vergleich mit Logistic Regression + WOE/IV, Basel/IFRS9-Kontext
   — bei anderem Fachgebiet die dort etablierten Pendants)?

Benenne auch explizit die **Stärken** des Repos. Priorisiere alle Befunde:
**P0** (Glaubwürdigkeit, vor jeder Bewerbung zwingend), **P1**
(Struktur/Professionalität), **P2** (fachliche Tiefe/Differenzierung), **P3** (Politur).

---

## Phase 2 — Umsetzungs-Briefings als Markdown

Lege auf einem neuen Feature-Branch an:

- `docs/REPO_REVIEW.md`: das Review-Dokument — Ziel, geprüfter Stand (Commit-Hash),
  Stärken, Schwächen nach Schweregrad, priorisierte Liste mit Links auf die Briefings.
- `docs/issues/NN-kurzer-slug.md`: ein Briefing pro Verbesserungspunkt
  (zweistellig nummeriert), jeweils mit exakt dieser Struktur:

  ```
  # Issue NN: <Titel>

  **Priority:** P0–P3 (+ kurze Begründung der Stufe)
  **Affects:** <betroffene Dateien/Bereiche>

  ## Context        (Warum ist das ein Problem? Mit Belegen aus Phase 1.)
  ## Goal           (Zielzustand in 2–4 Sätzen.)
  ## Implementation steps  (Nummerierte, konkrete Schritte.)
  ## Affected files
  ## Acceptance criteria   (Checkboxen, objektiv prüfbar.)
  ```

Qualitätsregeln für die Briefings:

- **Selbstständig umsetzbar:** Jede Datei muss von einem Agenten oder Menschen ohne
  jeden Vorkontext umsetzbar sein. Abhängigkeiten zwischen Briefings explizit nennen
  („requires Issue 03").
- **Führende Pakete statt Eigenbau:** Nenne für jede Aufgabe das etablierte Paket bzw.
  die Funktion, die sie mit minimalem Code löst (z. B. `scipy.stats.ks_2samp` statt
  manueller KS-Berechnung, `sklearn.calibration.CalibrationDisplay.from_predictions`
  statt eigenem Plot, `jupyter nbconvert --execute --inplace` für skriptbares
  „Restart & Run All", `pandas.testing.assert_frame_equal` in Tests, `ruff` als
  einziges Lint-Tool, `optbinning` für WOE/IV). Empfiehl niemals eine
  Eigenimplementierung, wenn ein Standardpaket eine Ein-Zeilen-Lösung hat.
- **Technisch valide Vorgaben:** Prüfe deine eigenen Vorschläge auf Machbarkeit
  (z. B. Python-Package-Namen dürfen nicht mit Ziffern beginnen — Ordner wie `03_src/`
  müssen für Importe zu `src/` umbenannt werden; Tests sollen auf kleinen synthetischen
  Daten laufen, damit CI ohne den großen Rohdatensatz funktioniert).
- **Performance-Fallstricke benennen:** z. B. SHAP nicht auf dem vollen Test-Set,
  sondern auf einem Sample von wenigen Tausend Zeilen rechnen.

---

## Phase 3 — GitHub-Issues anlegen und aufräumen

1. Erstelle pro Briefing ein GitHub-Issue: H1-Überschrift (ohne „Issue NN:"-Präfix)
   wird der Titel, der Rest der Datei der Body, Priorität als Label (`P0`–`P3`).
   Lege die Issues in Dateireihenfolge an und notiere die vergebenen Issue-Nummern.
2. **Zweiter Durchgang:** Ersetze in allen Issue-Bodies die textuellen Querverweise
   („see Issue 03") durch die echten GitHub-Nummern („see Issue 03 (#4)"), damit sie
   im Tracker klickbar sind.
3. Aktualisiere `docs/REPO_REVIEW.md`: Die priorisierte Liste verlinkt jetzt auf die
   GitHub-Issues (volle URLs — in Markdown-Dateien werden `#N`-Kurzreferenzen nicht
   automatisch verlinkt).
4. **Entferne `docs/issues/` wieder** (`git rm -r`): Die Briefings leben ab jetzt im
   Issue-Tracker; Arbeitsmaterial soll nicht dauerhaft im öffentlichen Portfolio-Repo
   liegen. `docs/REPO_REVIEW.md` bleibt als Übersicht, mit einem kurzen
   Lifecycle-Hinweis, dass auch dieses Dokument nach Abarbeitung der P0-/P1-Punkte
   entfernt werden sollte.
5. Committe und pushe den Branch und eröffne einen Pull Request (nur Doku: Review-
   Dokument, ggf. PR-Template — keine Code-Änderungen in diesem PR).

---

## Arbeitsregeln

- Feature-Branch, keine Direct-Pushes auf den Default-Branch.
- Aussagekräftige Commit-Messages im Imperativ; die PR-Beschreibung muss exakt dem
  tatsächlichen Diff entsprechen (jede enthaltene Datei erwähnen).
- Ändere in diesem Auftrag **keinen** Produktiv-Code (Notebook, Modelle, README,
  requirements) — die Umsetzung passiert später über die Issues, je Issue ein
  eigener PR mit `Closes #<Nummer>`.
- Am Ende: Zusammenfassung im Chat mit Link zum PR, Liste aller erstellten Issues
  (Nummer + Titel + Priorität) und den 3–5 wichtigsten Befunden.
