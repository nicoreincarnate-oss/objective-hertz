from openjarvis.core.events import EventType


def test_custom_event_type_remains_available_for_vassal_runtime():
    assert EventType.CUSTOM.value == "custom"
