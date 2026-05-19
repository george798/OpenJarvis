"""Tests for SlackConnector — OAuth-authenticated Slack channel message sync.

All Slack API calls are mocked; no network access is required.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest

from openjarvis.connectors._stubs import Document
from openjarvis.core.registry import ConnectorRegistry

# ---------------------------------------------------------------------------
# Fake API payloads
# ---------------------------------------------------------------------------

_CHANNELS_RESPONSE = {
    "channels": [
        {"id": "C001", "name": "general", "is_member": True},
        {"id": "C002", "name": "engineering", "is_member": True},
    ],
    "response_metadata": {"next_cursor": ""},
}

_HISTORY_RESPONSE = {
    "messages": [
        {
            "ts": "1710500000.000100",
            "user": "U001",
            "text": "Let's discuss the API redesign.",
            "thread_ts": "1710500000.000100",
        },
        {
            "ts": "1710500060.000200",
            "user": "U002",
            "text": "Sounds good, I'll prepare a doc.",
        },
    ],
    "has_more": False,
}

_USERS_RESPONSE = {
    "members": [
        {"id": "U001", "real_name": "Alice", "profile": {"email": "alice@co.com"}},
        {"id": "U002", "real_name": "Bob", "profile": {"email": "bob@co.com"}},
    ],
}

_AUTH_TEST_RESPONSE = {
    "ok": True,
    "team_id": "T0ACME",
    "team": "Acme",
    "url": "https://acme.slack.com/",
    "user": "bot",
    "user_id": "UBOT",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def connector(tmp_path: Path):
    """SlackConnector pointing at a tmp credentials path (no file yet)."""
    from openjarvis.connectors.slack_connector import SlackConnector  # noqa: PLC0415

    creds_path = str(tmp_path / "slack.json")
    return SlackConnector(credentials_path=creds_path)


# ---------------------------------------------------------------------------
# Test 1 — not connected without a credentials file
# ---------------------------------------------------------------------------


def test_not_connected_without_credentials(connector) -> None:
    """is_connected() returns False when no credentials file exists."""
    assert connector.is_connected() is False


# ---------------------------------------------------------------------------
# Test 2 — auth_type is "oauth"
# ---------------------------------------------------------------------------


def test_auth_type_is_oauth(connector) -> None:
    """SlackConnector.auth_type must be 'oauth'."""
    assert connector.auth_type == "oauth"


# ---------------------------------------------------------------------------
# Test 3 — auth_url contains "slack.com"
# ---------------------------------------------------------------------------


def test_auth_url(connector) -> None:
    """auth_url() returns a URL pointing to Slack's OAuth endpoint."""
    url = connector.auth_url()
    assert isinstance(url, str)
    assert "slack.com" in url
    assert "channels:history" in url or "channels%3Ahistory" in url


# ---------------------------------------------------------------------------
# Test 4 — sync yields documents with correct fields (mocked API)
# ---------------------------------------------------------------------------


@patch("openjarvis.connectors.slack_connector._slack_api_auth_test")
@patch("openjarvis.connectors.slack_connector._slack_api_conversations_list")
@patch("openjarvis.connectors.slack_connector._slack_api_conversations_history")
@patch("openjarvis.connectors.slack_connector._slack_api_users_list")
def test_sync_yields_documents(
    mock_users,
    mock_history,
    mock_channels,
    mock_auth,
    connector,
    tmp_path: Path,
) -> None:
    """sync() yields one Document per message with correct metadata.

    With 2 channels each having 2 messages, we expect exactly 4 documents.
    """
    # Set up fake credentials so is_connected() returns True
    creds_path = Path(connector._credentials_path)
    creds_path.write_text(json.dumps({"token": "fake-access-token"}), encoding="utf-8")

    # Configure mocks
    mock_auth.return_value = _AUTH_TEST_RESPONSE
    mock_users.return_value = _USERS_RESPONSE
    mock_channels.return_value = _CHANNELS_RESPONSE
    mock_history.return_value = _HISTORY_RESPONSE

    docs: List[Document] = list(connector.sync())

    # 2 channels × 2 messages = 4 documents
    assert len(docs) == 4

    # Verify all docs have correct source and doc_type
    for doc in docs:
        assert doc.source == "slack"
        assert doc.doc_type == "message"

    # Check a specific document from #general — doc_id encodes the
    # workspace subdomain so research_loop can rebuild the permalink.
    doc_c001 = next(
        (d for d in docs if d.doc_id == "slack:acme:C001:1710500000.000100"),
        None,
    )
    assert doc_c001 is not None
    assert doc_c001.title == "#general"
    assert doc_c001.author == "alice@co.com"
    assert doc_c001.content == "Let's discuss the API redesign."
    assert doc_c001.thread_id == "1710500000.000100"
    # v1 schema fields
    assert doc_c001.participants == ["alice@co.com"]
    assert doc_c001.participants_raw == ["U001"]
    assert doc_c001.channel == "general"
    assert doc_c001.url == (
        "https://acme.slack.com/archives/C001/p1710500000000100"
    )
    assert doc_c001.metadata["channel_id"] == "C001"
    assert doc_c001.metadata["channel_name"] == "general"
    assert doc_c001.metadata["team_id"] == "T0ACME"
    assert doc_c001.metadata["team_domain"] == "acme"

    # Check a specific document from #engineering
    doc_c002 = next(
        (d for d in docs if d.doc_id == "slack:acme:C002:1710500060.000200"),
        None,
    )
    assert doc_c002 is not None
    assert doc_c002.title == "#engineering"
    assert doc_c002.author == "bob@co.com"
    assert doc_c002.content == "Sounds good, I'll prepare a doc."
    assert doc_c002.thread_id is None
    assert doc_c002.channel == "engineering"

    # Verify the API was called correctly
    mock_auth.assert_called_once()
    mock_users.assert_called_once()
    assert mock_channels.call_count == 1
    # conversations.history called once per channel (2 channels)
    assert mock_history.call_count == 2


