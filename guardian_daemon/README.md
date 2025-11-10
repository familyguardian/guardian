
# Guardian Daemon (guardian-daemon)

## Design Decisions

- **Explicit User Monitoring:** Only users listed under `users:` in the configuration are monitored.
  Parents/admins/system accounts are excluded.
- **Config Reload:** The configuration is reloaded every 5 minutes. Changes are automatically detected and lead to an
  update of systemd timers and PAM rules.
- **Dynamic Adjustment:** Timers and login rules are immediately adjusted when policies change, without restarting the daemon.
- **Timer Catch-Up:** If the computer is not running at the reset time, the daily reset is performed at the next startup.
- **Quota Calculation:** Daily quota is calculated from a configurable reset time, not from midnight. Running sessions
  are included.
  - Basic global parameters:
    - hub_address: Address of the Guardian Hub (empty = disabled)
  - db_path: Path to the SQLite database (default: /var/lib/guardian/guardian.sqlite)
  - ipc_socket: Path to the IPC socket (default: /run/guardian-daemon.sock)
  - Global configuration for quota warnings and grace period:
    - notifications: Configured at the top level and applies system-wide
      - pre_quota_minutes: List of minutes before quota end for warnings (e.g. [15, 10, 5])
      - grace_period.enabled: Enables the grace period
      - grace_period.duration: Duration of the grace period in minutes
      - grace_period.interval: Notification interval in minutes during the grace period
    - defaults: Reserved for user-specific default values (e.g. daily_quota_minutes, curfew, grace_minutes)

## Overview

`guardian-daemon` is the system-wide background service of the Guardian system for enforcing time quotas and curfews
for children on Linux devices. It runs as a systemd service with root privileges and is modular.

### Current Components

- **Quota Calculation**
  - Sums all sessions of a day since the last reset time and checks against the daily quota.
  - Takes grace period and running sessions into account.

- **Dynamic PAM Rules**
  - Login time rules are automatically adjusted with every policy change.
  - Rules apply explicitly only to users listed in the configuration (children).

- **Systemd Timer Management**
  - Daily reset and curfew are automated via systemd timers/units.
  - Timers are updated on policy change and caught up at startup if missed.

- **Error Logging**
  - All key actions and errors are logged to the systemd journal.

- **Policy Loader (`policy.py`)**
  - Loads the configuration from a YAML file (path via ENV `GUARDIAN_DAEMON_CONFIG` or fallback to `config.yaml`).
  - Provides methods for accessing user and default policies.

- **Storage (`storage.py`)**
  - Central SQLite interface for sessions and future extensions.
  - Enables saving and querying session data.

- **SessionTracker (`sessions.py`)**
  - Monitors logins/logouts via systemd-logind (DBus, with `dbus-next`).
  - Measures usage time per child and saves it in the database.
  - Checks quota/curfew according to the policy.

- **UserManager (`user_manager.py`)**
  - Manages PAM time-based access restrictions via `/etc/security/time.conf`.
  - Ensures the `kids` group exists and users are properly assigned.
  - Sets up user-specific systemd services for the guardian-agent.
  - Configures D-Bus policies for user agents.
  - Ensures `pam_time.so` module is active in PAM configuration.

- **Integration (`__main__.py`)**
  - Initializes all components and starts the daemon.
  - Policy and storage are passed centrally.
  - PAM rules are set at startup.
  - Session tracking runs asynchronously.

## Phase 0 Status (Local Device) — MOSTLY COMPLETE

Phase 0 components are mostly implemented and functional:

✅ **Completed:**
- Daemon (systemd service with root privileges)
- Policy loader with YAML configuration
- PAM time window enforcement via `/etc/security/time.conf`
- logind session watcher via D-Bus
- Systemd timer management (curfew/daily reset)
- guardianctl CLI with dynamic command discovery
- IPC server for admin commands
- SQLAlchemy-based storage layer
- Grace period and notification system
- User and group management

⚠️ **Partially Implemented:**
- guardian-agent (notifications work but needs more testing)
- Session history tracking (basic implementation exists)

