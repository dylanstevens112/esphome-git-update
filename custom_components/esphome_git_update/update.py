"""The update entity: device revision against the branch, installed by Device Builder."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import aiohttp
from homeassistant.components.esphome.dashboard import async_get_dashboard
from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device import async_entity_id_to_device
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import GitUpdateConfigEntry, build_lock
from .const import (
    COMPILE_TIMEOUT_S,
    CONF_CONFIGURATION,
    CONF_NODE_NAME,
    CONF_REVISION_ENTITY,
    DOMAIN,
    LOCAL_SUFFIX,
    UPLOAD_ATTEMPTS,
    UPLOAD_RETRY_DELAY_S,
    UPLOAD_TIMEOUT_S,
)
from .devices import builder_node_name, node_device, sensor_node_name
from .gitea import (
    Commit,
    GiteaCoordinator,
    GiteaError,
    device_revision,
    is_commit,
    needs_build,
    same_commit,
)

_LOGGER = logging.getLogger(__name__)
MAX_NOTES = 30


def _markdown_text(text: str) -> str:
    """Commit titles as plain text in the release notes."""
    for ch in "\\`*_[]<>#|~":
        text = text.replace(ch, "\\" + ch)
    return text


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GitUpdateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([GitUpdateEntity(hass, entry)])


class GitUpdateEntity(CoordinatorEntity[GiteaCoordinator], UpdateEntity):
    """Offers every new commit on the branch; Install builds and uploads it."""

    _attr_has_entity_name = True
    _attr_translation_key = "code"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS | UpdateEntityFeature.RELEASE_NOTES
    )

    def __init__(self, hass: HomeAssistant, entry: GitUpdateConfigEntry) -> None:
        super().__init__(entry.runtime_data)
        self._configuration: str = entry.data[CONF_CONFIGURATION]
        self._revision_entity: str = entry.data[CONF_REVISION_ENTITY]
        self._attr_unique_id = f"{entry.entry_id}_code"
        self._attr_title = entry.title
        self._attr_in_progress = False
        # Show the update on the device Install flashes: the one built from the
        # chosen configuration (how Home Assistant's own helpers join another
        # integration's device).
        device = self._device(hass, entry)
        if device is not None:
            self.device_entry = device
        else:  # no device: stand alone, named after the entry
            self._attr_has_entity_name = False
            self._attr_translation_key = None
            self._attr_name = f"{entry.title} code"
        self._install_lock = asyncio.Lock()

    def _device(self, hass: HomeAssistant, entry: GitUpdateConfigEntry) -> dr.DeviceEntry | None:
        node = entry.data.get(CONF_NODE_NAME) or builder_node_name(hass, self._configuration)
        if node and (device := node_device(hass, node)) is not None:
            return device
        # Otherwise the sensor's device, but never another ESPHome device's.
        owner = sensor_node_name(hass, self._revision_entity)
        if node and owner and owner != node:
            return None
        return async_entity_id_to_device(hass, self._revision_entity)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._revision_entity], self._revision_changed
            )
        )

    @callback
    def _revision_changed(self, event: Event[EventStateChangedData]) -> None:
        if self.coordinator.ignored_files:
            # What the device lacks must be read again first; the refresh
            # writes the state, so it never flips to "update" in between.
            self.hass.async_create_task(self.coordinator.async_request_refresh())
        else:
            self.async_write_ha_state()

    def _changes(self) -> tuple[Commit, ...] | None:
        """With ignored files: the commits the device lacks, if known."""
        data, installed = self.coordinator.data, self._installed_commit()
        if (
            not self.coordinator.ignored_files
            or data is None
            or installed is None
            or data.base != installed
        ):
            return None
        return data.changes

    def _needing_build(self, commits: tuple[Commit, ...] | list[Commit]) -> list[Commit]:
        return [c for c in commits if needs_build(c, self.coordinator.ignored_files)]

    def _installed_commit(self) -> str | None:
        """The commit the device runs, without the "+local" mark."""
        installed = self.installed_version
        if installed is None:
            return None
        commit = installed.removesuffix(LOCAL_SUFFIX)
        return commit if is_commit(commit) else None

    @property
    def installed_version(self) -> str | None:
        state = self.hass.states.get(self._revision_entity)
        # Only a commit (optionally "+local") counts; "unknown", "unavailable"
        # or anything else the device sends leaves the version unknown.
        return device_revision(state.state if state else None)

    @property
    def latest_version(self) -> str | None:
        return self.coordinator.data.tip.short if self.coordinator.data else None

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        # Commits have no order: any difference is an update, and a build with
        # uncommitted changes is never the branch. A device may report a
        # longer or shorter hash than the 7 characters shown here.
        if installed_version.endswith(LOCAL_SUFFIX):
            return True
        # Compare with the full hash: the device may report one, and two
        # commits can share the 7 characters shown.
        tip = self.coordinator.data.tip.sha if self.coordinator.data else latest_version
        if same_commit(installed_version, tip):
            return False
        # With ignored files, only commits that change other files count.
        # Unknown changes count, as without a filter.
        changes = self._changes()
        return changes is None or bool(self._needing_build(changes))

    @property
    def release_summary(self) -> str | None:
        if not self.coordinator.data:
            return None
        changes = self._changes()
        relevant = self._needing_build(changes) if changes else []
        title = relevant[0].title if relevant else self.coordinator.data.tip.title
        return title[:255]

    @property
    def release_url(self) -> str | None:
        installed, latest = self._installed_commit(), self.latest_version
        if not installed or not latest:
            return None
        return self.coordinator.repo.compare_url(installed, latest)

    async def async_release_notes(self) -> str | None:
        installed, latest = self._installed_commit(), self.latest_version
        if not installed or not latest or not self.coordinator.data:
            return None
        # With ignored files the comparison was already read; reuse it.
        commits = list(self._changes() or [])
        if not commits:
            try:
                commits = await self.coordinator.repo.changes(installed, latest)
            except GiteaError:
                commits = []
        if not commits:
            return _markdown_text(self.coordinator.data.tip.title)
        skipped = 0
        if self.coordinator.ignored_files:
            relevant = self._needing_build(commits)
            skipped = len(commits) - len(relevant)
            commits = relevant
        if not commits:
            plural = "s" if skipped != 1 else ""
            return f"No commit needs a new build: {skipped} commit{plural} only change ignored files."
        lines = [f"- `{c.short}` {_markdown_text(c.title)}" for c in commits[:MAX_NOTES]]
        if len(commits) > MAX_NOTES:
            lines.append(f"- … and {len(commits) - MAX_NOTES} older")
        plural = "s" if len(commits) != 1 else ""
        text = f"**{len(commits)} new commit{plural}**\n\n" + "\n".join(lines)
        if skipped:
            plural = "s" if skipped != 1 else ""
            text += f"\n\n{skipped} other commit{plural} only change ignored files."
        return text

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        if self._install_lock.locked():
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="install_running",
                translation_placeholders={"device": self._attr_title or ""},
            )
        async with self._install_lock:
            dashboard = async_get_dashboard(self.hass)
            if dashboard is None or not dashboard.last_update_success:
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="no_device_builder"
                )
            devices = dashboard.data or {}
            if not any(d.get("configuration") == self._configuration for d in devices.values()):
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="unknown_configuration",
                    translation_placeholders={"configuration": self._configuration},
                )

            self._attr_in_progress = True
            self.async_write_ha_state()
            try:
                # Device Builder fetches the branch itself when it builds, so
                # this installs whatever is newest at that moment.
                queue = getattr(dashboard, "supports_build_queue", False)
                lock = contextlib.nullcontext() if queue else build_lock(self.hass)
                try:
                    async with lock, asyncio.timeout(COMPILE_TIMEOUT_S):
                        built = await dashboard.api.compile(self._configuration)
                except (aiohttp.ClientError, TimeoutError) as err:
                    _LOGGER.warning("Building %s failed: %s", self._configuration, type(err).__name__)
                    built = False
                if not built:
                    raise HomeAssistantError(
                        translation_domain=DOMAIN,
                        translation_key="build_failed",
                        translation_placeholders={"configuration": self._configuration},
                    )
                for attempt in range(1, UPLOAD_ATTEMPTS + 1):
                    try:
                        async with asyncio.timeout(UPLOAD_TIMEOUT_S):
                            uploaded = await dashboard.api.upload(self._configuration, "OTA")
                    except (aiohttp.ClientError, TimeoutError) as err:
                        _LOGGER.warning("Upload of %s failed: %s", self._configuration, type(err).__name__)
                        uploaded = False
                    if uploaded:
                        break
                    if attempt == UPLOAD_ATTEMPTS:
                        raise HomeAssistantError(
                            translation_domain=DOMAIN,
                            translation_key="upload_failed",
                            translation_placeholders={"device": self._attr_title or ""},
                        )
                    _LOGGER.warning("Upload of %s failed, trying again", self._configuration)
                    await asyncio.sleep(UPLOAD_RETRY_DELAY_S)
            finally:
                self._attr_in_progress = False
                self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
