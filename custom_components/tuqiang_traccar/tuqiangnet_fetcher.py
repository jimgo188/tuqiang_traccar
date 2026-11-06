"""TuqiangNet (www.tuqiang.net) data fetcher."""
from __future__ import annotations

import logging

from .fetcher_base import ApiError, BaseFetcher
from .util import parse_hb_time, to_float

_LOGGER = logging.getLogger(__name__)

TUQIANGNET_API_HOST = "https://www.tuqiang.net"


class TuqiangNetFetcher(BaseFetcher):
    """Fetch device positions from TuqiangNet platform."""

    def __init__(self, username: str, password: str) -> None:
        super().__init__(username, password)
        self.token: str | None = None
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        })

    @property
    def logged_in(self) -> bool:
        return bool(self.token)

    def _reset_auth(self) -> None:
        self.token = None

    def _do_login(self) -> bool:
        result = self._post(f"{TUQIANGNET_API_HOST}/loginVerification", {
            "timeZone": "28800",
            "token": "",
            "userName": self.username,
            "password": self.password,
            "lang": "zh",
        })
        if result.get("code") != 0:
            raise ApiError(result.get("msg") or "login rejected")
        self.token = result["data"]["token"]
        _LOGGER.info("TuqiangNet login successful")
        return True

    def _fetch_device_list(self) -> dict:
        result = self._post(f"{TUQIANGNET_API_HOST}/device/getDeviceList", {"token": self.token})
        if result.get("code") != 0:
            raise ApiError(result.get("msg") or "get device list failed")
        devices = {}
        for item in result["data"]:
            imei = item["imei"]
            devices[imei] = item.get("deviceName") or item.get("carNumber") or imei
            self.device_raw[imei] = item
        return devices

    def _fetch_position(self, imei: str) -> dict | None:
        result = self._post(f"{TUQIANGNET_API_HOST}/redis/getGps", {"imei": imei, "token": self.token})
        if result.get("code") != 0:
            raise ApiError(result.get("msg") or "get position failed")
        gps = result.get("data")
        if not isinstance(gps, dict) or gps.get("latitude") in (None, "") or gps.get("longitude") in (None, ""):
            self.last_error = "no position data yet"
            return None
        return {
            "imei": imei,
            "latitude": float(gps["latitude"]),
            "longitude": float(gps["longitude"]),
            "speed": to_float(gps.get("speed")),
            "course": to_float(gps.get("direction")),
            "timestamp": parse_hb_time(gps.get("hbTime")),
            "acc": gps.get("acc") == "1",
            "status": gps.get("status"),
            "raw": gps,
        }
