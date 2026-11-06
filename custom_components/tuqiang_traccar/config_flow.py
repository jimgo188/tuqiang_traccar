"""配置流程：选平台 → 登录 → 选设备 → 填 Traccar。"""
from __future__ import annotations

import logging

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import AuthError, TuqiangError, create_client
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
    MAX_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
    PLATFORM_TUQIANG123,
    PLATFORM_TUQIANGNET,
    PLATFORM_TUQIANGOL,
    SPEED_UNITS,
    TRACCAR_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

PLATFORM_LABELS = {
    PLATFORM_TUQIANGOL: "途强在线新版 (tuqiangol.com)",
    PLATFORM_TUQIANG123: "途强在线 (tuqiang123.com)",
    PLATFORM_TUQIANGNET: "途强物联 (tuqiang.net)",
}

INTERVAL = vol.All(vol.Coerce(int), vol.Range(min=MIN_UPDATE_INTERVAL, max=MAX_UPDATE_INTERVAL))


async def _async_login(hass, platform: str, username: str, password: str):
    """登录并取设备列表，返回 (client, devices, error)。"""
    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar(unsafe=True))
    client = create_client(session, platform, username, password)
    try:
        await client.async_login()
        devices = await client.async_get_devices()
    except AuthError:
        await session.close()
        return None, {}, "invalid_auth"
    except TuqiangError:
        await session.close()
        return None, {}, "cannot_connect"
    except Exception:  # noqa: BLE001 - 平台偶尔返回奇怪结构
        await session.close()
        _LOGGER.exception("登录途强时出现未预期的错误")
        return None, {}, "unknown"
    if not devices:
        await session.close()
        return None, {}, "no_devices"
    return client, devices, None


async def _async_probe_traccar(hass, url: str) -> bool:
    """确认 Traccar 的接入端口有响应（不发送假定位，不会产生设备）。"""
    session = async_create_clientsession(hass)
    try:
        async with session.get(
            url.rstrip("/") + "/", timeout=aiohttp.ClientTimeout(total=TRACCAR_TIMEOUT)
        ) as resp:
            return resp.status < 500
    except (aiohttp.ClientError, TimeoutError):
        return False
    finally:
        await session.close()


class TuqiangTraccarConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """初次添加。"""

    VERSION = 1

    def __init__(self) -> None:
        self._platform: str | None = None
        self._username: str | None = None
        self._password: str | None = None
        self._devices: dict[str, str] = {}
        self._session = None
        self._reauth_entry = None

    async def _async_cleanup(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            self._platform = user_input[CONF_PLATFORM]
            return await self.async_step_auth()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_PLATFORM, default=PLATFORM_TUQIANGOL): vol.In(PLATFORM_LABELS)}),
        )

    async def async_step_auth(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input["username"].strip()
            client, devices, error = await _async_login(
                self.hass, self._platform, username, user_input["password"]
            )
            if error is None:
                self._username = username
                self._password = user_input["password"]
                self._devices = devices
                self._session = client.session
                return await self.async_step_device_select()
            errors["base"] = error

        return self.async_show_form(
            step_id="auth",
            data_schema=vol.Schema({vol.Required("username"): str, vol.Required("password"): str}),
            errors=errors,
        )

    async def async_step_device_select(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_DEVICES):
                errors[CONF_DEVICES] = "no_device_selected"
            else:
                await self._async_cleanup()
                return await self.async_step_traccar_config(selected=user_input[CONF_DEVICES])

        options = {imei: f"{name} · {imei}" for imei, name in self._devices.items()}
        return self.async_show_form(
            step_id="device_select",
            data_schema=vol.Schema({vol.Required(CONF_DEVICES, default=list(options)): cv.multi_select(options)}),
            errors=errors,
        )

    async def async_step_traccar_config(self, user_input=None, selected=None):
        errors: dict[str, str] = {}
        if selected is not None:
            self._selected = selected
        if user_input is not None:
            url = user_input[CONF_TRACCAR_URL].strip().rstrip("/")
            if not url.startswith(("http://", "https://")):
                errors[CONF_TRACCAR_URL] = "invalid_url"
            elif not await _async_probe_traccar(self.hass, url):
                errors[CONF_TRACCAR_URL] = "cannot_connect_traccar"
            else:
                # unique_id 带上服务器地址：同一账号可以转发到不同的 Traccar
                await self.async_set_unique_id(f"{self._platform}_{self._username}_{url}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"途强转Traccar ({self._username})",
                    data={
                        CONF_PLATFORM: self._platform,
                        "username": self._username,
                        "password": self._password,
                        CONF_DEVICES: self._selected,
                        CONF_TRACCAR_URL: url,
                        CONF_DEVICE_PREFIX: user_input.get(CONF_DEVICE_PREFIX, ""),
                        CONF_UPDATE_INTERVAL: user_input[CONF_UPDATE_INTERVAL],
                        CONF_HEARTBEAT: bool(user_input.get(CONF_HEARTBEAT, DEFAULT_HEARTBEAT)),
                        CONF_SPEED_UNIT: user_input.get(CONF_SPEED_UNIT, DEFAULT_SPEED_UNIT),
                        CONF_SEND_BATTERY: user_input.get(CONF_SEND_BATTERY, DEFAULT_SEND_BATTERY),
                        CONF_SEND_ATTRS: user_input.get(CONF_SEND_ATTRS, DEFAULT_SEND_ATTRS),
                        CONF_BATTERY_FIELD: (user_input.get(CONF_BATTERY_FIELD) or "").strip(),
                    },
                )

        schema = vol.Schema({
            vol.Required(CONF_TRACCAR_URL, default=(user_input or {}).get(CONF_TRACCAR_URL, "")): str,
            vol.Optional(CONF_DEVICE_PREFIX, default=(user_input or {}).get(CONF_DEVICE_PREFIX, "")): str,
            vol.Optional(CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): INTERVAL,
            vol.Optional(CONF_SPEED_UNIT, default=DEFAULT_SPEED_UNIT): vol.In(SPEED_UNITS),
            vol.Optional(CONF_HEARTBEAT, default=DEFAULT_HEARTBEAT): bool,
            vol.Optional(CONF_SEND_BATTERY, default=DEFAULT_SEND_BATTERY): bool,
            vol.Optional(CONF_SEND_ATTRS, default=DEFAULT_SEND_ATTRS): bool,
            vol.Optional(CONF_BATTERY_FIELD, default=DEFAULT_BATTERY_FIELD): str,
        })
        return self.async_show_form(
            step_id="traccar_config", data_schema=schema, errors=errors,
            description_placeholders={"count": str(len(self._selected))},
        )

    # ---- 重新认证 ----
    async def async_step_reauth(self, entry_data):
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        self._platform = entry_data.get(CONF_PLATFORM)
        self._username = entry_data.get("username")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors: dict[str, str] = {}
        entry = self._reauth_entry
        if user_input is not None and entry is not None:
            client, _, error = await _async_login(
                self.hass, self._platform, self._username, user_input["password"]
            )
            if error is None:
                await client.session.close()
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, "password": user_input["password"]}
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required("password"): str}),
            description_placeholders={"username": self._username or ""},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return TuqiangTraccarOptionsFlow(config_entry)


