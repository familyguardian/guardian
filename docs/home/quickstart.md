# Quickstart

Welcome to Guardian! This guide will help you get started with installing and configuring Guardian.

> **Important:** Guardian is experimental software and not ready for production use. Use at your own risk.

## Prerequisites

- Linux system with systemd and D-Bus
- Python 3.10 or higher
- Root/sudo access for installation
- `authselect` (for PAM configuration)
- `notify-send` (for user notifications)

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/familyguardian/guardian.git
cd guardian
```

### 2. Install Dependencies

Guardian uses [uv](https://github.com/astral-sh/uv) for dependency management:

```bash
# Install uv if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install guardian-daemon
cd guardian_daemon
uv pip install .

# Install guardianctl CLI
cd ../guardianctl
uv pip install .

# Install guardian-agent (optional, for notifications)
cd ../guardian_agent
uv pip install .
```

## Configuration

### 1. Create Configuration File

Create a configuration file at `/etc/guardian/daemon/config.yaml`:

```yaml
# Basic Guardian Configuration
timezone: "Europe/Berlin"
db_path: "/var/lib/guardian/guardian.sqlite"
ipc_socket: "/run/guardian-daemon.sock"
reset_time: "03:00"

logging:
  level: INFO
  format: plain
  target: stdout

defaults:
  daily_quota_minutes: 90
  curfew:
    weekdays: "08:00-20:00"
    saturday: "08:00-22:00"
    sunday: "09:00-20:00"

users:
  kid1:
    daily_quota_minutes: 60
    curfew:
      weekdays: "07:30-19:30"
      saturday: "08:00-21:00"
      sunday: "09:00-19:00"
```

### 2. Create Required Directories

```bash
sudo mkdir -p /var/lib/guardian
sudo mkdir -p /etc/guardian/daemon
```

### 3. Set Up Systemd Services

```bash
# Copy systemd service files
sudo cp systemd_units/system/guardian_daemon.service /etc/systemd/system/
sudo cp systemd_units/user/guardian_agent.service /etc/systemd/user/

# Reload systemd
sudo systemctl daemon-reload

# Enable and start the daemon
sudo systemctl enable guardian_daemon.service
sudo systemctl start guardian_daemon.service
```

## Basic Usage

### Check Daemon Status

```bash
sudo systemctl status guardian_daemon.service
```

### Using guardianctl CLI

The `guardianctl` command provides various management functions:

```bash
# Check daemon connection
guardianctl status

# List managed users
guardianctl list-kids

# Get quota information for a user
guardianctl get-quota kid1

# View systemd timers
guardianctl list-timers

# View logs
guardianctl logs
```

### Verify Configuration

```bash
# Check configuration
guardianctl config-check

# Check socket connectivity
guardianctl socket-check
```

## Troubleshooting

### Daemon Won't Start

1. Check logs: `sudo journalctl -u guardian_daemon.service -n 50`
2. Verify configuration: `guardianctl config-check`
3. Check permissions on `/var/lib/guardian/`

### User Can't Log In

1. Check PAM configuration: `sudo cat /etc/security/time.conf`
2. Verify user is in the `kids` group: `groups username`
3. Check if outside curfew hours
4. Review daemon logs for enforcement actions

### CLI Can't Connect

1. Check socket exists: `guardianctl socket-check`
2. Verify daemon is running: `sudo systemctl status guardian_daemon.service`
3. Check socket permissions: `ls -l /run/guardian-daemon.sock`

## Next Steps

- Read the [full documentation](../index.md)
- Learn about [implementation details](../developer/daemon_implementation_notes.md)
- Understand [curfew configuration](../developer/curfew_implementation.md)
- Explore [Guardian Daemon API](../reference/daemon.md)
