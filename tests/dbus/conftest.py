"""Fixtures for the L2 D-Bus contract test layer.

These tests run guardian's real dbus-next client code against a *real* D-Bus
broker (dbus-daemon) with mock ``org.freedesktop.login1`` (logind) and
``org.freedesktop.Notifications`` services implemented with dbus-next. Unlike the
hand-rolled ``mock_dbus`` MagicMock fixture in guardian_daemon/tests, this
exercises real signal delivery, real introspection-driven property getters and
the real (uid, object_path) ``User`` struct - i.e. the contract details where
"green tests, broken reality" bugs hide.

Why dbus-next instead of python-dbusmock: guardian subscribes to logind signals
via ``manager.on_session_new(...)``, which dbus-next generates from the object's
introspection. dbusmock does not add signals to introspection (it only injects
methods/properties), so on_session_new would be missing. Serving the mock with
dbus-next gives correct signal introspection and keeps everything in one
interpreter (the venv), with no native build dependencies.
"""

import asyncio
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from dbus_next import Variant
from dbus_next.aio import MessageBus
from dbus_next.constants import BusType, PropertyAccess
from dbus_next.service import ServiceInterface, dbus_property, method, signal

# Permissive session-style bus config; we use it for both the "system" and
# "session" buses in tests (a bus is a bus - system vs session is only which env
# var clients read). This avoids the restrictive default system.conf policy.
_BUS_CONF = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN" "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:path={sock}</listen>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""


