"""Which ESPHome device a Device Builder configuration and a sensor belong to.

Install flashes the chosen Device Builder configuration, so the update must
show on that configuration's device, and its revision sensor must not come from
another ESPHome device.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.esphome.const import CONF_DEVICE_NAME
from homeassistant.components.esphome.const import DOMAIN as ESPHOME_DOMAIN
from homeassistant.components.esphome.dashboard import async_get_dashboard
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er


def builder_node_name(hass: HomeAssistant, configuration: str) -> str | None:
    """The node name Device Builder reports for a configuration, if it is connected."""
    dashboard = async_get_dashboard(hass)
    if dashboard is None or not dashboard.data:
        return None
    for device in dashboard.data.values():
        if device.get("configuration") == configuration and isinstance(device.get("name"), str):
            return device["name"]
    return None


def sensor_node_name(hass: HomeAssistant, entity_id: str) -> str | None:
    """The node name of the ESPHome device an entity comes from; None if not ESPHome."""
    ent = er.async_get(hass).async_get(entity_id)
    if ent is None or ent.platform != ESPHOME_DOMAIN or ent.config_entry_id is None:
        return None
    owner = hass.config_entries.async_get_entry(ent.config_entry_id)
    if owner is None or owner.domain != ESPHOME_DOMAIN:
        return None
    return owner.data.get(CONF_DEVICE_NAME)


def node_device(hass: HomeAssistant, node_name: str) -> dr.DeviceEntry | None:
    """The ESPHome device with this node name (not one of its sub-devices)."""
    registry = dr.async_get(hass)
    found = [
        device
        for entry in hass.config_entries.async_entries(ESPHOME_DOMAIN)
        if entry.data.get(CONF_DEVICE_NAME) == node_name
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id)
        if device.via_device_id is None
    ]
    return found[0] if len(found) == 1 else None


def node_connection(hass: HomeAssistant, node_name: str) -> Any | None:
    """ESPHome's live connection data for a node (its config entry's runtime data).

    Used like Home Assistant's own ESPHome update does: ``available`` (connected
    now), ``device_info.has_deep_sleep`` and ``async_subscribe_device_updated``.
    None when the node is not set up in ESPHome or this data looks different.
    """
    for entry in hass.config_entries.async_entries(ESPHOME_DOMAIN):
        if entry.data.get(CONF_DEVICE_NAME) != node_name:
            continue
        data = getattr(entry, "runtime_data", None)
        if isinstance(getattr(data, "available", None), bool) and callable(
            getattr(data, "async_subscribe_device_updated", None)
        ):
            return data
    return None


def has_deep_sleep(connection: Any) -> bool:
    return bool(getattr(getattr(connection, "device_info", None), "has_deep_sleep", False))
