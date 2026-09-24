"""Number platform for Dimplex MQTT."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

NUMBER_CONFIG_FILE = Path(__file__).parent / "sensors.json"
TRANSLATION_DIR = Path(__file__).parent / "translations"

UNITS = {
    "celsius": UnitOfTemperature.CELSIUS,
    "kelvin": "K",
    "h": UnitOfTime.HOURS,
    "min": UnitOfTime.MINUTES,
    "day": UnitOfTime.DAYS,
}


@dataclass(frozen=True, kw_only=True)
class DimplexMqttNumberEntityDescription(NumberEntityDescription):
    id: str
    scale: float = 1.0
    offset: int = 0
    hidden: bool = False


def _entity_name(language: str, key: str) -> str:
    path = TRANSLATION_DIR / f"{language}.json"
    if not path.exists():
        path = TRANSLATION_DIR / "en.json"
    try:
        with path.open("r", encoding="utf-8") as file:
            translations = json.load(file)
    except (OSError, json.JSONDecodeError):
        translations = {}
    entities = translations.get("entity", {})
    name = entities.get("number", {}).get(key, {}).get("name")
    if name is not None:
        return name
    for category in ("sensor", "binary_sensor", "select"):
        name = entities.get(category, {}).get(key, {}).get("name")
        if name is not None:
            return name
    return key


def _walk_number_config(node, installer_only=False):
    if isinstance(node, list):
        for item in node:
            yield from _walk_number_config(item)
        return

    if not isinstance(node, dict) or isinstance(node.get("select"), dict):
        return

    if "id" in node:
        if (
            not node.get("read_only", True)
            and not str(node["id"]).endswith("d")
        ):
            yield node, installer_only
        return

    for object_key, value in node.items():
        yield from _walk_number_config(
            value,
            installer_only or object_key == "settings",
        )


def _load_number_descriptions():
    with NUMBER_CONFIG_FILE.open("r", encoding="utf-8") as file:
        config = json.load(file)

    descriptions = []

    for item, installer_only in _walk_number_config(config):
        offset = item.get("offset", 0)
        scale = item.get("scale", 1.0)

        descriptions.append(
            DimplexMqttNumberEntityDescription(
                key=item["id"],
                id=item["id"],
                translation_key=item.get("translation_key", item["key"]),
                native_unit_of_measurement=UNITS.get(item.get("unit")),
                native_min_value=item.get("min", 0) + offset,
                native_max_value=item.get("max", 100) + offset,
                native_step=item.get("step", 1),
                scale=scale,
                offset=offset,
                hidden=item.get("hidden", False),
                entity_category=EntityCategory.CONFIG if installer_only else None,
            )
        )

    return tuple(descriptions)


NUMBER_DESCRIPTIONS = _load_number_descriptions()


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]

    added: set[str] = set()
    installer_access = hass.data[DOMAIN][entry.entry_id].config.get(
        "installer_access", False
    )

    def add_new_numbers():
        data = coordinator.data or {}
        new_entities = []

        for description in NUMBER_DESCRIPTIONS:
            if (
                description.entity_category == EntityCategory.CONFIG
                and not installer_access
            ):
                continue
            if description.hidden and not installer_access:
                continue
            if description.id in data and description.id not in added:
                added.add(description.id)
                new_entities.append(DimplexMqttNumber(coordinator, description))

        if new_entities:
            async_add_entities(new_entities)

    add_new_numbers()
    coordinator.async_add_listener(add_new_numbers)


class DimplexMqttNumber(CoordinatorEntity, NumberEntity):
    entity_description: DimplexMqttNumberEntityDescription
    _attr_has_entity_name = False

    def __init__(self, coordinator, description):
        super().__init__(coordinator)

        self.entity_description = description
        self._attr_translation_key = description.translation_key
        self._attr_name = _entity_name(
            (coordinator.hass.config.language or "en").split("-")[0],
            description.translation_key,
        )
        self._attr_unique_id = f"{coordinator.device_id}_{description.id}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.device_id)},
            "name": "Dimplex MQTT Gateway",
            "manufacturer": "Dimplex",
            "model": "MQTT Gateway",
        }

    @property
    def available(self) -> bool:
        data = self.coordinator.data or {}
        return self.entity_description.id in data

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        value = data.get(self.entity_description.id)

        if value is None:
            return None

        try:
            return float(value) + self.entity_description.offset
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.hass.async_add_executor_job(
            self.coordinator.client.write_variable,
            self.entity_description.id,
            str(value - self.entity_description.offset),
        )