🚧 **Not Yet Started (Future Phases):**
- Network client for hub communication (`net_client.py` is stub)
- guardian-hub server (Phase 1)
- Multi-device coordination (Phase 2)
- App allowlists/blocklists (Phase 3)
- Kiosk mode for game sessions (Phase 3)

## Remaining TODOs for Phase 0

- **Testing**
  - Expand unit test coverage for all modules
  - Create integration tests with mocked D-Bus and systemd
  - Test edge cases (system hibernation, clock changes, etc.)

- **Documentation**
  - Add more examples to configuration file
  - Document common troubleshooting scenarios
  - Create architecture diagrams

- **Robustness**
  - Improve error handling for D-Bus connection failures
  - Add retry logic for transient failures
  - Better handling of malformed configuration files

## Roadmap / Phases

### Phase 0 — Local (per device)

- Daemon (systemd), policy loader, PAM time windows, logind watcher, timer for curfew/reset.
- guardianctl (CLI).

### Phase 1 — Hub (MVP)

- Server with policies, usage, sessions, API.
- Device enrollment, policy pull, heartbeats.
- Daily reset server-side.

### Phase 2 — Multi-Device & Push

- WebSocket push: immediate termination on all devices.
- Conflict resolution + offline deltas.
- Parent dashboard with live status.

### Phase 3 — Comfort & Hardening

- Roles/multiple parents, 2FA, notifications (mail/signal/matrix).
- Allowlist/blocklist for apps.
- Kiosk mode units per child.

## Systemd Integration (generated by the daemon)

- **guardian.service** (root daemon)
- **guardian.socket** (admin IPC, group `guardian-admin`)
- **<curfew@.service> / timer** (logout per child at fixed times)
- **daily-reset.service / timer** (reset quotas at configurable time, e.g. 03:00)
- **<gamesession@.service>** (optional: kiosk mode for Steam/Gamescope)
- **PAM-managed block** in `/etc/security/time.conf`

- **Explicit User Monitoring:**
  - Only users listed under `users:` in the configuration are monitored by the daemon and receive quota/curfew rules.
  - An empty object (e.g. `kid2: {}`) means that defaults apply for this user.
  - All other users (e.g. parents, admins, system accounts) are ignored and exempt from the rules.
  - This logic must be considered in all future components (enforcement, PAM, systemd, network).

- **Modularity:** Keep interfaces between components clear and simple. Policy and storage should be used as central services.
- **Configurability:** Allow setting paths and options via ENV variables and systemd unit files.
- **Security:** Ensure secure permissions for IPC and database access. Backup and restore of PAM configurations.
- **Fault Tolerance:** Never hard lock out users due to errors in policy or database, but issue warnings and continue permissively.
- **Documentation:** Keep the README and docstrings up to date to facilitate development for further contributors.

## Design Principles

- **Explicit User Monitoring:**
  - Only users listed under `users:` in the configuration are monitored by the daemon and receive quota/curfew rules.
  - An empty object (e.g. `kid2: {}`) means that defaults apply for this user.
  - All other users (e.g. parents, admins, system accounts) are ignored and exempt from the rules.
  - This logic must be considered in all future components (enforcement, PAM, systemd, network).

- **Modularity:** Keep interfaces between components clear and simple. Policy and storage should be used as central services.
- **Configurability:** Allow setting paths and options via ENV variables and systemd unit files.
- **Security:** Ensure secure permissions for IPC and database access. Backup and restore of PAM configurations.
- **Fault Tolerance:** Never hard lock out users due to errors in policy or database, but issue warnings and continue permissively.
- **Documentation:** Keep the README and docstrings up to date to facilitate development for further contributors.

## Technical Implementation Notes

- **Notifications:** Triggered via D-Bus to guardian-agent user service, which uses `notify-send`
- **Timer Catch-Up:** Systemd timers with `Persistent=true` automatically catch up missed runs on next boot
- **Game Sessions:** Planned for Phase 3 with custom systemd units per child
- **PAM Rules:** Dynamic generation based on policy, safely preserved non-Guardian rules

---

For questions about the architecture or implementation, see the main README in the project root.
