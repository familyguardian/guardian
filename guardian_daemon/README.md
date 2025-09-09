# guardian-daemon

## Überblick

`guardian-daemon` ist der systemweite Hintergrunddienst des Guardian-Systems zur Durchsetzung von Zeitkontingenten und Curfews für Kinder auf Linux-Geräten. Er läuft als systemd-Service mit Root-Rechten und ist modular aufgebaut.

### Bisherige Komponenten

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


## Geplante/Offene Schritte & Designentscheidungen

- **Enforcement-Logik**
  - Sessions werden erst nach Erreichen von Quota + Grace-Minutes beendet.
  - Vor dem Logout werden Desktop-Notifications verschickt (guardian_agent). Die Häufigkeit und Schwellenwerte der Notifications müssen noch festgelegt werden. **TODO: Design für Notification-Frequenz.**
  - Session-Beendigung erfolgt zunächst mit `loginctl terminate-user <UID>`. **TODO: Später prüfen, ob gezieltere Aktionen nötig sind.**
  - Game Sessions (Steam/Kiosk) benötigen eine eigene Behandlung und Notification-Logik. **TODO: Konzept für Game Sessions und deren Enforcement/Notification.**

- **PAM-Integration**
  - Login-Zeitregeln werden dynamisch angepasst, z.B. bei Bonuszeit oder Policy-Änderung.
  - pam_time-Regeln gelten explizit nur für die in der Konfiguration aufgeführten Nutzer (Kinder). Eltern/Admins sind ausgenommen. **Hinweis: Die Syntax und Logik muss dies sicherstellen.**

- **Systemd-Timer und Reset**
  - systemd-Timer werden für Tagesreset und Curfew genutzt. Der Reset kann z.B. durch einen guardianctl-Command ausgelöst werden.
  - **Offene Frage:** Was passiert, wenn der Rechner zum Reset-Zeitpunkt nicht läuft? (Nachholen beim nächsten Start?)

- **Fehlerbehandlung**
  - Fehler beim Enforcement (z.B. Session-Beendigung) werden geloggt. Später kann eine Nachricht an den Hub gesendet werden. **TODO: Logging mit möglichst vielen Details.**

- **Notifications/guardian_agent**
  - Desktop-Notifications werden über guardian_agent verschickt. **TODO: Schnittstelle und Protokoll für die Kommunikation festlegen.**


- **Enforcement-Logik**
  - Implementiere die Überwachung und Durchsetzung von Quota/Curfew (z.B. Beenden von Sessions, Sperren von Logins).
  - Integration mit systemd und PAM für Live-Enforcement.

- **Systemd- und Timer-Management**
  - Automatisches Erstellen und Verwalten von systemd-Units/Timer für Curfew und Tagesreset.
  - Datei: `systemd_manager.py`

- **Netzwerk-Client**
  - Kommunikation mit zentralem Guardian-Hub (API/WebSocket).
  - Synchronisation von Policies und Usage-Daten.
  - Datei: `net_client.py`

- **Admin-IPC**
  - Implementiere einen lokalen Socket für Admin-Kommandos (Bonuszeit, Policy-Reload, etc.).
  - Datei: `ipc.py`

- **Quota-Berechnung**
  - Summiere alle Sessions eines Tages und prüfe gegen das Tageskontingent.
  - Berücksichtige Grace-Zeit und Bonuszeit.

- **Fehler- und Ausnahmebehandlung**
  - Fallback auf permissiven Modus bei Policy- oder DB-Fehlern.
  - Logging und Audit-Trail.

- **Tests und Mocking**
  - Schreibe Unit- und Integrationstests für alle Kernmodule.
  - Mock DBus und systemd für lokale Tests.



## Hinweise zur weiteren Implementation & offene Fragen

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
