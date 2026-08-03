from __future__ import annotations

from httpx import AsyncClient

USER = "test-user"


async def invoke(client: AsyncClient, tool_name: str, **arguments) -> dict:
    response = await client.post(
        "/v1/tools/invoke",
        json={"tool": tool_name, "user_id": USER, "arguments": arguments},
    )
    assert response.status_code == 200
    return response.json()


async def test_get_current_time(client: AsyncClient) -> None:
    body = await invoke(client, "get_current_time", timezone="Asia/Kolkata")

    assert body["ok"] is True
    assert "Asia/Kolkata" in body["summary"]
    assert body["data"]["utc_offset"] == "+0530"


async def test_web_search_status_is_checked_dynamically(client: AsyncClient) -> None:
    body = await invoke(client, "check_tool_status", tool="web_search")

    assert body["ok"] is True
    assert body["data"]["tool"] == "web_search"
    assert body["data"]["available"] is True
    assert body["data"]["provider"] in {"tavily", "brave", "searxng", "duckduckgo"}
    assert "milliseconds" in body["summary"]


async def test_unknown_timezone_is_a_speakable_failure(client: AsyncClient) -> None:
    body = await invoke(client, "get_current_time", timezone="Mars/Olympus")

    assert body["ok"] is False
    assert "not a recognised timezone" in body["summary"]


async def test_unknown_tool_is_rejected(client: AsyncClient) -> None:
    body = await invoke(client, "launch_rocket")

    assert body["ok"] is False
    assert "Unknown tool" in body["error"]


async def test_invalid_arguments_are_explained(client: AsyncClient) -> None:
    body = await invoke(client, "save_note")

    assert body["ok"] is False
    assert "Invalid arguments for save_note" in body["error"]


async def test_reminder_round_trip(client: AsyncClient) -> None:
    created = await invoke(
        client,
        "create_reminder",
        title="call the dentist",
        remind_in_minutes=30,
        timezone="Asia/Kolkata",
    )
    assert created["ok"] is True
    assert "call the dentist" in created["summary"]

    listed = await invoke(client, "list_reminders", timezone="Asia/Kolkata")
    assert listed["ok"] is True
    assert len(listed["data"]["reminders"]) == 1
    assert listed["data"]["reminders"][0]["title"] == "call the dentist"


async def test_reminder_rejects_unparseable_time(client: AsyncClient) -> None:
    body = await invoke(client, "create_reminder", title="stand up", due_at="next tuesday-ish")

    assert body["ok"] is False
    assert "could not understand that time" in body["summary"]


async def test_notes_are_saved_and_searchable(client: AsyncClient) -> None:
    saved = await invoke(
        client,
        "save_note",
        body="The wifi password for the studio is aurora-1234.",
    )
    assert saved["ok"] is True

    found = await invoke(client, "search_notes", query="wifi password")
    assert found["ok"] is True
    assert len(found["data"]["notes"]) == 1
    assert "aurora-1234" in found["data"]["notes"][0]["body"]


async def test_notes_are_scoped_per_user(client: AsyncClient) -> None:
    await invoke(client, "save_note", body="A private thought.")

    response = await client.post(
        "/v1/tools/invoke",
        json={"tool": "search_notes", "user_id": "someone-else", "arguments": {"query": "private"}},
    )

    assert response.json()["data"]["notes"] == []


async def test_missing_note_search_is_graceful(client: AsyncClient) -> None:
    body = await invoke(client, "search_notes", query="nothing here")

    assert body["ok"] is True
    assert body["data"]["notes"] == []
