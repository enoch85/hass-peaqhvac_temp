from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PEAQENABLED

"""
ventilate pollen
sun-heat timer?
pre-cold timer
night-cool
"""


async def async_setup_entry(hass: HomeAssistant, config_entry, async_add_entities):
    hub = hass.data[DOMAIN]["hub"]
    peaqsensors = [PeaqBinarySensorEnabled(hub)]
    async_add_entities(peaqsensors)


class PeaqBinarySensorEnabled(BinarySensorEntity):
    """The binary sensor for peaq being enabled or disabled"""

    def __init__(self, hub) -> None:
        self._attr_name = f"{hub.hubname} {PEAQENABLED}"
        self._hub = hub
        self._attr_device_class = "none"

    @property
    def unique_id(self):
        return f"{DOMAIN.lower()}_{self._attr_name}"

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self._hub.hub_id)}}

    @property
    def is_on(self) -> bool:
        return self._hub.sensors.peaqhvac_enabled.value
