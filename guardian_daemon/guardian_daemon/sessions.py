"""
Session-Tracking für guardian-daemon
Überwacht Logins/Logouts via systemd-logind (DBus), misst Nutzungszeit und prüft Quota/Curfew.
Speichert Daten in SQLite.
"""

import asyncio
import os
import time

import yaml
from dbus_next.aio import MessageBus

from guardian_daemon.policy import Policy
from guardian_daemon.storage import Storage


class SessionTracker:
    """
    Überwacht und speichert Nutzersessions, prüft Quota und Curfew.
    Bindet sich an systemd-logind via DBus.
    """

    def __init__(self, policy: Policy, config: dict):
        """
        Initialisiert den SessionTracker.

        Args:
            policy (Policy): Policy-Instanz
            config (dict): Geparste Konfiguration
        """
        self.policy = policy
        db_path = config.get("db_path", "guardian.sqlite")
        self.storage = Storage(db_path)
        self.active_sessions: dict[str, dict[str, float | int | str]] = (
            {}
        )  # session_id -> {uid, username, start_time}

    def handle_login(self, session_id, uid, username, props):
        """
        Registriert eine neue Session beim Login, aber nur für Kinder-Accounts.

        Args:
            session_id (str): Session-ID
            uid (int): User-ID
            username (str): Nutzername
        """
        kids = set(self.policy.data.get("users", {}).keys())
        if username not in kids:
            print(f"Ignoriere Session von {username} (UID {uid}) Session {session_id}")
            return
        self.active_sessions[session_id] = {
            "uid": uid,
            "username": username,
            "start_time": time.monotonic(),
        }
        # Debug-Ausgabe aller Infos vor dem Schreiben
        desktop = props.get("Desktop", None)
        service = props.get("Service", None)
        print(
            f"[DEBUG] Schreibe Session in DB: session_id={session_id}, username={username}, uid={uid}, start_time={self.active_sessions[session_id]['start_time']}, end_time=0.0, duration=0.0, desktop={desktop}, service={service}"
        )
        # Session-Eintrag mit end_time und duration=0 erstellen
        self.storage.add_session(
            session_id,
            username,
            uid,
            self.active_sessions[session_id]["start_time"],
            0.0,
            0.0,
            desktop,
            service,
        )
        print(f"Login: {username} (UID {uid}) Session {session_id}")

    def handle_logout(self, session_id):
        """
        Beendet eine Session beim Logout und speichert sie in der Datenbank (nur für Kinder-Accounts).

        Args:
            session_id (str): Session-ID
        """
        session = self.active_sessions.pop(session_id, None)
        if session:
            kids = set(self.policy.data.get("users", {}).keys())
            if session["username"] not in kids:
                print(
                    f"Ignoriere Logout von {session['username']} Session {session_id}"
                )
                return
            end_time = time.monotonic()
            duration = end_time - session["start_time"]
            # Debug-Ausgabe aller Infos vor dem Update
            print(
                f"[DEBUG] Update Session in DB: session_id={session_id}, username={session['username']}, uid={session['uid']}, start_time={session['start_time']}, end_time={end_time}, duration={duration}"
            )
            # Session-Eintrag aktualisieren
            self.storage.update_session_logout(session_id, end_time, duration)
            print(
                f"Logout: {session['username']} Session {session_id} Dauer: {duration:.1f}s"
            )

    def check_quota(self, username):
        """
        Summiert alle Sessions seit dem letzten Reset-Zeitpunkt und prüft gegen das Tageskontingent.
        Gibt True zurück, wenn noch Zeit übrig ist, sonst False.

        Args:
            username (str): Nutzername

        Returns:
            bool: True wenn noch Zeit übrig, False wenn Limit erreicht
        """
        user_policy = self.policy.get_user_policy(username)
        if user_policy is None:
            return True  # Nutzer wird nicht überwacht
        quota = user_policy.get("daily_quota_minutes")
        if quota is None:
            quota = self.policy.get_default("daily_quota_minutes")

        import datetime

        reset_time = self.policy.data.get("reset_time", "03:00")
        now = datetime.datetime.now(datetime.timezone.utc).astimezone()
        reset_hour, reset_minute = map(int, reset_time.split(":"))
        today_reset = now.replace(
            hour=reset_hour, minute=reset_minute, second=0, microsecond=0
        )
        if now < today_reset:
            last_reset = today_reset - datetime.timedelta(days=1)
        else:
            last_reset = today_reset

        sessions = self.storage.get_sessions_for_user(
            username, since=last_reset.timestamp()
        )
        # Filter: Nur Sessions mit sinnvoller Dauer (> 0.5 min) und ggf. keine SDDM/Service-Logins
        filtered_sessions = [
            s for s in sessions if s[6] > 30
        ]  # s[6] = duration (Sekunden), >30s
        total_minutes = sum((s[6] for s in filtered_sessions)) / 60

        for session in self.active_sessions.values():
            if session["username"] == username:
                total_minutes += (time.monotonic() - session["start_time"]) / 60

        return total_minutes < quota

    async def run(self):
        """
        Startet das Session-Tracking und bindet sich an systemd-logind via DBus.
        """
        from dbus_next.constants import BusType

        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        introspection = await bus.introspect(
            "org.freedesktop.login1", "/org/freedesktop/login1"
        )
        obj = bus.get_proxy_object(
            "org.freedesktop.login1", "/org/freedesktop/login1", introspection
        )
        manager = obj.get_interface("org.freedesktop.login1.Manager")

        async def get_session_info(object_path):
            session_obj = bus.get_proxy_object(
                "org.freedesktop.login1",
                object_path,
                await bus.introspect("org.freedesktop.login1", object_path),
            )
            session_iface = session_obj.get_interface("org.freedesktop.login1.Session")
            # Username
            username = await session_iface.get_name()
            # UID
            user_struct = await session_iface.get_user()
            uid = (
                user_struct[0]
                if isinstance(user_struct, (list, tuple))
                else user_struct
            )
            return username, uid

        def session_new_handler(session_id, object_path):
            async def inner():
                # Session-Objekt holen
                session_obj = bus.get_proxy_object(
                    "org.freedesktop.login1",
                    object_path,
                    await bus.introspect("org.freedesktop.login1", object_path),
                )
                session_iface = session_obj.get_interface(
                    "org.freedesktop.login1.Session"
                )
                # Alle Properties dynamisch auslesen
                props = {}
                introspection = await bus.introspect(
                    "org.freedesktop.login1", object_path
                )
                session_interface = next(
                    (
                        iface
                        for iface in introspection.interfaces
                        if iface.name == "org.freedesktop.login1.Session"
                    ),
                    None,
                )
                if session_interface:
                    property_names = [p.name for p in session_interface.properties]
                    for prop in property_names:
                        getter = getattr(session_iface, f"get_{prop.lower()}", None)
                        if getter:
                            try:
                                props[prop] = await getter()
                            except Exception as e:
                                props[prop] = f"[ERROR: {e}]"
                        else:
                            props[prop] = "[NO GETTER]"
                else:
                    props = {"error": "Session interface not found in introspection"}
                # Hole Name und User explizit
                username = props.get("Name", None)
                user_struct = props.get("User", None)
                uid = (
                    user_struct[0]
                    if isinstance(user_struct, (list, tuple))
                    else user_struct
                )
                kids = set(self.policy.data.get("users", {}).keys())
                if username not in kids:
                    print(
                        f"Ignoriere Session von {username} (UID {uid}) Session {session_id}"
                    )
                    return
                print(f"[DEBUG] Alle Session-Properties für {session_id}: {props}")
                print(f"[DEBUG] Extrahiert: Name={username}, UID={uid}")
                self.handle_login(session_id, uid, username, props)

            asyncio.create_task(inner())

        def session_removed_handler(session_id, object_path):
            # Keine D-Bus-Abfrage mehr, sondern lokale Daten nutzen
            self.handle_logout(session_id)

        manager.on_session_new(session_new_handler)
        manager.on_session_removed(session_removed_handler)

        print("SessionTracker läuft. Warten auf Logins/Logouts...")
        while True:
            await asyncio.sleep(3600)

    def _get_username(self, uid):
        """
        Holt den Nutzernamen zu einer UID.

        Args:
            uid (int): User-ID

        Returns:
            str: Nutzername
        """
        import pwd

        try:
            return pwd.getpwuid(uid).pw_name
        except Exception:
            return str(uid)


if __name__ == "__main__":
    # Lade Default-Konfiguration
    base_dir = os.path.dirname(os.path.abspath(__file__))
    default_config_path = os.path.join(base_dir, "../default-config.yaml")
    with open(default_config_path, "r") as f:
        config = yaml.safe_load(f)
    # Überschreibe mit Werten aus config.yaml, falls vorhanden
    config_path = os.path.join(base_dir, "../config.yaml")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            user_config = yaml.safe_load(f)
        if user_config:
            config.update(user_config)
    policy = Policy(config_path)
    tracker = SessionTracker(policy, config)
    asyncio.run(tracker.run())
# logind watcher
