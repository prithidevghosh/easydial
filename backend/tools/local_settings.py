import json
import os
from pathlib import Path

ENV = Path(__file__).resolve().parents[1] / ".env"


def save_settings(path: Path, updates: dict[str, str]):
    """Preserve unrelated settings; never print or put tokens in process arguments."""
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(updates)
    result = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in updates:
            if key in remaining:
                result.append(f"{key}={json.dumps(remaining.pop(key), ensure_ascii=False)}")
        else:
            result.append(line)
    result.extend(f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in remaining.items())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write("\n".join(result) + "\n")
