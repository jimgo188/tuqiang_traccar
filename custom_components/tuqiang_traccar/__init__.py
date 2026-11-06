"""途强 GPS → Traccar。

HA 侧只负责搬运：登录途强、批量拉定位、顺带取电量，再按 Traccar 的 OsmAnd 协议发出去。
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .api import AuthError, TuqiangError, create_client
from .battery import extract_battery
from .const import (
    CONF_BATTERY_FIELD,
    CONF_DEVICE_PREFIX,
    CONF_DEVICES,
    CONF_HEARTBEAT,
    CONF_PLATFORM,
    CONF_SEND_ATTRS,
    CONF_SEND_BATTERY,
    CONF_SPEED_UNIT,
    CONF_TRACCAR_URL,
    CONF_UPDATE_INTERVAL,
    DEFAULT_BATTERY_FIELD,
    DEFAULT_HEARTBEAT,
    DEFAULT_SEND_ATTRS,
    DEFAULT_SEND_BATTERY,
    DEFAULT_SPEED_UNIT,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    HEARTBEAT_SECONDS,
    META_REFRESH_SECONDS,
)
from .traccar import TraccarError, async_send, build_params, signature
from .util import seconds_since

_LOGGER = logging.getLogger(__name__)


def _as_heartbeat_flag(value) -> bool:
    """心跳开关是布尔值；老配置里可能是秒数，>0 就当打开。"""
    if value is None:
        return DEFAULT_HEARTBEAT
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    return str(value).strip().lower() in ("1", "true", "yes", "on")


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """选项变化后重载，让新配置立刻生效。"""
    await hass.config_entries.async_reload(entry.entry_id)


class TuqiangRuntime:
    """一个配置条目的运行状态：定时拉取、去重、上报。"""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, config: dict) -> None:
        self.hass = hass
        self.entry = entry

        self.platform = config[CONF_PLATFORM]
        self.devices: list[str] = list(config.get(CONF_DEVICES) or [])
        self.traccar_url = str(config.get(CONF_TRACCAR_URL) or "").rstrip("/")
        self.prefix = str(config.get(CONF_DEVICE_PREFIX) or "")
        self.interval = int(config.get(CONF_UPDATE_INTERVAL) or DEFAULT_UPDATE_INTERVAL)
        self.heartbeat = _as_heartbeat_flag(config.get(CONF_HEARTBEAT))
        self.heartbeat_seconds = HEARTBEAT_SECONDS
        self.speed_unit = str(config.get(CONF_SPEED_UNIT) or DEFAULT_SPEED_UNIT)
        self.send_battery = bool(config.get(CONF_SEND_BATTERY, DEFAULT_SEND_BATTERY))
        self.send_attrs = bool(config.get(CONF_SEND_ATTRS, DEFAULT_SEND_ATTRS))
        self.battery_field = str(config.get(CONF_BATTERY_FIELD) or DEFAULT_BATTERY_FIELD).strip()

        self._lock = asyncio.Lock()
        self._meta_at: float | None = None
        self._last_sig: dict[str, tuple] = {}
        self._last_ok: dict[str, float] = {}
        self._failing: set[str] = set()

        # 诊断信息
        self.reports = 0
        self.errors = 0
        self.last_error: str | None = None
        self.last_report_at: float | None = None

    @property
    def device_ids(self) -> dict[str, str]:
        """imei → Traccar 设备标识。"""
        return {imei: f"{self.prefix}{imei}" if self.prefix else imei for imei in self.devices}

    async def _async_refresh_meta(self, client) -> None:
        """定期刷新设备列表：机型电量常常只在设备列表里出现。"""
        if not self.send_battery:
            return
        if seconds_since(self._meta_at) < META_REFRESH_SECONDS:
            return
        self._meta_at = time.monotonic()
        try:
            await client.async_get_devices()
        except (AuthError, TuqiangError) as err:
            _LOGGER.debug("刷新设备列表失败（不影响定位）：%s", err)
            self._meta_at = None

    def _battery_for(self, imei: str, position: dict, client) -> dict:
        """先看定位数据，再看设备列表，最后才放弃。"""
        if not self.send_battery:
            return {}
        sources = [position.get("raw") or {}, client.device_raw.get(imei) or {}]
        return extract_battery(sources, self.battery_field or None)

    async def _async_report_one(self, client, session, imei: str, position: dict) -> None:
        device_id = self.device_ids[imei]
        battery = self._battery_for(imei, position, client)
        params = build_params(
            device_id,
            position,
            battery,
            send_battery=self.send_battery,
            send_attrs=self.send_attrs,
            speed_unit=self.speed_unit,
        )

        sig = signature(params)
        unchanged = self._last_sig.get(imei) == sig
        elapsed = seconds_since(self._last_ok.get(imei))

        if unchanged:
            # 位置和电量都没变化：默认不重复上报，但轨迹会因此“断开”。
            # 心跳开关打开时，超过间隔就补一个当前时间的点，让 Traccar 认为设备在线。
            if not self.heartbeat or elapsed < self.heartbeat_seconds:
                _LOGGER.debug("设备 %s 无变化，跳过", imei)
                return
            params["timestamp"] = str(int(time.time()))
            _LOGGER.debug("设备 %s 无变化，按心跳补报", imei)

        try:
            await async_send(session, self.traccar_url, params)
        except TraccarError as err:
            self.errors += 1
            self.last_error = f"{imei}: {err}"
            if imei not in self._failing:
                self._failing.add(imei)
                _LOGGER.warning(
                    "设备 %s 上报 Traccar 失败（%s）：%s", imei, self.traccar_url, err
                )
            return

        if imei in self._failing:
            self._failing.discard(imei)
            _LOGGER.info("设备 %s 上报已恢复", imei)

        self._last_sig[imei] = sig
        self._last_ok[imei] = time.monotonic()
        self.reports += 1
        self.last_report_at = time.time()

        if self.send_battery and battery.get("level") is not None:
            _LOGGER.debug(
                "设备 %s 已上报：%s%%（字段 %s，充电 %s，电压 %s）",
                imei,
                battery["level"],
                battery.get("source"),
                battery.get("charge"),
                battery.get("voltage"),
            )

    async def async_cycle(self, *_: object) -> None:
        """一轮完整的拉取 + 上报。"""
        if self._lock.locked():
            _LOGGER.debug("上一轮还没结束，跳过（间隔 %ss）", self.interval)
            return

        async with self._lock:
            session = self._session
            client = self._client
            await self._async_refresh_meta(client)

            try:
                positions = await client.async_get_positions(self.devices)
            except AuthError as err:
                self.errors += 1
                self.last_error = f"auth: {err}"
                _LOGGER.error("途强账号被拒绝，请重新认证：%s", err)
                return
            except TuqiangError as err:
                self.errors += 1
                self.last_error = f"api: {err}"
                _LOGGER.warning("本轮回获取定位失败：%s", err)
                return

            if not positions:
                _LOGGER.debug("本轮没有拿到任何定位数据")

            for imei in self.devices:
                position = positions.get(imei)
                if position is None:
                    if imei not in self._failing:
                        self._failing.add(imei)
                        _LOGGER.warning("设备 %s 没有可用定位（未上线或坐标为空）", imei)
                    continue
                try:
                    await self._async_report_one(client, session, imei, position)
                except Exception as err:  # noqa: BLE001 - 单台设备出错不影响其它设备
                    self.errors += 1
                    self.last_error = f"{imei}: {err!r}"
                    _LOGGER.exception("设备 %s 处理出错", imei)

    # ---- 生命周期 ----
    async def async_start(self, client, session) -> None:
        self._client = client
        self._session = session
        self.entry.async_create_background_task(
            self.hass, self.async_cycle(), f"{DOMAIN}_first_run"
        )
        self.entry.async_on_unload(
            async_track_time_interval(
                self.hass, self.async_cycle, timedelta(seconds=self.interval)
            )
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """建立集成。"""
    config = {**entry.data, **entry.options}

    if not config.get(CONF_DEVICES):
        _LOGGER.error("没有配置任何设备")
        return False

    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar(unsafe=True))
    client = create_client(
        session, config[CONF_PLATFORM], config["username"], config["password"]
    )

    try:
        await client.async_login()
    except AuthError as err:
        await session.close()
        raise ConfigEntryAuthFailed(f"途强账号认证失败：{err}") from err
    except TuqiangError as err:
        await session.close()
        raise ConfigEntryNotReady(f"暂时连不上途强：{err}") from err

    runtime = TuqiangRuntime(hass, entry, config)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    await runtime.async_start(client, session)

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    _LOGGER.info(
        "途强转 Traccar 已启动：%s 台设备 → %s，间隔 %ss，心跳 %s",
        len(runtime.devices),
        runtime.traccar_url,
        runtime.interval,
        f"开（{runtime.heartbeat_seconds}s）" if runtime.heartbeat else "关",
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载集成（定时器和监听器由 async_on_unload 清理）。"""
    runtime: TuqiangRuntime | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime is not None:
        try:
            await runtime._session.close()
        except Exception:  # noqa: BLE001 - 关会话失败不影响卸载
            pass
    return True