def _start_bus(tmp: Path, name: str):
    """Start a private dbus-daemon and return (proc, address)."""
    sock = tmp / f"{name}_socket"
    conf = tmp / f"{name}.conf"
    conf.write_text(_BUS_CONF.format(sock=sock))
    proc = subprocess.Popen(
        ["dbus-daemon", "--config-file", str(conf), "--nofork"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(100):
        if sock.exists():
            break
        time.sleep(0.05)
    else:
        proc.kill()
        raise RuntimeError(f"dbus-daemon ({name}) did not create its socket")
    return proc, f"unix:path={sock}"


# --------------------------------------------------------------------------- #
# Mock D-Bus services (dbus-next service side)
# --------------------------------------------------------------------------- #


class LoginSession(ServiceInterface):
    """Mock ``org.freedesktop.login1.Session`` object.

    Property shapes mirror real logind (and dbusmock's logind template): ``User``
    is a ``(uid, object_path)`` struct, plus ``Class``/``Service``/``Desktop``/
    ``Type``/``LockedHint``. ``Desktop`` is deliberately included because real
    logind exposes it while dbusmock's template omits it - the kind of field that
    naive MagicMocks silently paper over.
    """

    def __init__(self, uid, username, seat, klass, service, desktop, sess_type, locked):
        super().__init__("org.freedesktop.login1.Session")
        self._uid = uid
        self._name = username
        self._seat = seat
        self._class = klass
        self._service = service
        self._desktop = desktop
        self._type = sess_type
        self._locked = locked
        self._user_path = f"/org/freedesktop/login1/user/{uid}"

    @dbus_property(access=PropertyAccess.READ)
    def Name(self) -> "s":  # noqa: F821
        return self._name

    @dbus_property(access=PropertyAccess.READ)
    def User(self) -> "(uo)":  # noqa: F821
        return [self._uid, self._user_path]

    @dbus_property(access=PropertyAccess.READ)
    def Class(self) -> "s":  # noqa: F821
        return self._class

    @dbus_property(access=PropertyAccess.READ)
    def Service(self) -> "s":  # noqa: F821
        return self._service

    @dbus_property(access=PropertyAccess.READ)
    def Desktop(self) -> "s":  # noqa: F821
        return self._desktop

    @dbus_property(access=PropertyAccess.READ)
    def Type(self) -> "s":  # noqa: F821
        return self._type

    @dbus_property(access=PropertyAccess.READ)
    def Seat(self) -> "(so)":  # noqa: F821
        return [self._seat, f"/org/freedesktop/login1/seat/{self._seat}"]

    @dbus_property(access=PropertyAccess.READWRITE)
    def LockedHint(self) -> "b":  # noqa: F821
        return self._locked

    @LockedHint.setter
    def LockedHint(self, value: "b"):  # noqa: F821
        self._locked = value

    @signal()
    def Lock(self):
        pass

    @signal()
    def Unlock(self):
        pass


class LoginManager(ServiceInterface):
    """Mock ``org.freedesktop.login1.Manager`` with control helpers.

    ``ListSessions`` returns the real ``a(susso)`` 5-tuple shape
    ``(session_id, uid, username, seat, object_path)``. ``SessionNew`` /
    ``SessionRemoved`` are declared as real signals so they appear in
    introspection and guardian's ``manager.on_session_new(...)`` works.
    """

    def __init__(self, bus):
        super().__init__("org.freedesktop.login1.Manager")
        self._bus = bus
        self._sessions = {}  # session_id -> dict(uid, name, seat, path, obj)
        self.terminated_sessions = []
        self.terminated_users = []

    @method()
    def ListSessions(self) -> "a(susso)":  # noqa: F821
        return [
            [sid, d["uid"], d["name"], d["seat"], d["path"]]
            for sid, d in self._sessions.items()
        ]

    @method()
    def GetSession(self, session_id: "s") -> "o":  # noqa: F821
        return f"/org/freedesktop/login1/session/{session_id}"

    @method()
    def GetUser(self, uid: "u") -> "o":  # noqa: F821
        return f"/org/freedesktop/login1/user/{uid}"

    @method()
    def TerminateSession(self, session_id: "s"):  # noqa: F821
        self.terminated_sessions.append(session_id)

    @method()
    def TerminateUser(self, uid: "u"):  # noqa: F821
        self.terminated_users.append(uid)

    @signal()
    def SessionNew(self, session_id, path) -> "so":  # noqa: F821
        return [session_id, path]

    @signal()
    def SessionRemoved(self, session_id, path) -> "so":  # noqa: F821
        return [session_id, path]

    # ---- control helpers (plain methods, not exported over D-Bus) ----

    def add_session(
        self,
        session_id,
        uid,
        username,
        seat="seat0",
        klass="user",
        service="sddm",
        desktop="KDE",
        sess_type="wayland",
        locked=False,
        emit=True,
    ):
        path = f"/org/freedesktop/login1/session/{session_id}"
        sess = LoginSession(
            uid, username, seat, klass, service, desktop, sess_type, locked
        )
        self._bus.export(path, sess)
        self._sessions[session_id] = {
            "uid": uid,
            "name": username,
            "seat": seat,
            "path": path,
            "obj": sess,
        }
        if emit:
            self.SessionNew(session_id, path)
        return path

    def remove_session(self, session_id, emit=True):
        d = self._sessions.pop(session_id, None)
        if not d:
            return
        try:
            self._bus.unexport(d["path"])
        except Exception:
            pass
        if emit:
            self.SessionRemoved(session_id, d["path"])


class NotificationsService(ServiceInterface):
    """Mock ``org.freedesktop.Notifications`` capturing notify-send deliveries."""

    def __init__(self):
        super().__init__("org.freedesktop.Notifications")
        self.received = []
        self._next_id = 1

    @method()
    def Notify(
        self,
        app_name: "s",  # noqa: F821
        replaces_id: "u",  # noqa: F821
        app_icon: "s",  # noqa: F821
        summary: "s",  # noqa: F821
        body: "s",  # noqa: F821
        actions: "as",  # noqa: F821, F722
        hints: "a{sv}",  # noqa: F821, F722
        expire_timeout: "i",  # noqa: F821
    ) -> "u":  # noqa: F821
        self.received.append(
            {
                "app_name": app_name,
                "app_icon": app_icon,
                "summary": summary,
                "body": body,
                "hints": {k: v.value for k, v in hints.items()},
                "expire_timeout": expire_timeout,
            }
        )
        nid = self._next_id
        self._next_id += 1
        return nid

    @method()
    def GetCapabilities(self) -> "as":  # noqa: F821, F722
        return ["body", "actions", "icon-static"]

    @method()
    def CloseNotification(self, nid: "u"):  # noqa: F821
        pass

    @method()
    def GetServerInformation(self) -> "ssss":  # noqa: F821
        return ["guardian-mock", "guardian", "1.0", "1.2"]


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def dbus_broker():
    """Start private system + session D-Bus brokers and point the env at them.

    Because dbus-next resolves BusType.SYSTEM/SESSION from
    DBUS_SYSTEM_BUS_ADDRESS / DBUS_SESSION_BUS_ADDRESS, setting these makes all of
    guardian's ``MessageBus(bus_type=...)`` calls connect to the mock buses with
    no production-code changes.
    """
    tmp = Path(tempfile.mkdtemp(prefix="guardian_dbus_"))
    procs = []
    saved = {
        k: os.environ.get(k)
        for k in ("DBUS_SYSTEM_BUS_ADDRESS", "DBUS_SESSION_BUS_ADDRESS")
    }
    try:
        sysproc, sysaddr = _start_bus(tmp, "system")
        procs.append(sysproc)
        sesproc, sesaddr = _start_bus(tmp, "session")
        procs.append(sesproc)
        os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = sysaddr
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = sesaddr
        yield {"system": sysaddr, "session": sesaddr}
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=3)
            except Exception:
                p.kill()
        shutil.rmtree(tmp, ignore_errors=True)


@pytest_asyncio.fixture
async def mock_logind(dbus_broker):
    """Serve a mock logind on the system bus; yields the LoginManager controller."""
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    manager = LoginManager(bus)
    bus.export("/org/freedesktop/login1", manager)
    await bus.request_name("org.freedesktop.login1")
    try:
        yield manager
    finally:
        bus.disconnect()


@pytest_asyncio.fixture
async def mock_notifications(dbus_broker):
    """Serve a mock org.freedesktop.Notifications on the session bus."""
    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    svc = NotificationsService()
    bus.export("/org/freedesktop/Notifications", svc)
    await bus.request_name("org.freedesktop.Notifications")
    try:
        yield svc
    finally:
        bus.disconnect()


@pytest.fixture
def make_tracker(dbus_broker):
    """Factory building a real SessionTracker wired to the mock buses.

    ``user_manager.setup_user_login`` is stubbed to True so handle_login does not
    attempt real PAM/systemd/user setup - this layer validates D-Bus session
    tracking, not host mutation.
    """
    from types import SimpleNamespace

    from guardian_daemon.policy import Policy
    from guardian_daemon.sessions import SessionTracker

    tmpdirs = []

    def _make(users=None, defaults=None):
        users = users if users is not None else {"kid1": {}}
        defaults = defaults or {
            "daily_quota_minutes": 60,
            "grace_minutes": 5,
            "bonus_pool_minutes": 0,
            "curfew": {
                "weekdays": "08:00-20:00",
                "saturday": "09:00-22:00",
                "sunday": "09:00-20:00",
            },
        }
        tmp = tempfile.mkdtemp(prefix="guardian_tracker_")
        tmpdirs.append(tmp)
        db_path = os.path.join(tmp, "guardian.sqlite")
        config = {
            "users": users,
            "defaults": defaults,
            "db_path": db_path,
            "reset_time": "03:00",
            "timezone": "Europe/Berlin",
            "notifications": {
                "pre_quota_minutes": [15, 10, 5],
                "grace_period": {"enabled": True, "duration": 10, "interval": 1},
            },
        }
        config_path = os.path.join(tmp, "config.yaml")
        with open(config_path, "w") as f:
            yaml.dump(config, f)
        policy = Policy(config_path)
        user_manager = SimpleNamespace(setup_user_login=lambda username: True)
        return SessionTracker(policy, {"db_path": db_path}, user_manager)

    try:
        yield _make
    finally:
        for tmp in tmpdirs:
            shutil.rmtree(tmp, ignore_errors=True)


@pytest_asyncio.fixture
async def running_tracker():
    """Helper to start ``tracker.run()`` as a task and guarantee cancellation.

    Usage::

        tracker = make_tracker(...)
        await running_tracker(tracker)
        ...  # assert on tracker.active_sessions
    """
    tasks = []

    async def _start(tracker, settle=0.4):
        task = asyncio.create_task(tracker.run())
        tasks.append(task)
        await asyncio.sleep(settle)  # let run() do its initial scan + subscribe
        return task

    try:
        yield _start
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


# Expose Variant for tests that need to craft signal payloads.
__all__ = ["Variant"]
