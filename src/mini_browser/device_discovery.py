"""Background ADB enumeration and transient phone tiles; no GTK dependencies."""

import shutil
import time
from dataclasses import replace

from .devices import (
    AndroidConnection,
    Device,
    DeviceError,
    Transport,
    android_device_id,
    parse_android_devices,
    read_android_identity,
)

STATES = {
    "device": "Connected",
    "unauthorized": "Allow USB debugging",
    "offline": "Offline",
}


def preferred_connections(connected):
    """One reachable transport per physical phone, preferring authorized USB."""
    groups = {}
    for raw in connected:
        connection = AndroidConnection(*raw)
        key = connection.identity or connection.address
        rank = (
            {"device": 0, "unauthorized": 1, "offline": 2}.get(connection.state, 3),
            not connection.usb,
            connection.address != connection.identity if connection.identity else False,
            connection.address,
        )
        previous = groups.get(key)
        if previous is None or rank < previous[0]:
            groups[key] = rank, connection
    return [item[1] for item in groups.values()]


def device_tiles(saved, connected):
    """Merge saved phones with current ADB devices without persisting detection."""
    connected = [AndroidConnection(*record) for record in connected]
    aliases = {record.address: record.identity or record.address for record in connected}
    preferred = {
        record.identity or record.address: record for record in preferred_connections(connected)
    }
    result = []
    seen = set()
    for device in saved:
        if device.kind == "android":
            key = device.identity or aliases.get(device.address, device.address)
            if key in seen:
                continue
            seen.add(key)
            connection = preferred.get(key)
            if connection:
                device = replace(
                    device,
                    address=connection.address,
                    identity=device.identity or connection.identity,
                )
            subtitle = (
                STATES.get(connection.state, "Disconnected") if connection else "Disconnected"
            )
        else:
            subtitle = "iPhone"
        result.append((device, subtitle))
    for key, connection in preferred.items():
        if key not in seen:
            address, name, state, identity = connection[:4]
            name = "".join(char for char in name if char.isprintable()).strip()[:48] or "Android"
            device = Device(android_device_id(key), "android", name, address, identity)
            result.append((device, STATES.get(state, "Offline")))
    return result


class AndroidDiscovery(Transport):
    def __init__(self, changed, *, interval=3):
        super().__init__(lambda *_: None, lambda: None)
        self.changed = changed
        self.interval = interval
        self._identities = {}

    def scan(self, adb):
        output = self.command([adb, "devices", "-l"], timeout=5)
        records = parse_android_devices(output)
        usb = {
            fields[0]
            for line in output.splitlines()
            if (fields := line.split()) and any(field.startswith("usb:") for field in fields[2:])
        }
        present = {address for address, _name, _state in records}
        self._identities = {
            address: value for address, value in self._identities.items() if address in present
        }
        result = []
        for address, name, state in records:
            identity, checked = self._identities.get(address, ("", 0))
            if state == "device" and time.monotonic() - checked > 30:
                identity = read_android_identity(self.command, adb, address)
                self._identities[address] = identity, time.monotonic()
            elif state != "device":
                self._identities[address] = identity, 0
            result.append(AndroidConnection(address, name, state, identity, address in usb))
        return tuple(result)

    def run(self):
        previous = None
        while not self.cancelled.is_set():
            adb = shutil.which("adb")
            records, error = (), None
            if not adb:
                error = "Install adb (Android platform tools) to detect Android phones."
            else:
                try:
                    records = self.scan(adb)
                except (DeviceError, OSError):
                    error = "Could not detect Android phones. Check Android platform tools."
            current = records, error
            if current != previous and not self.cancelled.is_set():
                self.changed(records, error)
                previous = current
            self.cancelled.wait(self.interval)
