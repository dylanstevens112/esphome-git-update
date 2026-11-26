"""ESPHome Git Update.

Offers an update for an ESPHome device whenever its Git branch has a commit
the device is not running, and installs it through ESPHome Device Builder
(the same compile and over-the-air upload as Home Assistant's own ESPHome
update, which only offers an update when the ESPHome version changes).
One config entry per device.
"""
from __future__ import annotations

import asyncio

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError

from .const import DOMAIN
from .gitea import GiteaCoordinator, GiteaError

PLATFORMS = [Platform.UPDATE]

type GitUpdateConfigEntry = ConfigEntry[GiteaCoordinator]


def build_lock(hass: HomeAssistant) -> asyncio.Lock:
    """One build at a time across devices when Device Builder has no queue."""
    return hass.data.setdefault(f"{DOMAIN}_build_lock", asyncio.Lock())


async def async_setup_entry(hass: HomeAssistant, entry: GitUpdateConfigEntry) -> bool:
    try:
        coordinator = GiteaCoordinator(hass, entry)
    except GiteaError as err:
        raise ConfigEntryError(f"Invalid repository settings ({err})") from err
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GitUpdateConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
