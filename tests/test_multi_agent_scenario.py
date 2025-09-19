import os
import subprocess
import time
import signal
import pytest

AGENT_SCRIPT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../guardian_agent/guardian_agent/main.py'))

USERS = ['kid1', 'kid2', 'kid3']

class AgentProcess:
    def __init__(self, username):
        self.username = username
        self.env = os.environ.copy()
        self.env['USER'] = username
        self.proc = None
        self.obj_path = None
    def start(self):
        self.proc = subprocess.Popen([
            'python', AGENT_SCRIPT
        ], env=self.env)
        time.sleep(0.5)  # Wait for agent to start
    def stop(self):
        if self.proc:
            self.proc.send_signal(signal.SIGINT)
            self.proc.wait(timeout=2)
            self.proc = None

@pytest.fixture(scope='module')
def agents():
    procs = {}
    for user in USERS:
        procs[user] = AgentProcess(user)
    yield procs
    for agent in procs.values():
        agent.stop()

def test_multi_agent_scenario(agents):
    # kid1 login
    agents['kid1'].start()
    # kid2 login
    agents['kid2'].start()
    # kid3 login
    agents['kid3'].start()
    # Simulate quota reached for kid1
    # ... call daemon notify_user('kid1', 'Quota reached!', 'warning')
    # kid3 logout
    agents['kid3'].stop()
    # kid1 logout
    agents['kid1'].stop()
    # kid3 login
    agents['kid3'].start()
    # Simulate quota reached for kid2
    # ... call daemon notify_user('kid2', 'Quota reached!', 'warning')
    # kid2 logout
    agents['kid2'].stop()
    # Simulate quota reached for kid3
    # ... call daemon notify_user('kid3', 'Quota reached!', 'warning')
    # kid3 logout
    agents['kid3'].stop()
    # Optionally: Check logs or notification delivery
