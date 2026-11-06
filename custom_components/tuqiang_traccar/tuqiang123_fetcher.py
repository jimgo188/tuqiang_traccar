"""Tuqiang123 (www.tuqiang123.com / www.tuqiangol.com) data fetcher."""
from __future__ import annotations

import logging

from .fetcher_base import ApiError, BaseFetcher
from .util import parse_hb_time, to_float

_LOGGER = logging.getLogger(__name__)

TUQIANG123_API_HOST = "https://www.tuqiang123.com"
# 途强在线新站，接口与 tuqiang123 完全一致，仅域名不同
TUQIANGOL_API_HOST = "https://www.tuqiangol.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)


class Tuqiang123Fetcher(BaseFetcher):
    """Fetch device positions from Tuqiang123 / Tuqiangol platform."""

    def __init__(self, username: str, password: str, api_host: str = TUQIANG123_API_HOST) -> None:
        super().__init__(username, password)
        self.api_host = (api_host or TUQIANG123_API_HOST).rstrip("/")
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.userid = None
        self.usertype = None

    @property
    def logged_in(self) -> bool:
        return bool(self.userid)

    def _reset_auth(self) -> None:
        self.userid = None
        self.usertype = None
        self.session.cookies.clear()

    @staticmethod
    def _encode(code: str) -> str:
        return "|".join(str(ord(c)) for c in code)

    def _do_login(self) -> bool:
        self.session.cookies.clear()
        result = self._post(f"{self.api_host}/api/regdc", {
            "ver": "1",
            "method": "login",
            "account": self.username,
            "password": self._encode(self.password),
            "language": "zh",
        })
        if result.get("code") != 0:
            raise ApiError(result.get("msg") or "login rejected")
        info = self._post(f"{self.api_host}/customer/getProviderList")
        self.userid = info["data"]["user"]["userId"]
        self.usertype = info["data"]["user"]["type"]
        _LOGGER.info("Tuqiang123 login successful, userid=%s", self.userid)
        return True

    def _fetch_device_list(self) -> dict:
        result = self._post(f"{self.api_host}/device/list", {
            "dateType": "activation",
            "equipment.userId": self.userid,
        })
        if result.get("code") != 0:
            raise ApiError(result.get("msg") or "get device list failed")
        devices = {}
        for item in result["data"]["result"]:
            imei = item["imei"]
            devices[imei] = item.get("carNumber") or item.get("deviceModel") or imei
            self.device_raw[imei] = item
        return devices

    def _fetch_position(self, imei: str) -> dict | None:
        result = self._post(f"{self.api_host}/console/refresh", {
            "choiceUserId": self.userid,
            "normalImeis": str(imei),
            "userType": self.usertype,
            "followImeis": "",
            "userId": self.userid,
            "stock": "2",
        })
        if result.get("code") != 0:
            raise ApiError(result.get("msg") or "get position failed")
        normal = result["data"]["normalList"]
        if not normal:
            self.last_error = "device not in result list"
            return None
        data = normal[0]
        if data.get("lat") in (None, "") or data.get("lng") in (None, ""):
            self.last_error = "no position data yet"
            return None
        return {
            "imei": imei,
            "latitude": float(data["lat"]),
            "longitude": float(data["lng"]),
            "speed": to_float(data.get("speed")),
            "course": to_float(data.get("direction")),
            "timestamp": parse_hb_time(data.get("hbTime")),
            "acc": data.get("acc") == "1",
            "status": data.get("status"),
            "raw": data,
        }
