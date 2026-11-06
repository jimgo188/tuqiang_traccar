"""从途强返回的原始数据里找电池信息。

字段名在不同平台、不同机型上并不统一：

* 途强开放平台（dev-docs.tqiot.com）：powerPercent / charge / voltage
* 途强在线、途强在线新版（tuqiang123.com、tuqiangol.com）：实测为 elecQuantity
* 途强物联（tuqiang.net）：部分机型用 battery / batteryLevel

所以这里用候选键名表做兼容，并允许在集成选项里手动指定字段名（支持 a.b 这样的嵌套路径）。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable

_LOGGER = logging.getLogger(__name__)

# 含义明确就是百分比的字段（按优先级）
PERCENT_KEYS: tuple[str, ...] = (
    "powerPercent",
    "elecQuantity",          # 途强在线 / 途强在线新版实际字段
    "batteryPercent",
    "batteryPercentage",
    "batteryLevel",
    "battery_level",
    "electricQuantity",
    "soc",
    "batt",
    "battery",
    "percent",
)

PERCENT_ONLY_KEYS = frozenset(PERCENT_KEYS) - {"battery", "batt"}

VOLTAGE_KEYS: tuple[str, ...] = (
    "voltage",
    "batteryVoltage",
    "volt",
    "volts",
    "vbat",
    "solarVoltage",
)

CHARGE_KEYS: tuple[str, ...] = (
    "charge",
    "charging",
    "isCharging",
    "chargeStatus",
    "batteryCharging",
)

# 单节锂电池电压区间，用来把电压折算成百分比
_CELL_EMPTY_V = 3.30
_CELL_FULL_V = 4.20

_TRUE_TEXT = {"1", "true", "yes", "on", "charging", "charge", "y", "t"}
_FALSE_TEXT = {"0", "false", "no", "off", "not", "idle", "discharge", "n", "f"}

_MAX_DEPTH = 2


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        match = re.match(r"^[+-]?\d+(?:\.\d+)?", text)  # 兼容 "85%"、"3.9V"
        if match:
            try:
                return float(match.group(0))
            except ValueError:
                return None
    return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value)) if value in (0, 1) else None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_TEXT:
            return True
        if text in _FALSE_TEXT:
            return False
        number = _as_float(text)
        if number in (0.0, 1.0):
            return bool(number)
    return None


def _cell_voltage_to_percent(volts: float) -> float:
    percent = (volts - _CELL_EMPTY_V) / (_CELL_FULL_V - _CELL_EMPTY_V) * 100
    return max(0.0, min(100.0, percent))


def _normalize_percent(number: float) -> float:
    if 0.0 < number < 1.0:  # 部分平台用 0.87 表示 87%
        return round(number * 100, 1)
    return round(number, 1)


def _voltage_from_value(value: Any) -> float | None:
    number = _as_float(value)
    if number is None:
        return None
    if 1000.0 <= number <= 60000.0:  # 毫伏
        number /= 1000.0
    if 0.0 < number <= 60.0:
        return round(number, 2)
    return None


def _interpret(key: str, value: Any) -> tuple[float | None, float | None]:
    """把某个字段解释成 (电量百分比, 电压)。"""
    number = _as_float(value)
    if number is None:
        return None, None

    if key in PERCENT_ONLY_KEYS:
        if 0.0 <= number <= 100.0:
            return _normalize_percent(number), None
        return None, _voltage_from_value(number)

    # 含义模糊的字段（battery / batt）
    if 0.0 < number < 1.0:
        return _normalize_percent(number), None
    is_int = number == int(number)
    if 3.0 <= number <= 4.5 and not is_int:      # 3.87 → 单节电压
        volts = round(number, 2)
        return round(_cell_voltage_to_percent(volts), 1), volts
    if 1000.0 <= number <= 6000.0:               # 3700 / 4100 → 毫伏
        volts = round(number / 1000.0, 2)
        return round(_cell_voltage_to_percent(volts), 1), volts
    if not is_int and 4.5 < number <= 60.0:      # 12.6 / 13.8 → 车辆电瓶电压
        return None, _voltage_from_value(number)
    if 0.0 <= number <= 100.0:
        return _normalize_percent(number), None
    return None, _voltage_from_value(number)


def _flatten(sources: Iterable[Any]) -> dict[str, Any]:
    """把若干字典压平成一层；嵌套字典同时保留 "parent.child" 形式的键。"""
    flat: dict[str, Any] = {}

    def walk(prefix: str, value: Any, depth: int) -> None:
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            full = f"{prefix}.{key}" if prefix else str(key)
            if full not in flat:
                flat[full] = item
            if isinstance(item, dict) and depth < _MAX_DEPTH:
                walk(full, item, depth + 1)
            elif isinstance(item, list) and depth < _MAX_DEPTH and item and isinstance(item[0], dict):
                walk(full, item[0], depth + 1)

    for src in sources:
        walk("", src, 0)
    return flat


def _lookup(flat: dict[str, Any], key: str) -> tuple[str | None, Any]:
    """在压平后的字段里找 key，也认 a.b.c 这种嵌套写法。"""
    if key in flat:
        return key, flat[key]
    for full, value in flat.items():
        if "." in full and full.rsplit(".", 1)[-1] == key:
            return full, value
    return None, None


def extract_battery(sources: Iterable[Any], forced_key: str | None = None) -> dict[str, Any]:
    """从定位记录、设备列表项等数据源里提取电池信息（前面的数据源优先）。

    返回::

        {"level": 0-100 或 None, "charge": True/False 或 None, "voltage": 伏特 或 None,
         "source": 实际取用的字段名, "raw": 原始值, "keys": 可用字段名（排查用）}
    """
    info: dict[str, Any] = {
        "level": None,
        "charge": None,
        "voltage": None,
        "source": None,
        "raw": None,
        "keys": [],
    }

    flat = _flatten(src for src in sources if isinstance(src, dict))
    if not flat:
        return info
    info["keys"] = sorted(flat)

    # 1. 手动指定的字段最优先
    if forced_key:
        key = forced_key.strip()
        full, value = _lookup(flat, key)
        if full is not None:
            level, voltage = _interpret(key.rsplit(".", 1)[-1], value)
            if level is not None or voltage is not None:
                info.update(level=level, voltage=voltage, source=full, raw=value)

    # 2. 按候选表自动识别
    if info["level"] is None:
        for key in PERCENT_KEYS:
            full, value = _lookup(flat, key)
            if full is not None:
                level, voltage = _interpret(key, value)
                if level is not None:
                    info.update(level=level, source=full, raw=value)
                    if voltage is not None and info["voltage"] is None:
                        info["voltage"] = voltage
                    break
                if voltage is not None and info["voltage"] is None:
                    # 只能当电压读（例如车辆电瓶 12.6V），记下来继续找百分比
                    info.update(voltage=voltage, source=full, raw=value)

    # 3. 独立的电压字段
    if info["voltage"] is None:
        for key in VOLTAGE_KEYS:
            full, value = _lookup(flat, key)
            if full is not None:
                voltage = _voltage_from_value(value)
                if voltage is not None:
                    info["voltage"] = voltage
                    if info["source"] is None:
                        info["source"] = full
                    break

    # 4. 充电状态
    for key in CHARGE_KEYS:
        full, value = _lookup(flat, key)
        if full is not None:
            charge = _as_bool(value)
            if charge is not None:
                info["charge"] = charge
                break

    return info
