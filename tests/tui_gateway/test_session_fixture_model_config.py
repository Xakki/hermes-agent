"""The deletion fakes must expose stored onboarding metadata, not always return None."""
import json

import pytest

from agent.onboarding_record import KEY, load
from tests.tui_gateway.session_fixtures import StoredSessionDB


@pytest.mark.parametrize("record", [None, {}, {"run_id": "fixture-run", "ended_by": "skipped"}])
def test_stored_session_fixture_exercises_real_metadata_reader(record):
    config = {"unrelated": 7}
    if record is not None:
        config[KEY] = record

    class DB(StoredSessionDB):
        def get_session(self, session_id):
            return {"model_config": json.dumps(config)} if session_id == "stored" else None

    db = DB()
    assert load(db, "stored") == record
    assert db.get_session_model_config_value("stored", "unrelated") == 7
    assert db.get_session_model_config_value("missing", KEY, "absent") == "absent"
