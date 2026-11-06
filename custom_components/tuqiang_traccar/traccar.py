"""把整理好的定位组装成 Traccar 的 OsmAnd 报文，并发出去。

OsmAnd 协议（Traccar 官方文档）：

    id, lat, lon, timestamp, speed, bearing, altitude, accuracy, batt, charge

* speed 默认单位是节（knots），所以要从 km/h 换算
* batt 是 0-100 的电量百分比，charge 只接受 true / false
* 其它参数（这里的 ignition、voltage、tuqiang_status）会存成自定义属性
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .const import (
    SPEED_UNIT_KMH,
    SPEED_UNIT_KNOTS,
    TRACCAR_RETRIES,
    TRACCAR_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

KNOTS_PER_KMH = 0.5399568
TIMEOUT = aiohttp.ClientTimeout(total=TRACCAR_TIMEOUT)


class TraccarError(Exception):
    """上报失败。"""


def build_params(
    device_id: str,
    position: dict,
    battery: dict[str, Any] | None,
    *,
    send_battery: bool = True,
    send_attrs: bool = True,
    speed_unit: str = SPEED_UNIT_KNOTS,
) -> dict[str, str]:
    """把一条定位转成 Traccar 接受的参数表。

    途强的速度是 km/h；Traccar 的 OsmAnd 协议默认收节，所以默认换算，
    除非用户在选项里明确说服务器那边用的是 km/h。
    """
    speed = position.get("speed", 0.0)
    if speed_unit != SPEED_UNIT_KMH:
        speed = speed * KNOTS_PER_KMH

    params: dict[str, str] = {
        "id": device_id,
        "lat": f"{position['lat']:.6f}",
        "lon": f"{position['lon']:.6f}",
        "timestamp": str(int(position["timestamp"])),
        "speed": f"{speed:.2f}",
        "bearing": f"{position.get('course', 0.0):.1f}",
    }

    accuracy = position.get("accuracy")
    if accuracy:
        params["accuracy"] = f"{accuracy:.1f}"

    altitude = position.get("altitude")
    if altitude is not None:
        params["altitude"] = f"{altitude:.1f}"

    if send_battery and battery:
        if battery.get("level") is not None:
            params["batt"] = f"{battery['level']:.1f}"
        if battery.get("charge") is not None:
            params["charge"] = "true" if battery["charge"] else "false"
        if send_attrs and battery.get("voltage") is not None:
            params["voltage"] = f"{battery['voltage']:.2f}"

    if send_attrs:
        params["ignition"] = "true" if position.get("acc") else "false"
        status = position.get("status")
        if status not in (None, ""):
            params["tuqiang_status"] = str(status)

    return params


def signature(params: dict[str, str]) -> tuple:
    """内容指纹：忽略时间戳，用来判断这条数据是否和上次一样。"""
    return tuple(sorted((key, str(value)) for key, value in params.items() if key != "timestamp"))


async def async_send(
    session: aiohttp.ClientSession,
    base_url: str,
    params: dict[str, str],
    *,
    retries: int = TRACCAR_RETRIES,
) -> None:
    """上报到 Traccar；4xx 直接失败，网络错误和 5xx 退避重试。"""
    url = base_url.rstrip("/") + "/"
    last_error = "unknown"
    for attempt in range(1, retries + 1):
        try:
            async with session.post(url, data=params, timeout=TIMEOUT) as resp:
                if 200 <= resp.status < 300:
                    return
                if 400 <= resp.status < 500:
                    body = (await resp.text())[:200].strip()
                    raise TraccarError(f"HTTP {resp.status} {body}".strip())
                last_error = f"HTTP {resp.status}"
        except TraccarError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            last_error = str(err) or err.__class__.__name__

        if attempt < retries:
            await asyncio.sleep(attempt * 2)

    raise TraccarError(last_error)
