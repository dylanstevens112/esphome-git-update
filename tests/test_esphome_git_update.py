"""ESPHome Git Update: setup, update state, release notes, install, safety."""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esphome_git_update.const import DOMAIN

API = "http://gitea.test:3000/api/v1/repos/owner/repo"
REPO = "http://gitea.test:3000/owner/repo"
REV = "sensor.panel_firmware_revision"
ENTITY = "update.panel_code"
NEW = "30736b4d91db388cceeb235424e364b36d25c54c"
TOKEN = "s3cr3t-token-value"
HAPI = "https://gitea.test/api/v1/repos/owner/repo"
HREPO = "https://gitea.test/owner/repo"
DATA = {
    "configuration": "panel.yaml",
    "revision_entity": REV,
    "repo_url": REPO,
    "branch": "main",
    "token": "",
}
FLOW = "custom_components.esphome_git_update.config_flow.async_get_dashboard"
UPDATE = "custom_components.esphome_git_update.update.async_get_dashboard"


def mock_branch(aioclient_mock, sha=NEW, message="fix(vacuum): new IDs\n\nbody", api=API, **kwargs):
    aioclient_mock.get(
        f"{api}/branches/main", json={"commit": {"id": sha, "message": message}}, **kwargs
    )


def dashboard(compile_ok=True, uploads=(True,), devices=None):
    dash = MagicMock()
    dash.last_update_success = True
    dash.supports_build_queue = True
    dash.data = devices or {
        "panel": {"name": "panel", "friendly_name": "Panel", "configuration": "panel.yaml"}
    }
    dash.api.compile = AsyncMock(return_value=compile_ok)
    dash.api.upload = AsyncMock(side_effect=list(uploads))
    return dash


async def setup(hass: HomeAssistant, aioclient_mock, revision="b675152", data=None):
    hass.config.components.add("esphome")  # the real one needs a device
    hass.states.async_set(REV, revision)
    mock_branch(aioclient_mock)
    mock_branch(aioclient_mock, api=HAPI)
    entry = MockConfigEntry(
        domain=DOMAIN, title="Panel", data=data or DATA, unique_id="panel.yaml"
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


# --- update state -----------------------------------------------------------


async def test_offers_new_commit(hass, aioclient_mock):
    await setup(hass, aioclient_mock)
    state = hass.states.get(ENTITY)
    assert state.state == "on"
    assert state.attributes["installed_version"] == "b675152"
    assert state.attributes["latest_version"] == "30736b4"
    assert state.attributes["release_summary"] == "fix(vacuum): new IDs"
    assert state.attributes["release_url"] == f"{REPO}/compare/b675152...30736b4"
    assert state.attributes["title"] == "Panel"


@pytest.mark.parametrize(
    ("revision", "expected"),
    [
        ("30736b4", "off"),
        ("30736B4", "off"),
        (NEW, "off"),  # full hash on the device
        ("1234567", "on"),  # digits only: still a commit, not a version
        ("30736b4+local", "on"),  # uncommitted changes
        ("unavailable", "unknown"),
        ("unknown", "unknown"),
        ("hello world", "unknown"),
        ("<script>", "unknown"),
    ],
)
async def test_state_follows_the_device(hass, aioclient_mock, revision, expected):
    await setup(hass, aioclient_mock, revision="30736b4")
    hass.states.async_set(REV, revision)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == expected


async def test_local_build_links_to_its_commit(hass, aioclient_mock):
    await setup(hass, aioclient_mock, revision="b675152+local")
    url = hass.states.get(ENTITY).attributes["release_url"]
    assert url == f"{REPO}/compare/b675152...30736b4"


# --- release notes ----------------------------------------------------------


async def notes(hass, hass_ws_client):
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "update/release_notes", "entity_id": ENTITY})
    result = (await client.receive_json())["result"]
    return result["release_notes"] if isinstance(result, dict) else result


async def test_release_notes_escape_markdown(hass, aioclient_mock, hass_ws_client):
    await setup(hass, aioclient_mock)
    aioclient_mock.get(
        f"{API}/compare/b675152...30736b4",
        json={"commits": [
            {"sha": NEW, "commit": {"message": "fix: *new* <img src=x> [link](http://x)\n\nbody"}},
            {"sha": "ee36859aaaa", "commit": {"message": "feat: one\n"}},
        ]},
    )
    text = await notes(hass, hass_ws_client)
    assert "**2 new commits**" in text
    assert "- `30736b4` fix: \\*new\\* \\<img src=x\\> \\[link\\](http://x)" in text
    assert "- `ee36859` feat: one" in text


