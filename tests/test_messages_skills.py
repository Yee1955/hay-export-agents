import pytest

from hayagents import people
from hayagents.errors import Denied
from conftest import proc


def test_agents_on_linked_tasks_can_talk(planned):
    r = proc(planned, "send_message", to_agent="production_agent", body="100 t arriving Bowmans 3 Dec")
    assert r["sent"]


def test_agent_not_on_task_cannot_be_messaged(planned):
    with pytest.raises(Denied, match="not on this task"):
        proc(planned, "send_message", to_agent="finance_agent", body="approve this please")


def test_skill_has_version_and_owner(planned):
    s = proc(planned, "get_skill", name="pick-a-grower")
    assert (s["version"], s["owner"]) == ("1.0.0", "procurement_manager")


def test_skill_suggestion_only_after_approved_action_and_owner_decides(planned):
    with pytest.raises(Denied, match="after the task"):
        proc(planned, "suggest_skill_edit", skill="pick-a-grower", body="also check fumigation")
    planned.execute("UPDATE tasks SET status='po_created' WHERE id='G-001-T1'")
    proc(planned, "suggest_skill_edit", skill="pick-a-grower", body="also check fumigation")
    with pytest.raises(Denied, match="owner"):
        people.decide_skill_suggestion(planned, 1, "procurement_agent", True)
    people.decide_skill_suggestion(planned, 1, "procurement_manager", True)
