"""
Enforcement-Modul für guardian-daemon
Prüft Quota und Curfew, erzwingt Limits durch Session-Beendigung und Login-Sperre.
"""

from guardian_daemon.policy import Policy
from guardian_daemon.sessions import SessionTracker


class Enforcer:
    def __init__(self, policy: Policy, tracker: SessionTracker):
        self.policy = policy
        self.tracker = tracker

    def enforce_user(self, username):
        """
        Prüft Quota und Curfew für einen Nutzer und erzwingt ggf. Maßnahmen.
        """
        # Quota Enforcement
        if not self.tracker.check_quota(username):
            self.notify_user(username, "Quota erreicht! Deine Grace-Zeit beginnt.")
            # TODO: Grace-Minutes Timer/Countdown
            # TODO: Notification vor Ablauf der Grace-Zeit
            # Nach Ablauf der Grace-Zeit:
            self.terminate_session(username)
            self.notify_user(username, "Deine Sitzung wird jetzt beendet.")

        # Curfew Enforcement (optional, z.B. via PAMManager)
        # TODO: Curfew-Check und ggf. Login sperren

    def terminate_session(self, username):
        """
        Beendet alle laufenden Sessions des Nutzers (z.B. via systemd oder loginctl).
        """
        # TODO: Integration mit systemd/loginctl
        print(f"[ENFORCE] Beende alle Sessions für {username}")

    def notify_user(self, username, message, category="info"):
        """
        Sendet eine Desktop-Notification an alle passenden Agenten des angegebenen Nutzers (via D-Bus).
        """
        try:
            import asyncio

            from dbus_next import DBusError
            from dbus_next.aio import MessageBus

            async def send():
                bus = await MessageBus().connect()
                # Enumerate all possible agent instances (e.g., per session)
                # For simplicity, try common session paths and ignore errors
                notified = False
                for session_num in range(1, 10):
                    obj_path = (
                        f"/org/guardian/Agent{session_num}"
                        if session_num > 1
                        else "/org/guardian/Agent"
                    )
                    try:
                        proxy = await bus.introspect("org.guardian.Agent", obj_path)
                        obj = bus.get_proxy_object(
                            "org.guardian.Agent", obj_path, proxy
                        )
                        iface = obj.get_interface("org.guardian.Agent")
                        agent_username = await iface.call_get_username()
                        if agent_username == username:
                            await iface.call_notify_user(message, category)
                            print(
                                f"[NOTIFY] Nachricht an Agent {obj_path} für Nutzer {username} gesendet."
                            )
                            notified = True
                    except DBusError:
                        continue
                    except Exception as e:
                        print(f"[NOTIFY ERROR] Agent {obj_path}: {e}")
                if not notified:
                    print(f"[NOTIFY] Kein Agent für Nutzer {username} erreichbar.")

            asyncio.run(send())
        except Exception as e:
            print(f"[NOTIFY ERROR] {username}: {message} ({e})")


# Quota/Curfew enforcement