async def test_release_notes_with_many_commits(hass, aioclient_mock, hass_ws_client):
    """A large comparison (several network chunks) is read whole."""
    await setup(hass, aioclient_mock)
    commits = [
        {"sha": f"{i:07x}" + "a" * 33, "commit": {"message": f"change {i}\n\n" + "x" * 3000}}
        for i in range(200)
    ]
    aioclient_mock.get(f"{API}/compare/b675152...30736b4", json={"commits": commits})
    text = await notes(hass, hass_ws_client)
    assert "**200 new commits**" in text
    assert "- … and 170 older" in text


async def test_release_notes_fall_back_to_latest_title(hass, aioclient_mock, hass_ws_client):
    await setup(hass, aioclient_mock)
    aioclient_mock.get(f"{API}/compare/b675152...30736b4", status=404)
    assert await notes(hass, hass_ws_client) == "fix(vacuum): new IDs"


# --- install ----------------------------------------------------------------


async def install(hass):
    await hass.services.async_call("update", "install", {"entity_id": ENTITY}, blocking=True)


async def test_install_builds_then_uploads(hass, aioclient_mock):
    await setup(hass, aioclient_mock)
    dash = dashboard(uploads=(False, True))
    with patch(UPDATE, return_value=dash), patch(
        "custom_components.esphome_git_update.update.asyncio.sleep", AsyncMock()
    ):
        await install(hass)
    dash.api.compile.assert_awaited_once_with("panel.yaml")
    assert dash.api.upload.await_count == 2
    dash.api.upload.assert_awaited_with("panel.yaml", "OTA")
    assert hass.states.get(ENTITY).attributes["in_progress"] is False


@pytest.mark.parametrize(
    ("dash", "key"),
    [
        (None, "no_device_builder"),
        (dashboard(compile_ok=False), "build_failed"),
        (dashboard(uploads=(False, False)), "upload_failed"),
        (dashboard(devices={"x": {"name": "x", "configuration": "other.yaml"}}), "unknown_configuration"),
    ],
)
async def test_install_failures_are_reported(hass, aioclient_mock, dash, key):
    await setup(hass, aioclient_mock)
    with patch(UPDATE, return_value=dash), patch(
        "custom_components.esphome_git_update.update.asyncio.sleep", AsyncMock()
    ), pytest.raises(HomeAssistantError) as err:
        await install(hass)
    assert err.value.translation_key == key
    assert hass.states.get(ENTITY).attributes["in_progress"] is False


@pytest.mark.parametrize(
    ("step", "key"),
    [("compile", "build_failed"), ("upload", "upload_failed")],
)
async def test_connection_errors_and_hangs_end_cleanly(hass, aioclient_mock, step, key):
    import aiohttp

    await setup(hass, aioclient_mock)
    for failure in (aiohttp.ClientConnectionError(), "hang"):
        dash = dashboard(uploads=(True, True))

        async def broken(*_args, failure=failure):
            if failure == "hang":
                await asyncio.Event().wait()  # never set
            raise failure

        setattr(dash.api, step, AsyncMock(side_effect=broken))
        with patch(UPDATE, return_value=dash), patch(
            "custom_components.esphome_git_update.update.asyncio.sleep", AsyncMock()
        ), patch("custom_components.esphome_git_update.update.COMPILE_TIMEOUT_S", 0.05), patch(
            "custom_components.esphome_git_update.update.UPLOAD_TIMEOUT_S", 0.05
        ), pytest.raises(HomeAssistantError) as err:
            await install(hass)
        assert err.value.translation_key == key
        assert hass.states.get(ENTITY).attributes["in_progress"] is False


async def test_second_install_is_refused_while_one_runs(hass, aioclient_mock):
    await setup(hass, aioclient_mock)
    release = asyncio.Event()
    dash = dashboard()

    async def slow_compile(_configuration):
        await release.wait()
        return True

    dash.api.compile = AsyncMock(side_effect=slow_compile)
    with patch(UPDATE, return_value=dash):
        first = hass.async_create_task(install(hass))
        for _ in range(5):
            await asyncio.sleep(0)
        assert hass.states.get(ENTITY).attributes["in_progress"] is True
        with pytest.raises(HomeAssistantError) as err:
            await install(hass)
        # Home Assistant refuses it first (update_in_progress); ours is the backstop.
        assert err.value.translation_key in ("update_in_progress", "install_running")
        release.set()
        await first
    assert dash.api.compile.await_count == 1


async def test_builds_one_at_a_time_without_a_queue(hass, aioclient_mock):
    await setup(hass, aioclient_mock)
    dash = dashboard()
    dash.supports_build_queue = False
    seen = []

    async def compile_(_configuration):
        from custom_components.esphome_git_update import build_lock

        seen.append(build_lock(hass).locked())
        return True

    dash.api.compile = AsyncMock(side_effect=compile_)
    with patch(UPDATE, return_value=dash):
        await install(hass)
    assert seen == [True]


# --- setup and safety -------------------------------------------------------


