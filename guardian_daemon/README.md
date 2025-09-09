
## Design-Entscheidungen

- **Explizite Nutzerüberwachung:** Nur Nutzer, die unter `users:` in der Konfiguration eingetragen sind, werden überwacht. Eltern/Admins/Systemkonten sind ausgenommen.
- **Config-Reload:** Die Konfiguration wird alle 5 Minuten neu eingelesen. Änderungen werden automatisch erkannt und führen zu einer Aktualisierung der systemd-Timer und PAM-Regeln.
- **Dynamische Anpassung:** Timer und Login-Regeln werden bei Policy-Änderungen sofort angepasst, ohne Neustart des Daemons.
- **Timer-Nachholen:** Falls der Rechner zum Reset-Zeitpunkt nicht läuft, wird der Tagesreset beim nächsten Start nachgeholt.
- **Quota-Berechnung:** Tageskontingent wird ab einem konfigurierbaren Reset-Zeitpunkt berechnet, nicht ab Mitternacht. Laufende Sessions werden mitgerechnet.
  - Grundlegende globale Parameter:
    - hub_address: Adresse des Guardian-Hub (leer = deaktiviert)
  - db_path: Pfad zur SQLite-Datenbank (default: /var/lib/guardian/guardian.sqlite)
  - ipc_socket: Pfad zum IPC-Socket (default: /run/guardian-daemon.sock)
  - Globale Konfiguration für Quota-Vorwarnungen und Grace-Periode:
    - notifications: Wird auf oberster Ebene konfiguriert und gilt systemweit
      - pre_quota_minutes: Liste der Minuten vor Quota-Ende für Vorwarnungen (z.B. [15, 10, 5])
      - grace_period.enabled: Aktiviert die Grace-Periode
      - grace_period.duration: Dauer der Grace-Periode in Minuten
      - grace_period.interval: Benachrichtigungsintervall in Minuten während der Grace-Periode
    - defaults: Reserviert für nutzerspezifische Standardwerte (z.B. daily_quota_minutes, curfew, grace_minutes)

## Überblick

`guardian-daemon` ist der systemweite Hintergrunddienst des Guardian-Systems zur Durchsetzung von Zeitkontingenten und Curfews für Kinder auf Linux-Geräten. Er läuft als systemd-Service mit Root-Rechten und ist modular aufgebaut.


### Bisherige Komponenten

- **Quota-Berechnung**
  - Summiert alle Sessions eines Tages seit dem letzten Reset-Zeitpunkt und prüft gegen das Tageskontingent.
  - Berücksichtigt Grace-Zeit und laufende Sessions.

- **Dynamische PAM-Regeln**
  - Login-Zeitregeln werden bei jeder Policy-Änderung automatisch angepasst.
  - Regeln gelten explizit nur für die in der Konfiguration aufgeführten Nutzer (Kinder).

- **Systemd-Timer-Management**
  - Tagesreset und Curfew werden über systemd-Timer/Units automatisiert.
  - Timer werden bei Policy-Änderung aktualisiert und beim Start nachgeholt, falls verpasst.

- **Fehlerlogging**
  - Alle Schlüsselaktionen und Fehler werden ins systemd-Journal geloggt.

- **Policy-Loader (`policy.py`)**
  - Lädt die Konfiguration aus einer YAML-Datei (Pfad über ENV `GUARDIAN_DAEMON_CONFIG` oder Fallback auf `config.yaml`).
  - Stellt Methoden zum Zugriff auf Nutzer- und Default-Policies bereit.

- **Storage (`storage.py`)**
  - Zentrale SQLite-Schnittstelle für Sessions und spätere Erweiterungen.
  - Ermöglicht das Speichern und Abfragen von Sitzungsdaten.

- **SessionTracker (`sessions.py`)**
  - Überwacht Logins/Logouts via systemd-logind (DBus, mit `dbus-next`).
  - Misst Nutzungszeit pro Kind und speichert sie in der Datenbank.
  - Prüft Quota/Curfew anhand der Policy.

- **PamManager (`pam_manager.py`)**
  - Schreibt und entfernt Login-Zeitregeln in `/etc/security/time.conf` gemäß Policy.
  - Backup der Originaldatei wird automatisch angelegt.

- **Integration (`main.py`)**
  - Initialisiert alle Komponenten und startet den Daemon.
  - Policy und Storage werden zentral übergeben.
  - PAM-Regeln werden beim Start gesetzt.
  - Session-Tracking läuft asynchron.



## Noch offene Schritte & TODOs

- **Enforcement-Logik**
  - Design für Notification-Frequenz und Schwellenwerte festlegen (guardian_agent).
  - Session-Beendigung ggf. gezielter umsetzen (z.B. nur grafische Sessions, Game Sessions).
  - Konzept für Game Sessions und deren Enforcement/Notification ausarbeiten.

- **Netzwerk-Client**
  - Kommunikation mit zentralem Guardian-Hub (API/WebSocket).
  - Synchronisation von Policies und Usage-Daten.
  - Datei: `net_client.py`

- **Admin-IPC**
  - Implementiere einen lokalen Socket für Admin-Kommandos (Bonuszeit, Policy-Reload, etc.).
  - Datei: `ipc.py`

