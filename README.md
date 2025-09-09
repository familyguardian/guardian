# Guardian – Architektur- und Systemkonzept

## Übersicht

Guardian ist ein Multi-Device-Parental-Control-System für Linux, das Zeitkontingente, Curfews und geräteübergreifende Nutzungsgrenzen für Kinder durchsetzt. Die Architektur ist modular und besteht aus mehreren Python-Unterprojekten, die gemeinsam in einem Monorepo entwickelt werden.

---

## Entwicklungsworkflow mit VSCode, DevContainer und UV

Dieses Monorepo ist für die Entwicklung mit [Visual Studio Code](https://code.visualstudio.com/) und [DevContainers](https://containers.dev/) optimiert. Die DevContainer-Konfiguration sorgt für eine konsistente Umgebung mit vorinstalliertem Python, Node.js, Git und UV als Paketmanager und Script-Runner.

### Struktur & Dependency-Management

- **Jedes Unterprojekt** (`guardian_daemon`, `guardianctl`, `guardian_agent`, `guardian_hub`, etc.) besitzt ein eigenes `pyproject.toml` für Abhängigkeiten und Metadaten.
- Das Monorepo wird über einen zentralen Workspace in [`pyproject.toml`](pyproject.toml) verwaltet.
- **UV** wird als Paketmanager und Script-Runner für alle Unterprojekte verwendet.

### Typischer Workflow

1. **DevContainer öffnen**  
   Öffne das Repository in VSCode und wähle „Reopen in Container“. Die Umgebung wird automatisch eingerichtet.

2. **VirtualEnv pro Unterprojekt**  
   UV erstellt und verwaltet automatisch isolierte VirtualEnvs je Unterprojekt.  
   Beispiel für das Daemon-Projekt:
   ```sh
   cd guardian_daemon
   uv venv
   ```

3. **Dependencies installieren**  
   Im jeweiligen Unterprojekt:
   ```sh
   uv pip install -r requirements.txt  # falls requirements.txt existiert
   # oder direkt aus pyproject.toml:
   uv pip install .
   ```

4. **Dependencies updaten**  
   Im jeweiligen Unterprojekt:
   ```sh
   uv pip upgrade
   ```

5. **Scripts ausführen**  
   UV kann auch als Script-Runner genutzt werden:
   ```sh
   uv run main.py
   ```

6. **Entwicklung im Monorepo**  
   - Änderungen an Abhängigkeiten werden pro Unterprojekt im jeweiligen `pyproject.toml` gepflegt.
   - UV erkennt die Workspace-Struktur und installiert Abhängigkeiten nur für das aktive Unterprojekt.
   - Die DevContainer-Umgebung sorgt für konsistente Python-Versionen und Tools.

### Vorteile

- **Isolierte Environments**: Keine Abhängigkeitskonflikte zwischen Unterprojekten.
- **Schnelle Installation & Updates**: UV ist deutlich schneller als pip und Poetry.
- **Einheitlicher Workflow**: Alle Entwickler:innen nutzen die gleiche Umgebung und Tools.

---

## Ziele

- **Saubere Trennung pro Kind** durch eigene Linux-Konten.  
- **Zeitfenster & Tageskontingente** (ähnlich Google Family Link oder Amazon Eltern Dashboard).  
- **Geräteübergreifende Kontrolle**: Limits gelten über alle Laptops, Tower und Steam Decks hinweg.  
- **Eltern-Dashboard** (Web + CLI) zur Verwaltung und Überwachung.  
- **Robustheit**: auch bei Offline-Betrieb Enforcement lokal, bei Reconnect Sync mit zentralem Server.  

---

## Systemkomponenten

### guardian-daemon (Device-Agent)
- Python-Daemon, läuft als **systemd-Service** (root).
- Beobachtet Logins/Sitzungen via **systemd-logind (DBus)**.
- Zählt **tatsächlich genutzte Zeit** pro Kind (monotonic clock).
- Erzwingt **Quota & Curfews** (Login-Sperre, Live-Enforcement).
- Erstellt/verwaltet **systemd-Timer/Units** & **PAM-Regeln**.
- Kommuniziert mit zentralem Server (guardian-hub).

### guardianctl (CLI)
- Admin-Werkzeug (Python Typer): Policies anzeigen, Bonuszeit vergeben, Limits setzen, System neu generieren.
- Greift über Unix-Domain-Socket auf den Daemon oder über HTTPS auf den Hub zu.

### guardian-agent (optional pro User)
- User-Level-Service, zeigt Benachrichtigungen (DBus/notify-send).
- Nicht sicherheitskritisch, nur „freundlicher Hinweis“.

### guardian-hub (Zentralserver)
- **Quelle der Wahrheit** für Policies & Tageskonten.
- API (HTTP/JSON via FastAPI) + Realtime Push (WebSocket).
- Datenbank (Postgres für Prod; SQLite MVP).
- Web-UI (React/Next.js o. ä.): Eltern-Dashboard für Verwaltung und Live-Monitoring.
- Authentifizierung (Eltern-Login, ggf. 2FA).
- Audit-Log.

---

## Datenmodell

- **users**: Kinder, Linux-UID, Zeitzone, Name.
- **devices**: registrierte Geräte (Deck, Laptop, Tower).
- **enrollments**: Zuordnung Nutzer ↔ Geräte.
- **policies**: Tageskontingente, Curfew-Regeln, Grace-Zeit, App-Allowlist.
- **sessions**: aktive Sitzungen pro Kind & Gerät.
- **usage**: Tagesverbrauch pro Kind (geräteübergreifend).
- **audits**: Änderungen & Aktionen.

---

## Policy-Beispiel (config.yaml)

```yaml
timezone: "Europe/Berlin"
defaults:
  daily_quota_minutes: 90
  curfew:
    weekdays: "08:00-20:00"
    saturday: "08:00-22:00"
    sunday: "09:00-20:00"
  grace_minutes: 5
users:
  kid1:
    daily_quota_minutes: 60
    curfew:
      weekdays: "07:30-19:30"
  kid2:
    daily_quota_minutes: 90
    bonus_pool_minutes: 30
```

---

## Enforcement-Strategie

1. **Curfew (Login-Fenster)**  
   - PAM (`pam_time.so`) blockiert Logins außerhalb erlaubter Zeiten.

2. **Live-Quota (täglich)**  
   - Daemon zählt Nutzungszeit über logind.
   - Bei Erreichen:
     - Warnung (notify, optional agent).
     - Grace-Zeit.
     - Danach `loginctl terminate-user` oder gezielt „Game-Session“ Unit beenden.

3. **Geräteübergreifend**  
   - Jeder Agent sendet Heartbeats mit Verbrauch an Hub.
   - Hub akkumuliert global und broadcastet „terminate-user“ an **alle aktiven Geräte**.

4. **Reset**  
   - Server setzt Verbrauch täglich (z. B. 00:05) zurück.
   - Agents synchronisieren beim nächsten Heartbeat.

---

## Offline-Verhalten

- Agent cached letzte Policy + Verbrauch.
- Erzwingt lokal weiter, auch ohne Server.
- Bei Reconnect → **Delta-Sync** (`/usage/reconcile`).
- Konfliktlösung: **Server gewinnt**; Agent korrigiert (ggf. sofortige Beendigung, falls Kind schon drüber).

---

## Systemd-Integration (vom Daemon generiert)

- **guardian.service** (root-Daemon)
- **guardian.socket** (Admin-IPC, Gruppe `guardian-admin`)
- **curfew@.service / timer** (Logout pro Kind zu festen Zeiten)
- **daily-reset.service / timer** (Reset Quotas um 00:05)
- **gamesession@.service** (optional: Kiosk-Modus für Steam/Gamescope)
- **PAM-Managed Block** in `/etc/security/time.conf`

---

## Sicherheit

- **Geräte-Enrollment**: Token/PIN oder mTLS.
- **Transport**: TLS; Auth via JWT + Refresh oder mTLS.
- **IPC-Socket**: nur für `guardian-admin` Gruppe.
- **Zeitmessung**: monotonic clock (nicht manipulierbar über Systemzeit).
- **Fail-safe**: bei Policy/DB-Fehler → permissive Mode mit Warnung (nie hart aussperren wegen Bug).

---

## Roadmap

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

---

## Projektstruktur (Python)

```
guardian/
 ├─ guardian_daemon/     # Hauptdaemon (systemd-Service)
 │   ├─ main.py
 │   ├─ policy.py        # Policy-Modelle (pydantic)
 │   ├─ sessions.py      # logind-Watcher
 │   ├─ enforcer.py      # Quota/Curfew Enforcement
 │   ├─ pam_manager.py   # PAM time.conf Blöcke
 │   ├─ systemd_manager.py
 │   ├─ net_client.py    # API/WebSocket Hub
 │   ├─ storage.py       # SQLite
 │   └─ ipc.py           # Admin-Socket
 ├─ guardianctl/         # CLI-Tool
 │   └─ cli.py
 ├─ guardian_agent/      # User-Benachrichtigungen (optional)
 ├─ guardian_hub/        # Zentralserver (FastAPI, DB, Websocket)
 │   ├─ api.py
 │   ├─ models.py
 │   ├─ db.py
 │   └─ webui/           # React/Next.js Frontend
 ├─ pyproject.toml
 └─ scripts/
     └─ install_artifacts.py
```
