import logging

from .const import DOMAIN
from .coordinator import DimplexMqttCoordinator
from .utils.translations_helper import load_translations

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor", "number", "select"]


async def async_setup_entry(hass, entry):
    await hass.async_add_executor_job(load_translations)

    hass.data.setdefault(DOMAIN, {})

    coordinator = DimplexMqttCoordinator(
        hass,
        entry.data,
    )

    hass.data[DOMAIN][entry.entry_id] = coordinator

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await coordinator.async_shutdown()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
        raise

    return True


async def async_unload_entry(hass, entry):
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if not unload_ok:
        return False

    coordinator = hass.data[DOMAIN].pop(entry.entry_id, None)

    if coordinator is not None:
        await coordinator.async_shutdown()

    if not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)

    return unload_ok
