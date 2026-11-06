"""诊断信息：出问题时把这份文件发出去就够了。"""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_PASSWORD, DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    data = dict(entry.data)
    if CONF_PASSWORD in data:
        data[CONF_PASSWORD] = "**REDACTED**"

    payload: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "data": data,
            "options": dict(entry.options),
            "version": entry.version,
        },
        "runtime": None,
    }

    if runtime is not None:
        payload["runtime"] = {
            "platform": runtime.platform,
            "traccar_url": runtime.traccar_url,
            "interval": runtime.interval,
            "heartbeat": runtime.heartbeat,
            "heartbeat_seconds": runtime.heartbeat_seconds,
            "speed_unit": runtime.speed_unit,
            "send_battery": runtime.send_battery,
            "send_attrs": runtime.send_attrs,
            "battery_field": runtime.battery_field,
            "devices": runtime.devices,
            "device_ids": runtime.device_ids,
            "reports_sent": runtime.reports,
            "errors": runtime.errors,
            "last_error": runtime.last_error,
            "last_report_at": runtime.last_report_at,
            "failing_devices": sorted(runtime._failing),
        }

    return payload
