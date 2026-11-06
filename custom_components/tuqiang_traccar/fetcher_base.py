"""Common logic for Tuqiang fetchers: timeouts, re-login, locking."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

import requests

from .util import REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)

LOGIN_RETRY_INTERVAL = 20   # 登录失败后，至少间隔多少秒再重试
RELOGIN_MIN_AGE = 60        # 刚登录成功不足这个秒数时，请求失败不触发重新登录


class ApiError(Exception):
    """平台返回 code != 0 或响应不是预期的 JSON。"""


class BaseFetcher:
    """Base class: serialized requests, timeout and automatic re-login."""

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.lock = threading.RLock()  # requests.Session 不是线程安全的，串行化访问
        self.device_raw: dict[str, Any] = {}
        # "invalid_auth" / "cannot_connect" / "bad_data" / 其它错误文本 / None
        self.last_error: str | None = None
        self._last_try: float | None = None
        self._last_ok: float | None = None

    # ---- 子类实现 ----
    @property
    def logged_in(self) -> bool:
        raise NotImplementedError

    def _reset_auth(self) -> None:
        raise NotImplementedError

    def _do_login(self) -> bool:
        raise NotImplementedError

    # ---- 公共接口 ----
    def _post(self, url: str, data: dict | None = None) -> dict:
        resp = self.session.post(url, data=data, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def login(self) -> bool:
        with self.lock:
            now = time.monotonic()
            self._last_try = now
            self.last_error = None
            try:
                ok = self._do_login()
            except ApiError as err:
                self.last_error = "invalid_auth"
                _LOGGER.error("%s login failed: %s", self.__class__.__name__, err)
                return False
            except (requests.RequestException, ValueError) as err:
                self.last_error = "cannot_connect"
                _LOGGER.error("%s login error: %s", self.__class__.__name__, err)
                return False
            except (KeyError, IndexError, TypeError) as err:
                self.last_error = "bad_data"
                _LOGGER.error("%s login: unexpected response (%s)", self.__class__.__name__, err)
                return False
            if ok:
                self._last_ok = time.monotonic()
            return ok

    def _ensure_login(self) -> bool:
        if self.logged_in:
            return True
        if self._last_try is not None and time.monotonic() - self._last_try < LOGIN_RETRY_INTERVAL:
            return False  # 刚失败过，别每台设备都去重试一次登录
        return self.login()

    def call(self, func: Callable[..., Any], *args: Any) -> Any:
        """执行一次业务请求；会话失效时自动重新登录并重试一次。失败返回 None。"""
        with self.lock:
            for _ in range(2):
                if not self._ensure_login():
                    return None
                try:
                    self.last_error = None
                    return func(*args)
                except (ApiError, ValueError) as err:
                    # 会话过期时常见：code != 0，或返回登录页（非 JSON）
                    self.last_error = str(err) or "api_error"
                    _LOGGER.debug("Request failed: %s", err)
                    age = time.monotonic() - (self._last_ok or 0)
                    if age < RELOGIN_MIN_AGE:
                        return None  # 刚登录过，多半不是会话问题
                    self._reset_auth()
                except requests.RequestException as err:
                    self.last_error = "cannot_connect"
                    _LOGGER.debug("Network error: %s", err)
                    return None
                except (KeyError, IndexError, TypeError) as err:
                    self.last_error = f"bad_data: {err!r}"
                    _LOGGER.debug("Unexpected response: %r", err)
                    return None
            return None

    def get_device_position(self, imei: str) -> dict | None:
        return self.call(self._fetch_position, imei)

    def get_device_list(self) -> dict:
        result = self.call(self._fetch_device_list)
        return result or {}

    def _fetch_position(self, imei: str) -> dict | None:
        raise NotImplementedError

    def _fetch_device_list(self) -> dict:
        raise NotImplementedError