async def test_gitea_down_retries_setup(hass, aioclient_mock):
    hass.config.components.add("esphome")
    aioclient_mock.get(f"{API}/branches/main", status=500)
    entry = MockConfigEntry(domain=DOMAIN, title="Panel", data=DATA)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.SETUP_RETRY


@pytest.mark.parametrize(
    "answer",
    [
        {"status": 302, "headers": {"Location": "http://evil.test/"}},
        {"json": {"commit": {"id": "../../etc", "message": "x"}}},
        {"json": ["not", "a", "branch"]},
        {"text": "x" * (2 * 1024 * 1024 + 10)},
    ],
)
async def test_bad_answers_are_refused(hass, aioclient_mock, answer):
    hass.config.components.add("esphome")
    aioclient_mock.get(f"{API}/branches/main", **answer)
    entry = MockConfigEntry(domain=DOMAIN, title="Panel", data=DATA)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.SETUP_RETRY


async def test_token_goes_only_to_gitea_and_is_never_logged(hass, aioclient_mock, caplog):
    caplog.set_level(logging.DEBUG)
    hass.config.components.add("esphome")
    aioclient_mock.get(f"{HAPI}/branches/main", status=500)
    entry = MockConfigEntry(
        domain=DOMAIN, title="Panel", data={**DATA, "repo_url": HREPO, "token": TOKEN}
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    (_method, url, _data, headers) = aioclient_mock.mock_calls[-1]
    assert str(url).startswith(HAPI)
    assert headers["Authorization"] == f"token {TOKEN}"
    assert TOKEN not in caplog.text


@pytest.mark.parametrize(
    "url",
    ["ftp://gitea.test/owner/repo", "http://gitea.test/onlyone", "http://user:pw@gitea.test/o/r",
     "http://gitea.test/o/r?x=1", "not a url"],
)
async def test_invalid_saved_settings_fail_cleanly(hass, aioclient_mock, url):
    hass.config.components.add("esphome")
    entry = MockConfigEntry(domain=DOMAIN, title="Panel", data={**DATA, "repo_url": url})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.SETUP_ERROR
    assert aioclient_mock.call_count == 0


async def test_gitea_under_a_sub_path(hass, aioclient_mock):
    hass.config.components.add("esphome")
    hass.states.async_set(REV, "b675152")
    aioclient_mock.get(
        "https://example.test/git/api/v1/repos/owner/repo/branches/release/1",
        json={"commit": {"id": NEW, "message": "m"}},
    )
    entry = MockConfigEntry(
        domain=DOMAIN, title="Panel",
        data={**DATA, "repo_url": "https://example.test/git/owner/repo.git", "branch": "release/1"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).attributes["release_url"] == (
        "https://example.test/git/owner/repo/compare/b675152...30736b4"
    )


async def test_shows_on_the_device(hass, aioclient_mock, device_registry, entity_registry):
    esphome_entry = MockConfigEntry(domain="esphome", title="Panel", data={"device_name": "panel"})
    esphome_entry.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=esphome_entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, "80:f1:b2:00:00:01")},
        name="Panel",
    )
    entity_registry.async_get_or_create(
        "sensor", "esphome", "rev", suggested_object_id="panel_firmware_revision", device_id=device.id
    )
    await setup(hass, aioclient_mock)
    ours = [e for e in entity_registry.entities.values() if e.platform == DOMAIN]
    assert len(ours) == 1 and ours[0].device_id == device.id
    assert ours[0].entity_id == ENTITY
    assert len(list(device_registry.devices)) == 1


