""" light platform """
from __future__ import annotations

import logging
from typing import Any

from bleak import BleakClient, BleakError
from bleak.backends.client import BaseBleakClient
from bleak.backends.device import BLEDevice
from bleak_retry_connector import establish_connection
import voluptuous as vol

from homeassistant.components.cover import (
    ENTITY_ID_FORMAT,
    PLATFORM_SCHEMA,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, CONF_NAME, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import generate_entity_id
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MODEL_TVLIFT, MODEL_UNKNOWN

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_MAC): cv.string,
        vol.Optional(CONF_NAME, default=DOMAIN): cv.string,
    }
)

CONTROL_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

SUPPORT_TVLIFT = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE

_LOGGER = logging.getLogger(__name__)


def model_from_name(ble_name: str) -> str:
    model = MODEL_UNKNOWN
    if ble_name.startswith("limoss"):
        model = MODEL_TVLIFT
    return model


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the platform from config_entry."""
    _LOGGER.debug(
        f"lift async_setup_entry: setting up the config entry {config_entry.title} "
        f"with data:{config_entry.data}"
    )
    name = config_entry.data.get(CONF_NAME) or DOMAIN
    ble_device = hass.data[DOMAIN][config_entry.entry_id]

    entity = BreagleBT(name, ble_device)
    async_add_entities([entity])


class BreagleBT(CoverEntity):
    """Representation of a lift."""

    def __init__(self, name: str, ble_device: BLEDevice) -> None:
        """Initialize the lift."""
        self._client: BleakClient | None = None
        self._ble_device = ble_device
        self._name = name
        self._mac = ble_device.address
        self.entity_id = generate_entity_id(ENTITY_ID_FORMAT, self._name, [])
        self._is_closed = None
        self._available = None
        self._model = model_from_name(self._ble_device.name)

        _LOGGER.info(f"Initializing BreagleBT Entity: {self.name}, {self._mac}")

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        self.async_on_remove(
            self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP, self.async_will_remove_from_hass
            )
        )
        # schedule immediate refresh of lamp state:
        self.async_schedule_update_ha_state(force_refresh=True)
        try:
            await self.connect()
        except BleakError:
            _LOGGER.debug(f"Exception connecting from {self._mac}", exc_info=True)

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed from hass."""
        _LOGGER.debug("Running async_will_remove_from_hass")
        try:
            await self.disconnect()
        except BleakError:
            _LOGGER.debug(f"Exception disconnecting from {self._mac}", exc_info=True)

    async def connect(self, num_tries: int = 3) -> None:
        if (
            self._client and not self._client.is_connected
        ):  # check the connection has not dropped
            await self.disconnect()
        _LOGGER.debug("Initiating new connection")
        try:
            if self._client:
                await self.disconnect()
            _LOGGER.debug(f"Connecting now to {self._ble_device}:...")
            self._client = await establish_connection(
                BleakClient,
                device=self._ble_device,
                name=self._mac,
                disconnected_callback=self.diconnected_cb,
                max_attempts=4,
            )
            _LOGGER.debug(
                f"Client used is: {self._client}. Backend is {self._client._backend}"
            )
            _LOGGER.debug(f"Connected: {self._client.is_connected}")
            self._available = True
            self.async_write_ha_state()
        except BleakError as err:
            _LOGGER.error(f"Connection: BleakError: {err}")

    async def disconnect(self) -> None:
        if self._client is None:
            return
        try:
            _LOGGER.debug("Disconnecting from device")
            await self._client.disconnect()
        except BleakError as err:
            _LOGGER.error(f"Disconnection: BleakError: {err}")
        self._available = False
        self.async_write_ha_state()

    def diconnected_cb(self, client: BaseBleakClient) -> None:
        _LOGGER.debug(f"Disconnected CB from client {client}")
        self._available = False
        self.async_write_ha_state()

    @property
    def should_poll(self) -> bool:
        """Polling needed for a updating status."""
        return True

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {
                # Serial numbers are unique identifiers within a specific domain
                (DOMAIN, self.unique_id)
            },
            "name": self._name,
            "manufacturer": "Limoss",
            "model": self._model,
        }

    @property
    def unique_id(self) -> str:
        """Return the unique id of the lift."""
        return self._mac

    @property
    def available(self) -> bool:
        return self._available

    @property
    def name(self) -> str:
        """Return the name of the lift if any."""
        return self._name

    @property
    def is_closed(self) -> bool:
        """Return true if lift is closed."""
        return self._is_closed

    @property
    def supported_features(self) -> int:
        """Flag supported features."""
        return SUPPORT_TVLIFT

    async def async_update(self) -> None:
        # Note, update should only start fetching,
        # followed by asynchronous updates through notifications.
        if not self._available:
            _LOGGER.debug("Reconnecting to Lift")
            await self.connect()

    async def send_cmd(self, bits: bytes) -> bool:
        if not self._available:
            await self.connect()
        try:
            await self._client.write_gatt_char(CONTROL_UUID, bytearray(bits))
            return True
        except BleakError as err:
            _LOGGER.error(f"Send Cmd: BleakError: {err}")
        return False

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close cover."""
        _LOGGER.debug("Close cover")
        await self.send_cmd(bits=bytes.fromhex("DDA212A0446A3DBFC7A2"))
        self._is_closed = True
        self.async_write_ha_state()

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open cover."""
        _LOGGER.debug("Open cover")
        await self.send_cmd(bits=bytes.fromhex("DD29CCEA3FEE319C540A"))
        self._is_closed = False
        self.async_write_ha_state()
