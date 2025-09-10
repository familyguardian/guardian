
import socket
import json
import os

from guardian_daemon.policy import Policy


class GuardianIPCServer:
    """
    IPC-Server für Admin-Kommandos des Guardian-Daemon.
    Stellt eine Socket-Schnittstelle für Status- und Steuerbefehle bereit.
    """
    def __init__(self, config):
        """
        Initialisiert den IPC-Server und öffnet den Unix-Socket.

        Args:
            config (dict): Konfigurationsdaten
        """
        self.policy = Policy()
        self.socket_path = config.get('ipc_socket', '/run/guardian-daemon.sock')
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if os.path.exists(self.socket_path):
            os.remove(self.socket_path)
        self.server.bind(self.socket_path)
        self.server.listen(1)
        self.handlers = {
            'list_kids': self.handle_list_kids,
            'get_quota': self.handle_get_quota,
            'get_curfew': self.handle_get_curfew,
            'list_timers': self.handle_list_timers,
            'reload_timers': self.handle_reload_timers,
            'reset_quota': self.handle_reset_quota,
        }

    def serve_once(self):
        """
        Wartet auf einen eingehenden Befehl, führt den passenden Handler aus und sendet die Antwort zurück.
        """
        conn, _ = self.server.accept()
        data = conn.recv(1024).decode().strip()
        cmd, *args = data.split(' ', 1)
        handler = self.handlers.get(cmd)
        if handler:
            arg = args[0] if args else None
            response = handler(arg)
            conn.sendall(response.encode())
        else:
            conn.sendall(b'Unknown command')
        conn.close()

    def handle_list_kids(self, _):
        """
        Gibt die Liste aller Kinder (Nutzer) zurück.
        """
        kids = self.policy.storage.get_all_usernames()
        return json.dumps({'kids': kids})

    def handle_get_quota(self, kid):
        """
        Gibt den aktuellen Quota-Stand eines Kindes zurück.

        Args:
            kid (str): Nutzername
        """
        if not kid:
            return json.dumps({'error': 'missing kid'})
        from guardian_daemon.sessions import SessionTracker
        tracker = SessionTracker(self.policy)
        user_policy = self.policy.get_user_policy(kid)
        if user_policy is None:
            return json.dumps({'error': 'unknown kid'})
        quota = user_policy.get('daily_quota_minutes')
        if quota is None:
            quota = self.policy.get_default('daily_quota_minutes')
        import datetime
        reset_time = self.policy.data.get('reset_time', '03:00')
        now = datetime.datetime.now(datetime.timezone.utc).astimezone()
        reset_hour, reset_minute = map(int, reset_time.split(':'))
        today_reset = now.replace(hour=reset_hour, minute=reset_minute, second=0, microsecond=0)
        if now < today_reset:
            last_reset = today_reset - datetime.timedelta(days=1)
        else:
            last_reset = today_reset
        storage = tracker.storage
        sessions = storage.get_sessions_for_user(kid, since=last_reset.timestamp())
        used = sum((s[6] for s in sessions)) / 60  # Minuten
        for session in tracker.active_sessions.values():
            if session['username'] == kid:
                used += (datetime.datetime.now().timestamp() - session['start_time']) / 60
        remaining = max(0, quota - used)
        return json.dumps({'kid': kid, 'used': round(used, 2), 'limit': quota, 'remaining': round(remaining, 2)})

    def handle_get_curfew(self, kid):
        """
        Gibt die aktuellen Curfew-Zeiten eines Kindes zurück.

        Args:
            kid (str): Nutzername
        """
        if not kid:
            return json.dumps({'error': 'missing kid'})
        user_policy = self.policy.get_user_policy(kid)
        if user_policy is None:
            return json.dumps({'error': 'unknown kid'})
        curfew = user_policy.get('curfew')
        if curfew is None:
            curfew = self.policy.get_default('curfew')
        return json.dumps({'kid': kid, 'curfew': curfew})

    def handle_list_timers(self, _):
        """
        Listet alle aktiven Guardian-Timer.
        """
        from guardian_daemon.systemd_manager import SYSTEMD_PATH
        timers = []
        for f in os.listdir(SYSTEMD_PATH):
            if f.endswith('.timer') and f.startswith('guardian-'):
                timers.append(f[:-6])
        return json.dumps({'timers': timers})

    def handle_reload_timers(self, _):
        """
        Lädt die Timer-Konfiguration neu.
        """
        from guardian_daemon.systemd_manager import SystemdManager
        mgr = SystemdManager()
        mgr.create_daily_reset_timer()
        return json.dumps({'status': 'timers reloaded'})

    def handle_reset_quota(self, _):
        """
        Setzt das Tageskontingent für alle Nutzer zurück (löscht Sessions ab letztem Reset).
        """
        import datetime
        reset_time = self.policy.data.get('reset_time', '03:00')
        now = datetime.datetime.now(datetime.timezone.utc).astimezone()
        reset_hour, reset_minute = map(int, reset_time.split(':'))
        today_reset = now.replace(hour=reset_hour, minute=reset_minute, second=0, microsecond=0)
        if now < today_reset:
            last_reset = today_reset - datetime.timedelta(days=1)
        else:
            last_reset = today_reset
        self.policy.storage.delete_sessions_since(last_reset.timestamp())
        return json.dumps({'status': 'quota reset'})

    def close(self):
        """
        Schließt den IPC-Socket und entfernt die Socket-Datei.
        """
        self.server.close()
        if os.path.exists(self.socket_path):
            os.remove(self.socket_path)