async def test_several_devices(hass, aioclient_mock):
    hass.config.components.add("esphome")
    hass.states.async_set(REV, "b675152")
    hass.states.async_set("sensor.other_revision", "30736b4")
    mock_branch(aioclient_mock)
    for title, config, rev in (("Panel", "panel.yaml", REV), ("Other", "other.yaml", "sensor.other_revision")):
        entry = MockConfigEntry(
            domain=DOMAIN, title=title, unique_id=config,
            data={**DATA, "configuration": config, "revision_entity": rev},
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("update.panel_code").state == "on"
    assert hass.states.get("update.other_code").state == "off"


# --- config flow ------------------------------------------------------------


async def test_flow_needs_device_builder(hass):
    hass.config.components.add("esphome")
    with patch(FLOW, return_value=None):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_device_builder"


async def test_flow_picks_device_and_suggests_its_sensor(hass, aioclient_mock, entity_registry):
    hass.config.components.add("esphome")
    esphome_entry = MockConfigEntry(domain="esphome", data={"device_name": "panel"})
    esphome_entry.add_to_hass(hass)
    entity_registry.async_get_or_create(
        "sensor", "esphome", "rev", suggested_object_id="panel_firmware_revision",
        config_entry=esphome_entry, original_name="Firmware revision",
    )
    entity_registry.async_get_or_create(
        "sensor", "esphome", "ver", suggested_object_id="panel_firmware_version",
        config_entry=esphome_entry, original_name="Firmware version",
    )
    hass.states.async_set(REV, "b675152")
    dash = dashboard(devices={
        "panel": {"name": "panel", "friendly_name": "Panel", "configuration": "panel.yaml"},
        "other": {"name": "other", "configuration": "other.yaml"},
    })
    with patch(FLOW, return_value=dash):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["step_id"] == "user"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"configuration": "panel.yaml"}
        )
        assert result["step_id"] == "details"
        schema = {str(k): k for k in result["data_schema"].schema}
        assert schema["revision_entity"].default() == REV

        aioclient_mock.get(f"{API}/branches/main", status=404)
        details = {"revision_entity": REV, "repo_url": REPO, "branch": "main"}
        result = await hass.config_entries.flow.async_configure(result["flow_id"], details)
        assert result["errors"] == {"base": "cannot_read_repo"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**details, "repo_url": "http://gitea.test/only-one"}
        )
        assert result["errors"] == {"base": "invalid_repo"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**details, "revision_entity": "sensor.nope"}
        )
        assert result["errors"] == {"revision_entity": "unknown_entity"}

        aioclient_mock.clear_requests()
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**details, "token": TOKEN}
        )
        assert result["errors"] == {"base": "token_needs_https"}
        assert aioclient_mock.call_count == 0  # refused before anything is sent
        schema = {str(k): k for k in result["data_schema"].schema}
        assert schema["token"].default is vol.UNDEFINED  # never put back in the form

        mock_branch(aioclient_mock, api=HAPI)
        with patch("custom_components.esphome_git_update.async_setup_entry", return_value=True):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {**details, "repo_url": HREPO + "/ ", "token": TOKEN}
            )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Panel"
    assert result["data"] == {**DATA, "repo_url": HREPO + "/", "token": TOKEN}
    assert result["result"].unique_id == "panel.yaml"


async def test_flow_offers_only_devices_not_yet_set_up(hass):
    hass.config.components.add("esphome")
    MockConfigEntry(domain=DOMAIN, unique_id="panel.yaml", data=DATA).add_to_hass(hass)
    with patch(FLOW, return_value=dashboard()):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "all_configured"


async def test_reconfigure_keeps_or_clears_the_token(hass, aioclient_mock):
    entry = await setup(hass, aioclient_mock, data={**DATA, "repo_url": HREPO, "token": TOKEN})
    details = {"revision_entity": REV, "repo_url": HREPO + "/", "branch": "main"}

    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "details"
    schema = {str(k): k for k in result["data_schema"].schema}
    assert "clear_token" in schema
    assert schema["token"].default is vol.UNDEFINED  # the saved token is never shown
    result = await hass.config_entries.flow.async_configure(result["flow_id"], details)
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["token"] == TOKEN  # same server: kept
    assert aioclient_mock.mock_calls[-1][3]["Authorization"] == f"token {TOKEN}"

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**details, "clear_token": True}
    )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["token"] == ""


@pytest.mark.parametrize(
    "new_url",
    [
        "https://elsewhere.test/owner/repo",  # another host
        "https://gitea.test:8443/owner/repo",  # another port
        "http://gitea.test/owner/repo",  # no longer encrypted
    ],
)
async def test_saved_token_never_goes_to_another_server(hass, aioclient_mock, new_url):
    entry = await setup(hass, aioclient_mock, data={**DATA, "repo_url": HREPO, "token": TOKEN})
    new_api = new_url.replace("/owner/repo", "/api/v1/repos/owner/repo")
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{new_api}/branches/main", status=404)
    details = {"revision_entity": REV, "repo_url": new_url, "branch": "main"}

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], details)
    assert result["errors"] == {"base": "cannot_read_repo"}
    # Submitting again after the error must not reuse the token either.
    result = await hass.config_entries.flow.async_configure(result["flow_id"], details)
    assert result["errors"] == {"base": "cannot_read_repo"}
    assert aioclient_mock.call_count == 2
    for _method, url, _data, headers in aioclient_mock.mock_calls:
        assert str(url).startswith(new_api)
        assert "Authorization" not in (headers or {})

    aioclient_mock.clear_requests()
    mock_branch(aioclient_mock, api=new_api)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], details)
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["token"] == ""  # left behind with the old server
    assert entry.data["repo_url"] == new_url


async def test_saved_http_token_is_refused_without_sending(hass, aioclient_mock):
    hass.config.components.add("esphome")
    entry = MockConfigEntry(domain=DOMAIN, title="Panel", data={**DATA, "token": TOKEN})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.SETUP_ERROR
    assert aioclient_mock.call_count == 0
