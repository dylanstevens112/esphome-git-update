"""Set up ESPHome Git Update from the UI: one entry per Device Builder device."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.esphome.const import CONF_DEVICE_NAME
from homeassistant.components.esphome.const import DOMAIN as ESPHOME_DOMAIN
from homeassistant.components.esphome.dashboard import async_get_dashboard
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import Platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_BRANCH,
    CONF_CONFIGURATION,
    CONF_REPO_URL,
    CONF_REVISION_ENTITY,
    CONF_TOKEN,
    DEFAULT_BRANCH,
    DOMAIN,
    REVISION_SENSOR_WORDS,
)
from .gitea import GiteaError, GiteaRepo, InsecureTokenError, origin


class GitUpdateConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pick a Device Builder device, then where its code lives."""

    VERSION = 1

    def __init__(self) -> None:
        self._configuration: str | None = None
        self._title: str | None = None
        self._node_name: str | None = None

    def _devices(self) -> dict[str, dict[str, Any]] | None:
        dashboard = async_get_dashboard(self.hass)
        if dashboard is None or not dashboard.last_update_success or not dashboard.data:
            return None
        return {
            d["configuration"]: d
            for d in dashboard.data.values()
            if isinstance(d.get("configuration"), str)
        }

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        devices = self._devices()
        if devices is None:
            return self.async_abort(reason="no_device_builder")
        configured = {e.unique_id for e in self._async_current_entries()}
        choices = {c: d for c, d in devices.items() if c not in configured}
        if not choices:
            return self.async_abort(reason="all_configured")

        if user_input is not None:
            configuration = user_input[CONF_CONFIGURATION]
            if configuration in choices:
                await self.async_set_unique_id(configuration)
                self._abort_if_unique_id_configured()
                device = choices[configuration]
                self._configuration = configuration
                self._node_name = device.get("name")
                self._title = device.get("friendly_name") or device.get("name") or configuration
                return await self.async_step_details()

        options = [
            selector.SelectOptionDict(
                value=c, label=f"{d.get('friendly_name') or d.get('name') or c} ({c})"
            )
            for c, d in sorted(choices.items())
        ]
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONFIGURATION): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        self._configuration = entry.data[CONF_CONFIGURATION]
        self._title = entry.title
        return await self.async_step_details()

    async def async_step_details(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        reconfigure = self.source == SOURCE_RECONFIGURE
        # The saved settings decide about the token; "current" only refills the form.
        saved = dict(self._get_reconfigure_entry().data) if reconfigure else {}
        current = dict(saved)
        errors: dict[str, str] = {}

        if user_input is not None:
            values = {k: v.strip() if isinstance(v, str) else v for k, v in user_input.items()}
            token = values.get(CONF_TOKEN) or ""
            if (
                reconfigure
                and token == ""
                and not values.get("clear_token")
                and saved.get(CONF_TOKEN)
                and origin(values[CONF_REPO_URL]) is not None
                and origin(values[CONF_REPO_URL]) == origin(saved.get(CONF_REPO_URL, ""))
            ):
                # An empty field keeps the saved token, but only for the same
                # server: a token is never sent to a server it was not given for.
                token = saved[CONF_TOKEN]
            data = {
                CONF_CONFIGURATION: self._configuration,
                CONF_REVISION_ENTITY: values[CONF_REVISION_ENTITY],
                CONF_REPO_URL: values[CONF_REPO_URL],
                CONF_BRANCH: values[CONF_BRANCH],
                CONF_TOKEN: token,
            }
            if self.hass.states.get(data[CONF_REVISION_ENTITY]) is None:
                errors[CONF_REVISION_ENTITY] = "unknown_entity"
            else:
                try:
                    repo = GiteaRepo(self.hass, data[CONF_REPO_URL], data[CONF_BRANCH], token or None)
                except InsecureTokenError:
                    errors["base"] = "token_needs_https"
                except GiteaError:
                    errors["base"] = "invalid_repo"
                else:
                    try:
                        await repo.latest()
                    except GiteaError:
                        errors["base"] = "cannot_read_repo"
            if not errors:
                if reconfigure:
                    return self.async_update_reload_and_abort(
                        self._get_reconfigure_entry(), data=data
                    )
                return self.async_create_entry(title=self._title or self._configuration, data=data)
            # Never put a token back into the form.
            current = {**current, **{k: v for k, v in data.items() if k != CONF_TOKEN}}

        schema: dict[Any, Any] = {
            vol.Required(
                CONF_REVISION_ENTITY,
                default=current.get(CONF_REVISION_ENTITY) or self._suggest_revision_sensor() or vol.UNDEFINED,
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain=Platform.SENSOR)),
            vol.Required(CONF_REPO_URL, default=current.get(CONF_REPO_URL, vol.UNDEFINED)): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
            ),
            vol.Required(CONF_BRANCH, default=current.get(CONF_BRANCH, DEFAULT_BRANCH)): str,
            # Never shown back: a saved token stays saved unless replaced or cleared.
            vol.Optional(CONF_TOKEN): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
        }
        if reconfigure and saved.get(CONF_TOKEN):
            schema[vol.Optional("clear_token", default=False)] = bool
        return self.async_show_form(
            step_id="details",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "device": self._title or self._configuration or "",
                "example_url": "https://git.example.com/owner/repo",
            },
        )

    def _suggest_revision_sensor(self) -> str | None:
        """The device's revision sensor, if exactly one sensor looks like it."""
        if not self._node_name:
            return None
        registry = er.async_get(self.hass)
        found = []
        for entry in self.hass.config_entries.async_entries(ESPHOME_DOMAIN):
            if entry.data.get(CONF_DEVICE_NAME) != self._node_name:
                continue
            for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
                name = (ent.original_name or ent.name or "").lower()
                if ent.domain == Platform.SENSOR and any(w in name for w in REVISION_SENSOR_WORDS):
                    found.append(ent.entity_id)
        return found[0] if len(found) == 1 else None
