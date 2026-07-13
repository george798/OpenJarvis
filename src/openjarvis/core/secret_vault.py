"""Encrypted secret vault shared by the CLI, the server, and agent tools.

Secrets live in ``~/.openjarvis/vault.enc`` (Fernet-encrypted JSON) with the
key at ``~/.openjarvis/.vault_key`` — the same files used by ``jarvis vault``.
Each secret is also exported as an environment variable so tools like
``http_request`` can reference it via ``$VAR`` header expansion without the
plaintext ever passing through the model context.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from openjarvis.core.config import DEFAULT_CONFIG_DIR

_VAULT_FILE = DEFAULT_CONFIG_DIR / "vault.enc"
_VAULT_KEY_FILE = DEFAULT_CONFIG_DIR / ".vault_key"
_LOCK = threading.Lock()


def env_var_name(key: str) -> str:
    """Normalise a secret key to its exported environment variable name."""
    return re.sub(r"[^A-Za-z0-9]+", "_", key).strip("_").upper()


def _get_or_create_key() -> bytes:
    from cryptography.fernet import Fernet

    if _VAULT_KEY_FILE.exists():
        return _VAULT_KEY_FILE.read_bytes().strip()
    key = Fernet.generate_key()
    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _VAULT_KEY_FILE.write_bytes(key)
    _VAULT_KEY_FILE.chmod(0o600)
    return key


def load_vault() -> dict[str, str]:
    """Load and decrypt the vault. Returns {} when missing or unreadable."""
    if not _VAULT_FILE.exists():
        return {}
    try:
        from cryptography.fernet import Fernet

        f = Fernet(_get_or_create_key())
        return json.loads(f.decrypt(_VAULT_FILE.read_bytes()).decode())
    except Exception:
        return {}


def save_vault(data: dict[str, str]) -> None:
    """Encrypt and persist the vault with owner-only permissions."""
    from cryptography.fernet import Fernet

    f = Fernet(_get_or_create_key())
    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _VAULT_FILE.write_bytes(f.encrypt(json.dumps(data).encode()))
    _VAULT_FILE.chmod(0o600)


def set_secret(key: str, value: str) -> str:
    """Store a secret and export it to the environment. Returns the env var."""
    import os

    with _LOCK:
        data = load_vault()
        data[key] = value
        save_vault(data)
    var = env_var_name(key)
    os.environ[var] = value
    return var


def delete_secret(key: str) -> bool:
    """Remove a secret from the vault and the environment."""
    import os

    with _LOCK:
        data = load_vault()
        if key not in data:
            return False
        del data[key]
        save_vault(data)
    os.environ.pop(env_var_name(key), None)
    return True


def inject_vault_into_environ() -> int:
    """Export every vault secret as an env var. Call at server startup.

    Returns the number of secrets injected. Existing environment values
    are not overwritten (docker-compose / .env wins).
    """
    import os

    count = 0
    for key, value in load_vault().items():
        var = env_var_name(key)
        existing = os.environ.get(var)
        if existing is None or existing == "":
            os.environ[var] = value
            count += 1
    return count


__all__ = [
    "env_var_name",
    "load_vault",
    "save_vault",
    "set_secret",
    "delete_secret",
    "inject_vault_into_environ",
]