# ---------------------------------------------------------------------------
# Test — conversations.list is called with every conversation type so
# DMs and group DMs auto-sync alongside public/private channels.
# ---------------------------------------------------------------------------


@patch("openjarvis.connectors.slack_connector._slack_api_with_retry")
def test_conversations_list_requests_all_conversation_types(mock_retry) -> None:
    """``_slack_api_conversations_list`` widens ``types`` to cover IMs + MPIMs.

    A user connecting Slack expects ``everything I can see is searchable``
    without a per-channel opt-in — same as Gmail. The proxy for that here
    is the API request shape: ``types`` must include im + mpim so the bot
    token's DMs and group DMs come back in the listing.
    """
    from openjarvis.connectors.slack_connector import (  # noqa: PLC0415
        _slack_api_conversations_list,
    )

    mock_retry.return_value = {"channels": [], "response_metadata": {}}
    _slack_api_conversations_list("fake-token")

    method, _token, params = mock_retry.call_args.args[:3]
    assert method == "conversations.list"
    types = params["types"].split(",")
    assert set(types) == {"public_channel", "private_channel", "mpim", "im"}


# ---------------------------------------------------------------------------
# Test — sync() yields documents for DMs (im) and group DMs (mpim) too,
# without requiring conversations.join (no join concept on those types).
# ---------------------------------------------------------------------------


@patch("openjarvis.connectors.slack_connector._slack_api_with_retry")
@patch("openjarvis.connectors.slack_connector._slack_api_auth_test")
@patch("openjarvis.connectors.slack_connector._slack_api_conversations_list")
@patch("openjarvis.connectors.slack_connector._slack_api_conversations_history")
@patch("openjarvis.connectors.slack_connector._slack_api_users_list")
def test_sync_includes_dms_and_group_dms(
    mock_users,
    mock_history,
    mock_channels,
    mock_auth,
    mock_retry,
    connector,
    tmp_path: Path,
) -> None:
    """IMs and MPIMs are synced without a join step and get sensible labels.

    The fake ``conversations.list`` returns one public channel, one IM
    (``is_im`` with the peer's ``user`` field), and one group DM
    (``is_mpim``). All three yield documents. No ``conversations.join``
    call is made for IM/MPIM because there's no join concept for them
    on Slack's API.
    """
    creds_path = Path(connector._credentials_path)
    creds_path.write_text(json.dumps({"token": "fake-token"}), encoding="utf-8")

    mock_auth.return_value = _AUTH_TEST_RESPONSE
    mock_users.return_value = _USERS_RESPONSE
    mock_channels.return_value = {
        "channels": [
            {
                "id": "C001",
                "name": "general",
                "is_member": True,
            },
            {
                "id": "D001",
                "is_im": True,
                "user": "U001",  # 1:1 DM with Alice
            },
            {
                "id": "G001",
                "name": "mpdm-alice--bob-1",
                "is_mpim": True,
            },
        ],
        "response_metadata": {"next_cursor": ""},
    }
    mock_history.return_value = {
        "messages": [
            {
                "ts": "1710500000.000100",
                "user": "U001",
                "text": "context-specific message",
            },
        ],
        "has_more": False,
    }
    # _slack_api_with_retry is used for join + any unmocked endpoints.
    # If sync ever tries to join an IM/MPIM, this records it as a call.
    mock_retry.return_value = {"ok": False}

    docs: List[Document] = list(connector.sync())

    # One message per conversation × 3 conversations = 3 documents.
    assert len(docs) == 3

    by_chan_id = {d.metadata["channel_id"]: d for d in docs}

    public_doc = by_chan_id["C001"]
    assert public_doc.title == "#general"
    assert public_doc.channel == "general"
    assert public_doc.metadata["channel_type"] == "public_channel"

    im_doc = by_chan_id["D001"]
    assert im_doc.title == "DM with Alice"
    assert im_doc.channel == "dm-Alice"
    assert im_doc.metadata["channel_type"] == "im"

    mpim_doc = by_chan_id["G001"]
    assert mpim_doc.title == "#mpdm-alice--bob-1"
    assert mpim_doc.channel == "mpdm-alice--bob-1"
    assert mpim_doc.metadata["channel_type"] == "mpim"

    # Critically, no join was attempted for IM/MPIM (the helper is only
    # invoked via this mock for `conversations.join`).
    join_calls = [
        c
        for c in mock_retry.call_args_list
        if c.args and c.args[0] == "conversations.join"
    ]
    assert join_calls == []


