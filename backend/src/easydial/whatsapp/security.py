"""Authenticated encryption for sensitive queue payloads and merchant browser state."""

import base64
import hashlib
import hmac
import json
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet


class SecretBox:
    def __init__(self, key):
        raw = key.encode() if isinstance(key, str) else key
        self.fernet = Fernet(raw)
        self.identity_key = hashlib.sha256(b"easydial-identity-v1:" + raw).digest()

    def derive(self, context):
        material = hmac.new(self.identity_key, context.encode(), hashlib.sha256).digest()
        return SecretBox(base64.urlsafe_b64encode(material))

    def seal(self, value):
        return self.fernet.encrypt(json.dumps(value).encode()).decode()

    def open(self, value):
        return json.loads(self.fernet.decrypt(value.encode()))

    def write(self, path, value):
        target = Path(path)
        target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(target.parent, 0o700)
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".session-")
        try:
            with os.fdopen(fd, "w") as file:
                file.write(self.seal(value))
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
