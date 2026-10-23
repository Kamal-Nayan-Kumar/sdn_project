import time
import pytest
from app.db.repository import init_db, save_run_record, load_runs_into_registry
from app.services.proactive_monitor import ProactiveMonitor
from app.streaming.registry import registry
from app.streaming.events import EventType


def test_db_persistence_roundtrip():
    init_db()
    run_id = f"test_run_{int(time.time() * 1000)}"
    record = registry.create(run_id, "Test persistence intent", trigger="reactive")
    record.publish(EventType.RUN_STARTED, user_message="Test persistence intent")
    record.publish(EventType.RUN_COMPLETED, outcome="intent_satisfied")
    record.finish(outcome="intent_satisfied", final_state={"final_outcome": "intent_satisfied"})

    save_run_record(record)

    # Clear from in-memory registry and restore
    registry._runs.pop(run_id, None)
    assert registry.get(run_id) is None

    load_runs_into_registry(limit=10)
    restored = registry.get(run_id)
    assert restored is not None
    assert restored.outcome == "intent_satisfied"
    assert restored.terminal is True


def test_proactive_monitor_lifecycle():
    monitor = ProactiveMonitor()
    status = monitor.status()
    assert status["enabled"] is False
    assert status["consecutive_violations"] == 0

    # Start monitor
    monitor.start()
    assert monitor.status()["enabled"] is True

    # Stop monitor
    monitor.stop()
    assert monitor.status()["enabled"] is False
