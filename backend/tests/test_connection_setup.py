import importlib.util
import stat
from pathlib import Path

from dotenv import dotenv_values

spec = importlib.util.spec_from_file_location("setup", Path(__file__).parents[1] / "tools/local_settings.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def test_local_settings_preserve_existing_keys_and_private_permissions(tmp_path):
    path = tmp_path / ".env"
    path.write_text('OPENAI_API_KEY="test-not-a-secret"\nCOMMERCE_MODE=live\n')
    setup.save_settings(
        path, {"BLINKIT_BROWSER_PROFILE": "test-profile", "LIVE_ADDRESS_LABEL": 'Test "home"'}
    )
    result = dotenv_values(path)
    assert result["OPENAI_API_KEY"] == "test-not-a-secret"
    assert result["LIVE_ADDRESS_LABEL"] == 'Test "home"'
    assert result["COMMERCE_MODE"] == "live"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
