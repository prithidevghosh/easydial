import importlib.util
import stat
from pathlib import Path

from dotenv import dotenv_values

spec = importlib.util.spec_from_file_location("setup", Path(__file__).parents[1] / "tools/connect_swiggy.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def test_callback_rejects_wrong_state_and_duplicate_query_values():
    assert setup.parse_callback("/callback?state=good&code=one", "good") == "one"
    for path in [
        "/callback?state=bad&code=one",
        "/callback?state=good&code=one&code=two",
        "/other?state=good&code=one",
        "/callback?state=good",
    ]:
        assert setup.parse_callback(path, "good") is None


def test_local_settings_preserve_existing_keys_and_private_permissions(tmp_path):
    path = tmp_path / ".env"
    path.write_text('OPENAI_API_KEY="test-not-a-secret"\nCOMMERCE_MODE=live\n')
    setup.save_settings(path, {"SWIGGY_ACCESS_TOKEN": "test-token", "LIVE_ADDRESS_LABEL": 'Test "home"'})
    result = dotenv_values(path)
    assert result["OPENAI_API_KEY"] == "test-not-a-secret"
    assert result["LIVE_ADDRESS_LABEL"] == 'Test "home"'
    assert result["COMMERCE_MODE"] == "live"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
