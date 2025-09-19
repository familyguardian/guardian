# Guardian – Architecture and System Concept

## Overview

Guardian is a multi-device parental control system for Linux that enforces time quotas, curfews, and cross-device usage
limits for children. The architecture is modular and consists of several Python subprojects developed together in a monorepo.

---

## Dynamics & Robustness

- **Config Reload:** The configuration is periodically reloaded in the daemon (e.g., every 5 minutes). Changes are
  automatically detected and lead to an update of systemd timers and PAM rules.
- **Dynamic Adjustment:** Timers and login rules are immediately adjusted when policies change, without restarting the daemon.
- **Timer Catch-Up:** If the computer is not running at the reset time, the daily reset is performed at the next startup.
- **Logging:** All key actions and errors are logged to the systemd journal to facilitate operation and debugging.

This monorepo is optimized for development with [Visual Studio Code](https://code.visualstudio.com/) and
[DevContainers](https://containers.dev/). The DevContainer configuration ensures a consistent environment with
pre-installed Python, Node.js, Git, and UV as package manager and script runner.

### Structure & Dependency Management

- **Each subproject** (`guardian_daemon`, `guardianctl`, `guardian_agent`, `guardian_hub`, etc.) has its own
  `pyproject.toml` for dependencies and metadata.
- The monorepo is managed via a central workspace in [`pyproject.toml`](pyproject.toml).
- **UV** is used as the package manager and script runner for all subprojects.

### Typical Workflow

1. **Open DevContainer**  
   Open the repository in VSCode and select "Reopen in Container". The environment is set up automatically.

2. **VirtualEnv per subproject**  
   UV automatically creates and manages isolated virtualenvs for each subproject.  
   Example for the daemon project:

   ```sh
   cd guardian_daemon
   uv venv
   ```

3. **Install dependencies**  
   In the respective subproject:

   ```sh
   uv pip install -r requirements.txt  # if requirements.txt exists
   # or directly from pyproject.toml:
   uv pip install .
   ```

4. **Update dependencies**  
   In the respective subproject:

   ```sh
   uv pip upgrade
   ```

5. **Run scripts**  
   UV can also be used as a script runner:

   ```sh
   uv run main.py
   ```

6. **Development in the monorepo**  
   - Changes to dependencies are maintained per subproject in the respective `pyproject.toml`.
   - UV recognizes the workspace structure and installs dependencies only for the active subproject.
   - The DevContainer environment ensures consistent Python versions and tools.

### Advantages

- **Isolated environments:** No dependency conflicts between subprojects.
- **Fast installation & updates:** UV is much faster than pip and Poetry.
- **Unified workflow:** All developers use the same environment and tools.

---

## Goals

- **Clean separation per child** through individual Linux accounts.  
- **Time windows & daily quotas** (similar to Google Family Link or Amazon Parent Dashboard).  
- **Cross-device control:** Limits apply across all laptops, towers, and Steam Decks.  
- **Parent dashboard** (Web + CLI) for management and monitoring.  
- **Robustness:** Enforcement locally even when offline, sync with central server upon reconnect.  

---

## System Components

### guardian-daemon (Device Agent)

- Python daemon, runs as a **systemd service** (root).
- Monitors logins/sessions via **systemd-logind (DBus)**.
- Tracks **actual usage time** per child (monotonic clock).
- Enforces **quota & curfews** (login block, live enforcement).
- Creates/manages **systemd timers/units** & **PAM rules**.
- Communicates with central server (guardian-hub).

### guardianctl (CLI)

- Admin tool (Python Typer): View policies, grant bonus time, set limits, regenerate system.
- Accesses the daemon via Unix domain socket or the hub via HTTPS.

### guardian-agent (optional per user)

- User-level service, displays notifications (DBus/notify-send).
- Not security-critical, just a "friendly reminder".

### guardian-hub (Central Server)

- **Source of truth** for policies & daily accounts.
- API (HTTP/JSON via FastAPI) + realtime push (WebSocket).
- Database (Postgres for production; SQLite MVP).
- Web UI (React/Next.js etc.): Parent dashboard for management and live monitoring.
- Authentication (parent login, optionally 2FA).
- Audit log.

---

## Data Model

- **users:** Children, Linux UID, timezone, name.
- **devices:** Registered devices (Deck, Laptop, Tower).
- **enrollments:** Assignment user ↔ devices.
- **policies:** Daily quotas, curfew rules, grace period, app allowlist.
- **sessions:** Active sessions per child & device.
- **usage:** Daily usage per child (cross-device).
- **audits:** Changes & actions.

---

## Policy Example (config.yaml)

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

## Enforcement Strategy

1. **Curfew (Login Window)**  
   - PAM (`pam_time.so`) blocks logins outside allowed times.

2. **Live Quota (daily)**  
   - Daemon tracks usage time via logind.
   - When reached:
     - Warning (notify, optional agent).
     - Grace period.
     - Then `loginctl terminate-user` or specifically terminate "Game-Session" unit.

3. **Cross-device**  
   - Each agent sends heartbeats with usage to the hub.
   - Hub accumulates globally and broadcasts "terminate-user" to **all active devices**.

4. **Reset**  
   - Server resets usage daily (e.g., 00:05).
   - Agents synchronize on next heartbeat.

---

## Offline Behavior & Multi-Device Vision

- The daemon caches the last policy and usage locally and enforces the rules even without connection to the hub.
- Upon reconnect, local usage data is synchronized with the hub (delta sync).
- Conflict resolution: The server (hub) is the source of truth; the daemon immediately corrects local data if necessary.
- The architecture is designed to synchronize and enforce quota and curfew across devices (multi-device).

---

## Systemd Integration (generated by the daemon)

- **guardian.service** (root daemon)
- **guardian.socket** (admin IPC, group `guardian-admin`)
- **<curfew@.service> / timer** (logout per child at fixed times)
- **daily-reset.service / timer** (reset quotas at configurable time, e.g., 03:00)
- **<gamesession@.service>** (optional: kiosk mode for Steam/Gamescope)
- **PAM-managed block** in `/etc/security/time.conf`
- **Automatic activation and catch-up of timers:** Timers are automatically updated on policy change and caught up at
  startup if missed.

---

## Security

- **Device enrollment:** Token/PIN or mTLS.
- **Transport:** TLS; auth via JWT + refresh or mTLS.
- **IPC socket:** only for `guardian-admin` group.
- **Time measurement:** monotonic clock (cannot be manipulated via system time).
- **Fail-safe:** on policy/DB error → permissive mode with warning (never hard lockout due to bug).

---

## Roadmap

**Phase 0 — Local (per device)**  

- Daemon (systemd), policy loader, PAM time windows, logind watcher, timer for curfew/reset.
- guardianctl (CLI).

**Phase 1 — Hub (MVP)**  

- Server with policies, usage, sessions, API.
- Device enrollment, policy pull, heartbeats.
- Daily reset server-side.

**Phase 2 — Multi-Device & Push**  

- WebSocket push: immediate termination on all devices.
- Conflict resolution + offline deltas.
- Parent dashboard with live status.

**Phase 3 — Comfort & Hardening**  

- Roles/multiple parents, 2FA, notifications (mail/signal/matrix).
- Allowlist/blocklist for apps.
- Kiosk mode units per child.

---

## Project Structure (Python)

```text
guardian/
 ├─ guardian_daemon/     # Main daemon (systemd service)
 │   ├─ main.py
 │   ├─ policy.py        # Policy models (pydantic)
 │   ├─ sessions.py      # logind watcher
 │   ├─ enforcer.py      # Quota/curfew enforcement
 │   ├─ pam_manager.py   # PAM time.conf blocks
 │   ├─ systemd_manager.py
 │   ├─ net_client.py    # API/WebSocket hub
 │   ├─ storage.py       # SQLite
 │   └─ ipc.py           # Admin socket
 ├─ guardianctl/         # CLI tool
 │   └─ cli.py
 ├─ guardian_agent/      # User notifications (optional)
 ├─ guardian_hub/        # Central server (FastAPI, DB, Websocket)
 │   ├─ api.py
 │   ├─ models.py
 │   ├─ db.py
 │   └─ webui/           # React/Next.js frontend
 ├─ pyproject.toml
 └─ scripts/
     └─ install_artifacts.py
```

---

## Build Documentation

The central API and project documentation is generated with [Sphinx](https://www.sphinx-doc.org/) from all subprojects.

### Prerequisites

- All Python dependencies and Sphinx must be installed in the DevContainer/venv.
- The subprojects must be installed as packages (editable install).

### Step-by-step

1. **Open DevContainer**
   - Open the repository in VSCode and select "Reopen in Container".

2. **Build docs**
   - In the project root:

     ```sh
     bash scripts/gen_docs.sh
     ```

   - The script installs all subprojects as packages and builds the central Sphinx documentation.
   - The finished HTML docs are located in `docs/_build/html`.

### Notes

- The docstrings from all modules are automatically integrated.
- Static pages and API docs are centrally maintained in `docs/index.rst`.
- After changes to modules or docstrings, simply run the script again.
