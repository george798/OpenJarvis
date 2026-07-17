"""Obsidian / markdown vault auto-connect and startup indexing.

Reads ``memory_files.vault_path`` from config; seeds the connectors-router
instance cache (so the web UI shows the vault connected) and kicks off a
background ingestion sync so the vault contents are searchable via the
knowledge tools.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


def connect_vault(config: Any) -> Dict[str, Any]:
    """Auto-connect the Obsidian vault and index it into the knowledge store."""
    mf = getattr(config, "memory_files", None)
    vault_path = (getattr(mf, "vault_path", "") or "").strip()
    if not vault_path:
        return {}
    path = Path(vault_path).expanduser()
    if not path.is_dir():
        logger.warning("Vault connect: vault path %s does not exist", path)
        return {"vault": "missing"}

    from openjarvis.connectors.obsidian import ObsidianConnector

    connector = ObsidianConnector(vault_path=str(path))

    # Seed the API router's cache so GET /v1/connectors shows it connected.
    try:
        from openjarvis.server import connectors_router

        connectors_router._instances["obsidian"] = connector
    except Exception:
        pass

    def _index() -> None:
        try:
            from openjarvis.connectors.pipeline import IngestionPipeline
            from openjarvis.connectors.store import KnowledgeStore
            from openjarvis.connectors.sync_engine import SyncEngine

            engine = SyncEngine(pipeline=IngestionPipeline(store=KnowledgeStore()))
            engine.sync(connector)
            logger.info("Vault connect: vault indexed from %s", path)
        except Exception:
            logger.exception("Vault connect: vault indexing failed")

    threading.Thread(target=_index, daemon=True, name="vault-index").start()
    return {"vault": str(path)}


__all__ = ["connect_vault"]