# ---------------------------------------------------------------------------
# Test 5 — disconnect removes the credentials file
# ---------------------------------------------------------------------------


def test_disconnect(connector, tmp_path: Path) -> None:
    """disconnect() deletes the credentials file."""
    creds_path = Path(connector._credentials_path)
    creds_path.write_text(json.dumps({"token": "fake-access-token"}), encoding="utf-8")
    assert connector.is_connected() is True

    connector.disconnect()

    assert not creds_path.exists()
    assert connector.is_connected() is False


# ---------------------------------------------------------------------------
# Test 6 — mcp_tools returns the three expected tool specs
# ---------------------------------------------------------------------------


def test_mcp_tools(connector) -> None:
    """mcp_tools() returns exactly 3 tools with the required names."""
    tools = connector.mcp_tools()
    names = {t.name for t in tools}
    assert len(tools) == 3
    assert "slack_search_messages" in names
    assert "slack_get_thread" in names
    assert "slack_list_channels" in names


# ---------------------------------------------------------------------------
# Test 7 — ConnectorRegistry contains "slack" after import
# ---------------------------------------------------------------------------


def test_registry() -> None:
    """SlackConnector can be registered and retrieved via ConnectorRegistry."""
    from openjarvis.connectors.slack_connector import SlackConnector  # noqa: PLC0415

    # The registry is cleared before each test by the autouse conftest fixture,
    # so we imperatively re-register here (same pattern as test_gmail.py).
    ConnectorRegistry.register_value("slack", SlackConnector)
    assert ConnectorRegistry.contains("slack")
    cls = ConnectorRegistry.get("slack")
    assert cls.connector_id == "slack"


# ---------------------------------------------------------------------------
# Test 8 — end-to-end: connector → pipeline → KnowledgeStore → HybridSearch
# ---------------------------------------------------------------------------


@patch("openjarvis.connectors.slack_connector._slack_api_auth_test")
@patch("openjarvis.connectors.slack_connector._slack_api_conversations_list")
@patch("openjarvis.connectors.slack_connector._slack_api_conversations_history")
@patch("openjarvis.connectors.slack_connector._slack_api_users_list")
def test_end_to_end_ingest_and_search(
    mock_users,
    mock_history,
    mock_channels,
    mock_auth,
    connector,
    tmp_path: Path,
) -> None:
    """Synced Slack messages are searchable via HybridSearch with v1 fields.

    Lexical-only path (no embedder) so this stays a pure unit test — no
    Ollama daemon needed. Confirms the v1 contract end-to-end: source,
    namespaced thread_id, channel, participants, and a workspace-qualified
    permalink all survive ingest → store → hit.
    """
    from openjarvis.connectors.hybrid_search import HybridSearch  # noqa: PLC0415
    from openjarvis.connectors.pipeline import IngestionPipeline  # noqa: PLC0415
    from openjarvis.connectors.store import KnowledgeStore  # noqa: PLC0415

    creds_path = Path(connector._credentials_path)
    creds_path.write_text(json.dumps({"token": "fake-token"}), encoding="utf-8")
    mock_auth.return_value = _AUTH_TEST_RESPONSE
    mock_users.return_value = _USERS_RESPONSE
    mock_channels.return_value = _CHANNELS_RESPONSE
    mock_history.return_value = _HISTORY_RESPONSE

    store = KnowledgeStore(db_path=tmp_path / "slack_e2e.db")
    pipeline = IngestionPipeline(store)
    chunks_stored = pipeline.ingest(connector.sync())

    # 4 short messages → 4 chunks (no chunk splitting at this length).
    assert chunks_stored == 4

    hybrid = HybridSearch(store)
    hits = hybrid.search("API redesign", limit=5)
    assert len(hits) >= 1

    target = next(
        (h for h in hits if "API redesign" in h.content_snippet),
        None,
    )
    assert target is not None
    assert target.source == "slack"
    assert target.title == "#general"
    # Thread id is namespaced by the pipeline.
    assert target.thread_id == "slack:1710500000.000100"
    assert target.participants == ["alice@co.com"]
    # The stored doc_id flows through the hit and is the format the
    # research-loop URL builder expects.
    assert target.document_id == "slack:acme:C001:1710500000.000100"

    # And the research-loop builder reconstructs the workspace permalink.
    from openjarvis.agents.research_loop import _hit_url  # noqa: PLC0415

    assert _hit_url(target.source, target.document_id) == (
        "https://acme.slack.com/archives/C001/p1710500000000100"
    )
