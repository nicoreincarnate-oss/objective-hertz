import sys

from openjarvis.core.events import EventBus
from openjarvis.vassals.supervisor import VassalSupervisor


def test_register_defaults_uses_current_python_executable(tmp_path):
    supervisor = VassalSupervisor(EventBus(), str(tmp_path))
    supervisor.register_defaults(str(tmp_path))

    assert supervisor._vassals["titan"].command[:2] == [sys.executable, "-m"]
    assert supervisor._vassals["hermes"].command[:2] == [sys.executable, "-m"]
    assert supervisor._vassals["clawdbot"].command[:2] == [sys.executable, "-m"]
    assert supervisor._vassals["system_executor"].command[:2] == [sys.executable, "-m"]
    assert supervisor._vassals["deerflow_research"].command[:2] == [sys.executable, "-m"]
    assert supervisor._vassals["system_executor"].env == {
        "SYSTEM_EXECUTOR_A2A": "1",
        "SYSTEM_EXECUTOR_A2A_PORT": "9010",
    }
    assert supervisor._vassals["deerflow_research"].env == {
        "DEERFLOW_RESEARCH_A2A": "1",
        "DEERFLOW_RESEARCH_A2A_PORT": "9011",
    }