- **Fehler- und Ausnahmebehandlung**
  - Logging mit möglichst vielen Details, ggf. Nachricht an den Hub.

- **Tests und Mocking**
  - Schreibe Unit- und Integrationstests für alle Kernmodule.
  - Mock DBus und systemd für lokale Tests.




## Roadmap / Phasen

**Phase 0 — Lokal (pro Gerät)**
- Daemon (systemd), Policy-Loader, PAM-Zeitfenster, logind-Watcher, Timer für Curfew/Reset.
- guardianctl (CLI).

**Phase 1 — Hub (MVP)**
- Server mit Policies, Usage, Sessions, API.
- Geräte-Enrollment, Pull von Policies, Heartbeats.
- Tagesreset serverseitig.

**Phase 2 — Multi-Device & Push**
- WebSocket Push: sofortige Terminierung auf allen Geräten.
- Konfliktlösung + Offline-Deltas.
- Eltern-Dashboard mit Live-Status.

**Phase 3 — Komfort & Härtung**
- Rollen/Mehrere Eltern, 2FA, Benachrichtigungen (Mail/Signal/Matrix).
- Allowlist/Blocklist für Apps.
- Kiosk-Mode-Units pro Kind.

## Systemd-Integration (vom Daemon generiert)

- **guardian.service** (root-Daemon)
- **guardian.socket** (Admin-IPC, Gruppe `guardian-admin`)
- **curfew@.service / timer** (Logout pro Kind zu festen Zeiten)
- **daily-reset.service / timer** (Reset Quotas zum konfigurierbaren Zeitpunkt, z.B. 03:00)
- **gamesession@.service** (optional: Kiosk-Modus für Steam/Gamescope)
- **PAM-Managed Block** in `/etc/security/time.conf`

- **Explizite Nutzerüberwachung:**
  - Nur Nutzer, die unter `users:` in der Konfiguration eingetragen sind, werden vom Daemon überwacht und erhalten Quota-/Curfew-Regeln.
  - Ein leeres Objekt (z.B. `kid2: {}`) bedeutet, dass die Defaults für diesen Nutzer gelten.
  - Alle anderen Nutzer (z.B. Eltern, Admins, Systemkonten) werden ignoriert und sind von den Regeln ausgenommen.
  - Diese Logik muss in allen zukünftigen Komponenten (Enforcement, PAM, systemd, Netzwerk) berücksichtigt werden.

- **Modularität:** Halte die Schnittstellen zwischen Komponenten klar und einfach. Policy und Storage sollten als zentrale Services genutzt werden.
- **Konfigurierbarkeit:** Ermögliche das Setzen von Pfaden und Optionen über ENV-Variablen und systemd-Unit-Files.
- **Sicherheit:** Achte auf sichere Rechtevergabe für IPC und Datenbankzugriffe. Backup und Restore von PAM-Konfigurationen.
- **Fehlertoleranz:** Bei Fehlern in Policy oder Datenbank nie hart aussperren, sondern Warnungen ausgeben und permissiv weiterarbeiten.
- **Dokumentation:** Halte die README und die Docstrings aktuell, um die Entwicklung für weitere Mitwirkende zu erleichtern.

- **Offene Fragen:**
  - Wie werden Notifications technisch ausgelöst (guardian_agent)? DBus, Socket, Kommando?
  - Wie werden systemd-Timer nachgeholt, wenn der Rechner zum Reset-Zeitpunkt nicht läuft?
  - Wie werden Game Sessions und deren Enforcement/Notification technisch umgesetzt?
  - Wie flexibel und dynamisch sollen PAM-Regeln angepasst werden?

- **Explizite Nutzerüberwachung:**
  - Nur Nutzer, die unter `users:` in der Konfiguration eingetragen sind, werden vom Daemon überwacht und erhalten Quota-/Curfew-Regeln.
  - Ein leeres Objekt (z.B. `kid2: {}`) bedeutet, dass die Defaults für diesen Nutzer gelten.
  - Alle anderen Nutzer (z.B. Eltern, Admins, Systemkonten) werden ignoriert und sind von den Regeln ausgenommen.
  - Diese Logik muss in allen zukünftigen Komponenten (Enforcement, PAM, systemd, Netzwerk) berücksichtigt werden.

- **Modularität:** Halte die Schnittstellen zwischen Komponenten klar und einfach. Policy und Storage sollten als zentrale Services genutzt werden.
- **Konfigurierbarkeit:** Ermögliche das Setzen von Pfaden und Optionen über ENV-Variablen und systemd-Unit-Files.
- **Sicherheit:** Achte auf sichere Rechtevergabe für IPC und Datenbankzugriffe. Backup und Restore von PAM-Konfigurationen.
- **Fehlertoleranz:** Bei Fehlern in Policy oder Datenbank nie hart aussperren, sondern Warnungen ausgeben und permissiv weiterarbeiten.
- **Dokumentation:** Halte die README und die Docstrings aktuell, um die Entwicklung für weitere Mitwirkende zu erleichtern.

---

Für Fragen zur Architektur oder zur Implementation siehe die Haupt-README im Projektroot.