def _as_flag(value) -> bool:
    """老配置里心跳可能是秒数。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    return str(value).strip().lower() in ("1", "true", "yes", "on")


class TuqiangTraccarOptionsFlow(config_entries.OptionsFlow):
    """选项：可重新选设备、改间隔和电量。"""

    def __init__(self, config_entry) -> None:
        self._entry = config_entry
        self._devices: dict[str, str] = {}

    def _current(self, key, default):
        return self._entry.options.get(key, self._entry.data.get(key, default))

    async def async_step_init(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            data = dict(user_input)
            data[CONF_BATTERY_FIELD] = (data.get(CONF_BATTERY_FIELD) or "").strip()
            return self.async_create_entry(title="", data=data)

        devices = {imei: imei for imei in self._entry.data.get(CONF_DEVICES, [])}
        try:
            client, fetched, error = await _async_login(
                self.hass,
                self._entry.data[CONF_PLATFORM],
                self._entry.data["username"],
                self._entry.data["password"],
            )
            if error is None:
                devices = {imei: f"{name} · {imei}" for imei, name in fetched.items()}
                await client.session.close()
            else:
                errors["base"] = error
        except Exception:  # noqa: BLE001 - 拿不到列表就沿用原有设备
            _LOGGER.debug("选项里刷新设备列表失败，沿用原设备")

        selected = self._entry.data.get(CONF_DEVICES, [])
        default_devices = [imei for imei in selected if imei in devices]
        missing = [imei for imei in selected if imei not in devices]
        if missing:
            _LOGGER.warning("这些设备已不在账号下，选项里不再显示：%s", missing)
        if not default_devices:
            default_devices = list(devices)

        schema = vol.Schema({
            vol.Optional(CONF_DEVICES, default=default_devices): cv.multi_select(devices),
            vol.Optional(CONF_DEVICE_PREFIX, default=self._current(CONF_DEVICE_PREFIX, "")): str,
            vol.Optional(
                CONF_UPDATE_INTERVAL, default=self._current(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
            ): INTERVAL,
            vol.Optional(
                CONF_SPEED_UNIT, default=self._current(CONF_SPEED_UNIT, DEFAULT_SPEED_UNIT)
            ): vol.In(SPEED_UNITS),
            vol.Optional(
                CONF_HEARTBEAT, default=_as_flag(self._current(CONF_HEARTBEAT, DEFAULT_HEARTBEAT))
            ): bool,
            vol.Optional(
                CONF_SEND_BATTERY, default=self._current(CONF_SEND_BATTERY, DEFAULT_SEND_BATTERY)
            ): bool,
            vol.Optional(
                CONF_SEND_ATTRS, default=self._current(CONF_SEND_ATTRS, DEFAULT_SEND_ATTRS)
            ): bool,
            vol.Optional(
                CONF_BATTERY_FIELD, default=self._current(CONF_BATTERY_FIELD, DEFAULT_BATTERY_FIELD)
            ): str,
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
