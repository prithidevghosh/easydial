import runpy
import sqlite3
from pathlib import Path

import pytest

from easydial.settings import Settings
from easydial.whatsapp.binding import bind, fingerprint

ENV = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'deploy/aws/runtime_env.py'))


def test_deployment_env_only_exports_allowlisted_credentials():
    data = {key: 'test-value' for key in ENV['REQUIRED']}
    data['CUSTOMER_SESSION_DIRECTORY'] = '/local/private/path'
    data['UNEXPECTED_SECRET'] = 'never-export'
    result = ENV['render'](data)
    assert 'never-export' not in result
    assert '/local/private/path' not in result
    with pytest.raises(ValueError):
        ENV['render']({})
    data['OPENAI_API_KEY'] = 'line1\nline2'
    with pytest.raises(ValueError):
        ENV['render'](data)


def test_multiuser_binding_migrates_without_weakening_account_or_key_checks():
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE binding(id INTEGER PRIMARY KEY, fingerprint TEXT)')
    settings = Settings(_env_file=None, whatsapp_multiuser=True, whatsapp_phone_number_id='12345',
                        customer_session_key='test-key', blinkit_browser_profile='/old/mac/profile')
    db.execute('INSERT INTO binding VALUES (1,?)', (fingerprint(settings, legacy=True),))
    bind(db, settings)
    cloud = settings.model_copy(update={'blinkit_browser_profile': '', 'customer_session_directory': '/data/customers'})
    bind(db, cloud)
    for changes in ({'customer_session_key': 'another-key'}, {'whatsapp_phone_number_id': '99999'}):
        with pytest.raises(RuntimeError):
            bind(db, cloud.model_copy(update=changes))
    db.close()


DEPLOY = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'deploy/aws/deploy.py'))


def test_release_waits_for_remote_success(monkeypatch):
    monkeypatch.setattr(DEPLOY['time'], 'sleep', lambda _: None)
    responses = iter([
        {'CommandInvocations': []},
        {'CommandInvocations': [{'Status': 'InProgress'}]},
        {'CommandInvocations': [{'Status': 'Success'}]},
    ])
    DEPLOY['wait_for_command'](lambda *args: next(responses), 'command', 'instance')
    with pytest.raises(StopIteration):
        next(responses)


@pytest.mark.parametrize('status', ['Failed', 'Cancelled', 'TimedOut'])
def test_release_propagates_remote_failure(status):
    with pytest.raises(RuntimeError, match=status):
        DEPLOY['wait_for_command'](
            lambda *args: {'CommandInvocations': [{'Status': status}]}, 'command', 'instance'
        )


def test_public_health_rejects_unconfigured_service(monkeypatch):
    import io
    monkeypatch.setattr(DEPLOY['time'], 'sleep', lambda _: None)
    monkeypatch.setattr(DEPLOY['urllib'].request, 'urlopen',
                        lambda *args, **kwargs: io.BytesIO(b'{"status":"setup_required"}'))
    with pytest.raises(RuntimeError, match='public HTTPS health failed'):
        DEPLOY['verify_public_health']('https://example.cloudfront.net')


def test_proxy_credentials_export_real_secret_and_reach_runtime():
    import json

    settings = Settings(_env_file=None, blinkit_proxy_username='test-in-rotate',
                        blinkit_proxy_password='private-proxy-password')
    data = DEPLOY['runtime_credentials'](settings, ENV['ALLOWED'])
    data.update({key: 'required-test-value' for key in ENV['REQUIRED']})
    serialized = json.loads(json.dumps(data))
    rendered = ENV['render'](serialized)
    assert "BLINKIT_PROXY_USERNAME='test-in-rotate'" in rendered
    assert "BLINKIT_PROXY_PASSWORD='private-proxy-password'" in rendered
    assert '**********' not in rendered


@pytest.mark.parametrize("with_paths", [True, False])
def test_existing_multiuser_database_survives_tester_list_changes(with_paths):
    import hashlib
    import json

    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE binding(id INTEGER PRIMARY KEY, fingerprint TEXT)")
    config = Settings(_env_file=None, whatsapp_multiuser=True, whatsapp_phone_number_id="12345",
                      customer_session_key="test-key", whatsapp_allowed_sender_2="919000000002")
    previous = ["12345", "919000000002"]
    if with_paths:
        previous += ["", "", ""]
    previous += ["multiuser-v1", hashlib.sha256(b"test-key").hexdigest()]
    db.execute("INSERT INTO binding VALUES (1,?)", (hashlib.sha256(json.dumps(previous).encode()).hexdigest(),))
    bind(db, config)
    bind(db, config.model_copy(update={"whatsapp_allowed_sender_2": "919000000003"}))
    db.close()


def test_numbered_senders_reach_aws_runtime():
    slots = {f"WHATSAPP_ALLOWED_SENDER_{i}": f"91900000000{i}" for i in range(1, 6)}
    settings = Settings(_env_file=None, **{key.lower(): value for key, value in slots.items()})
    data = DEPLOY["runtime_credentials"](settings, ENV["ALLOWED"])
    data.update(dict.fromkeys(ENV["REQUIRED"], "test-value"))
    rendered = ENV["render"](data)
    for key, value in slots.items():
        assert f"{key}='{value}'" in rendered
