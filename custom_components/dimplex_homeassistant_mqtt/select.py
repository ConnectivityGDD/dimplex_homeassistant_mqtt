from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from homeassistant.components.select import (
    SelectEntity,
    SelectEntityDescription,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

SELECT_CONFIG_FILE = Path(__file__).parent / "sensors.json"
TRANSLATION_DIR = Path(__file__).parent / "translations"

@dataclass(frozen=True, kw_only=True)
class DimplexMqttSelectEntityDescription(
    SelectEntityDescription
):
    id: str
    raw_options: list[str]
    hidden: bool = False

def _load_translation_file(language: str) -> dict[str, Any]:
    path = TRANSLATION_DIR / f"{language}.json"

    if not path.exists():
        path = TRANSLATION_DIR / "en.json"

    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return {}


TRANSLATIONS = {
    "en": _load_translation_file("en"),
    "de": _load_translation_file("de"),
}

def _walk_select_config(node):
    if isinstance(node, list):
        for item in node:
            yield from _walk_select_config(item)
        return

    if not isinstance(node, dict):
        return

    if isinstance(node.get("select"), dict):
        yield node
        return

    for value in node.values():
        yield from _walk_select_config(value)


def _load_select_descriptions():
    with SELECT_CONFIG_FILE.open("r", encoding="utf-8") as file:
        config = json.load(file)

    descriptions = []

    for item in _walk_select_config(config):
        raw_options = [
            option.strip()
            for option in item["select"]["range"].split(",")
        ]

        descriptions.append(
            DimplexMqttSelectEntityDescription(
                key=item["id"],
                id=item["id"],
                translation_key=item["key"],
                raw_options=raw_options,
                hidden=item.get("hidden", False),
            )
        )

    return tuple(descriptions)


SELECT_DESCRIPTIONS = _load_select_descriptions()


async def async_setup_entry(
    hass,
    entry,
    async_add_entities,
):
    coordinator = hass.data[DOMAIN][entry.entry_id]

    added: set[str] = set()

    def add_new_selects():
        data = coordinator.data or {}
        new_entities = []

        for description in SELECT_DESCRIPTIONS:
            if description.hidden and not coordinator.config.get(
                "installer_access", False
            ):
                continue
            if (
                description.id in data
                and description.id not in added
            ):
                added.add(description.id)

                new_entities.append(
                    DimplexMqttSelect(
                        coordinator,
                        description,
                    )
                )

        if new_entities:
            async_add_entities(new_entities)

    add_new_selects()
    coordinator.async_add_listener(add_new_selects)


class DimplexMqttSelect(
    CoordinatorEntity,
    SelectEntity,
):
    entity_description: (
        DimplexMqttSelectEntityDescription
    )

    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator,
        description,
    ):
        super().__init__(coordinator)

        self.entity_description = description
        self._attr_translation_key = description.translation_key
        translations = TRANSLATIONS.get(
            (coordinator.hass.config.language or "en").split("-")[0],
            TRANSLATIONS.get("en", {}),
        )
        entities = translations.get("entity", {})
        self._attr_name = description.translation_key
        for category in ("select", "sensor", "number", "binary_sensor"):
            name = entities.get(category, {}).get(
                description.translation_key, {}
            ).get("name")
            if name is not None:
                self._attr_name = name
                break
        self._attr_unique_id = f"{coordinator.device_id}_{description.id}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.device_id)},
            "name": "Dimplex MQTT Gateway",
            "manufacturer": "Dimplex",
            "model": "MQTT Gateway",
        }

    @property
    def available(self):
        return (
            self.entity_description.id
            in (self.coordinator.data or {})
        )

    @property
    def current_option(self):
        value = (self.coordinator.data or {}).get(self.entity_description.id)
        if value is None:
            return None

        states = self._translated_options()
        return states.get(str(value), str(value))
    
    @property
    def options(self):
        states = self._translated_options()
        return [
            states.get(value, value)
            for value in self.entity_description.raw_options
        ]
    
    def _translated_options(self) -> dict[str, str]:
        language = (self.coordinator.hass.config.language or "en").split("-")[0]
        translations = TRANSLATIONS.get(language) or TRANSLATIONS.get("en", {})

        return (
            translations
            .get("entity", {})
            .get("select", {})
            .get(self.entity_description.translation_key, {})
            .get("state", {})
        )

    async def async_select_option(self, option: str) -> None:
        states = self._translated_options()
        reverse_map = {label: value for value, label in states.items()}

        mqtt_value = reverse_map.get(option, option)

        await self.coordinator.hass.async_add_executor_job(
            self.coordinator.client.write_variable,
            self.entity_description.id,
            mqtt_value,
        )# type: ignore