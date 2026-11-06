"""途强平台的异步客户端（aiohttp，不占用 HA 的线程池）。

支持三个站点：

* www.tuqiangol.com   途强在线新版（账号密码登录 /api/regdc，Cookie 会话）
* www.tuqiang123.com  途强在线（同上，接口一致）
* www.tuqiang.net     途强物联（token 登录）

对外只有三个方法：``async_login``、``async_get_devices``、``async_get_positions``。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import aiohttp

from .const import (
    API_HOSTS,
    LOGIN_COOLDOWN,
    PLATFORM_TUQIANG123,
    PLATFORM_TUQIANGNET,
    PLATFORM_TUQIANGOL,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from .util import normalize_position, seconds_since

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)

# 平台用它表示账号类问题；这些错误值得提示用户重新认证
AUTH_CODES = {21001, 21002, 21004, 21005}


class TuqiangError(Exception):
    """途强接口的通用错误。"""


class AuthError(TuqiangError):
    """账号或密码被拒绝。"""


class SessionExpired(TuqiangError):
    """会话失效（接口返回登录页 / 未登录）。"""


class ApiError(TuqiangError):
    """平台返回了非 0 的业务码。"""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class _BaseClient:
    """公共部分：会话、登录节流、设备与定位的统一接口。"""

    platform = ""

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self.session = session
        self.username = username
        self.password = password
        self.host = API_HOSTS.get(self.platform, "")
        self.device_raw: dict[str, dict] = {}
        self.last_error: str | None = None
        self._last_login_attempt: float | None = None

    # ---- 子类实现 ----
    @property
    def logged_in(self) -> bool:
        raise NotImplementedError

    async def _do_login(self) -> None:
        raise NotImplementedError

    async def _fetch_devices(self) -> list[dict]:
        raise NotImplementedError

    async def _fetch_positions(self, imeis: list[str]) -> dict[str, dict]:
        raise NotImplementedError

    # ---- HTTP ----
    async def _request(self, path: str, data: dict | None = None, *, method: str = "post") -> dict:
        """发一次请求并返回 JSON；登录页 / 非 JSON 视为会话失效。"""
        url = f"{self.host}{path}"
        headers = {"User-Agent": USER_AGENT, "X-Requested-With": "XMLHttpRequest"}
        try:
            async with self.session.request(
                method, url, data=data, headers=headers, timeout=TIMEOUT, allow_redirects=False
            ) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    raise SessionExpired(f"redirected to {resp.headers.get('Location', '?')}")
                if resp.status >= 400:
                    raise ApiError(f"HTTP {resp.status}", resp.status)
                text = await resp.text()
        except aiohttp.ClientError as err:
            raise TuqiangError(f"cannot connect: {err}") from err
        except TimeoutError as err:
            raise TuqiangError("request timed out") from err

        try:
            payload = json.loads(text)
        except ValueError as err:
            raise SessionExpired("response is not JSON (probably the login page)") from err
        if not isinstance(payload, dict):
            raise ApiError("unexpected payload")
        return payload

    @staticmethod
    def _check(payload: dict, what: str) -> dict:
        code = payload.get("code")
        if code == 0:
            return payload
        message = str(payload.get("msg") or f"{what} failed")
        if code in AUTH_CODES:
            raise SessionExpired(message)
        raise ApiError(message, code if isinstance(code, int) else None)

    # ---- 登录 ----
    async def async_login(self) -> None:
        """登录；密码错误抛 AuthError，网络问题抛 TuqiangError。"""
        self._last_login_attempt = time.monotonic()
        self.last_error = None
        try:
            await self._do_login()
        except AuthError as err:
            self.last_error = "invalid_auth"
            _LOGGER.error("途强登录被拒绝（%s）：%s", self.platform, err)
            raise
        except SessionExpired as err:
            self.last_error = "cannot_connect"
            _LOGGER.error("途强登录异常（%s）：%s", self.platform, err)
            raise TuqiangError(str(err)) from err
        except (TuqiangError, KeyError, TypeError) as err:
            self.last_error = "cannot_connect"
            _LOGGER.error("途强登录失败（%s）：%s", self.platform, err)
            raise TuqiangError(str(err)) from err
        _LOGGER.info("途强登录成功（%s，用户 %s）", self.platform, self.username)

    async def _ensure_login(self) -> None:
        if self.logged_in:
            return
        if seconds_since(self._last_login_attempt) < LOGIN_COOLDOWN:
            raise TuqiangError("login attempted too recently")
        await self.async_login()

    async def _call(self, func, *args):
        """执行一次业务请求，会话过期时重新登录并重试一次。"""
        await self._ensure_login()
        try:
            return await func(*args)
        except SessionExpired as err:
            _LOGGER.debug("会话失效，重新登录后重试：%s", err)
            self._reset_auth()
            self._last_login_attempt = None
            await self.async_login()
            return await func(*args)

    def _reset_auth(self) -> None:
        raise NotImplementedError

    # ---- 对外接口 ----
    async def async_get_devices(self) -> dict[str, str]:
        """返回 {imei: 名称}，同时缓存设备条目（电量回退来源）。"""
        items = await self._call(self._fetch_devices)
        devices: dict[str, str] = {}
        for item in items or []:
            imei = str(item.get("imei") or "").strip()
            if not imei:
                continue
            name = (
                item.get("carNumber")
                or item.get("deviceName")
                or item.get("plateNumber")
                or item.get("deviceModel")
                or imei
            )
            devices[imei] = str(name)
            self.device_raw[imei] = item
        return devices

    async def async_get_positions(self, imeis: list[str]) -> dict[str, dict]:
        """一次取回多台设备的定位（平台本身支持逗号分隔的 IMEI 列表）。"""
        if not imeis:
            return {}
        raw = await self._call(self._fetch_positions, list(imeis))
        positions: dict[str, dict] = {}
        for imei, item in (raw or {}).items():
            pos = normalize_position(item)
            if pos is not None:
                positions[imei] = pos
        return positions


class TuqiangOlClient(_BaseClient):
    """途强在线 / 途强在线新版：Cookie 会话 + 网页版私有接口。"""

    platform = ""

    def __init__(self, session, username, password, platform: str) -> None:
        self.platform = platform
        super().__init__(session, username, password)
        self.userid: str | None = None
        self.usertype: str | None = None

    @property
    def logged_in(self) -> bool:
        return bool(self.userid)

    def _reset_auth(self) -> None:
        self.userid = None
        self.usertype = None
        self.session.cookie_jar.clear()

    @staticmethod
    def encode_password(password: str) -> str:
        """网页版把密码每个字符转成 ASCII 码，用 | 连接。"""
        return "|".join(str(ord(char)) for char in password)

    async def _do_login(self) -> None:
        self.session.cookie_jar.clear()
        payload = await self._request(
            "/api/regdc",
            {
                "ver": "1",
                "method": "login",
                "account": self.username,
                "password": self.encode_password(self.password),
                "language": "zh",
            },
        )
        code = payload.get("code")
        if code != 0:
            message = str(payload.get("msg") or "登录失败")
            if code in AUTH_CODES or code is None:
                raise AuthError(f"{code}: {message}")
            raise ApiError(message, code)

        info = await self._request("/customer/getProviderList")
        self._check(info, "getProviderList")
        data = info.get("data") or {}
        user = data.get("user") or {}
        self.userid = user.get("userId") or user.get("id")
        self.usertype = user.get("type")
        if not self.userid:
            raise ApiError("登录成功但缺少 userId")

    async def _fetch_devices(self) -> list[dict]:
        payload = await self._request(
            "/device/list",
            {"dateType": "activation", "equipment.userId": self.userid},
        )
        self._check(payload, "device/list")
        data = payload.get("data") or {}
        result = data.get("result") if isinstance(data, dict) else None
        return result or []

    async def _fetch_positions(self, imeis: list[str]) -> dict[str, dict]:
        payload = await self._request(
            "/console/refresh",
            {
                "choiceUserId": self.userid,
                "normalImeis": ",".join(imeis),
                "userType": self.usertype or "",
                "followImeis": "",
                "userId": self.userid,
                "stock": "2",
            },
        )
        self._check(payload, "console/refresh")
        data = payload.get("data") or {}
        items: list[dict] = []
        for key in ("normalList", "followList"):
            value = data.get(key)
            if isinstance(value, list):
                items.extend(item for item in value if isinstance(item, dict))
        out: dict[str, dict] = {}
        for item in items:
            imei = str(item.get("imei") or "").strip()
            if imei:
                out[imei] = item
        return out


class Tuqiang123Client(TuqiangOlClient):
    platform = "tuqiang123.com"


class TuqiangOlNewClient(TuqiangOlClient):
    platform = "tuqiangol.com"


class TuqiangNetClient(_BaseClient):
    """途强物联：token 登录。"""

    platform = "tuqiang.net"

    def __init__(self, session, username, password) -> None:
        super().__init__(session, username, password)
        self.token: str | None = None

    @property
    def logged_in(self) -> bool:
        return bool(self.token)

    def _reset_auth(self) -> None:
        self.token = None

    async def _do_login(self) -> None:
        payload = await self._request(
            "/loginVerification",
            {
                "timeZone": "28800",
                "token": "",
                "userName": self.username,
                "password": self.password,
                "lang": "zh",
            },
        )
        code = payload.get("code")
        if code != 0:
            message = str(payload.get("msg") or "登录失败")
            if code in AUTH_CODES or code is None:
                raise AuthError(f"{code}: {message}")
            raise ApiError(message, code)
        data = payload.get("data") or {}
        self.token = data.get("token")
        if not self.token:
            raise ApiError("登录成功但缺少 token")

    async def _fetch_devices(self) -> list[dict]:
        payload = await self._request("/device/getDeviceList", {"token": self.token})
        self._check(payload, "getDeviceList")
        data = payload.get("data")
        return data if isinstance(data, list) else []

    async def _fetch_positions(self, imeis: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for imei in imeis:
            payload = await self._request("/redis/getGps", {"imei": imei, "token": self.token})
            self._check(payload, "getGps")
            gps = payload.get("data")
            if isinstance(gps, dict) and gps.get("latitude") not in (None, ""):
                # 物联平台用 latitude/longitude，统一成 lat/lng
                out[imei] = {
                    **gps,
                    "lat": gps.get("latitude"),
                    "lng": gps.get("longitude"),
                }
        return out


def create_client(session, platform: str, username: str, password: str) -> _BaseClient:
    if platform == PLATFORM_TUQIANGNET:
        return TuqiangNetClient(session, username, password)
    if platform == PLATFORM_TUQIANG123:
        return Tuqiang123Client(session, username, password, platform=PLATFORM_TUQIANG123)
    return TuqiangOlNewClient(
        session, username, password, platform=platform or PLATFORM_TUQIANGOL
    )
