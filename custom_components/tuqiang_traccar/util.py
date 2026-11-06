"""小工具：时间、数值、坐标。"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# 途强平台返回的时间一律是北京时间
PLATFORM_TZ = ZoneInfo("Asia/Shanghai")

_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S")


def to_float(value, default=0.0):
    """宽松转 float：None / 空串 / 非法值返回 default。"""
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).strip()
        if not text:
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


def parse_platform_time(value) -> float | None:
    """平台的 'YYYY-MM-DD HH:MM:SS'（北京时间）→ UTC 时间戳；失败返回 None。"""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        # 毫秒时间戳
        return number / 1000.0 if number > 1e11 else number
    text = str(value).strip()
    if not text:
        return None
    for fmt in _TIME_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=PLATFORM_TZ).timestamp()
        except ValueError:
            continue
    return None


def sane_timestamp(value, now: float | None = None) -> float:
    """把平台时间限制在合理范围内：比现在晚太多、或早于 7 天，就用当前时间。"""
    now = time.time() if now is None else now
    ts = parse_platform_time(value)
    if ts is None:
        return now
    if ts > now + 3600 or ts < now - 7 * 86400:
        return now
    return ts


def normalize_position(item: dict) -> dict | None:
    """把平台的一条定位记录整理成统一结构；坐标不可用时返回 None。"""
    lat = to_float(item.get("lat"), None)
    lon = to_float(item.get("lng"), None)
    if lat is None or lon is None:
        return None
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None
    if abs(lat) < 1e-6 and abs(lon) < 1e-6:  # 0,0 是无效定位
        return None

    speed = to_float(item.get("speed"), 0.0)
    if speed < 0 or speed > 400:  # km/h，明显异常的值丢掉
        speed = 0.0

    bearing = to_float(item.get("direction"), 0.0) % 360.0

    accuracy = None
    for key in ("accuracy", "acc2", "radius", "locateError"):
        candidate = to_float(item.get(key), None)
        if candidate is not None and 0 <= candidate <= 5000:
            accuracy = candidate
            break

    altitude = to_float(item.get("height"), None)

    hb_time = item.get("hbTime") or item.get("gpsTime") or item.get("time")

    return {
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "speed": speed,           # km/h
        "course": bearing,
        "accuracy": accuracy,     # 米
        "altitude": altitude,
        "timestamp": sane_timestamp(hb_time),
        "hb_time": hb_time,
        "acc": str(item.get("acc", "")).strip() == "1",
        "status": item.get("status"),
        "raw": item,
    }


def seconds_since(ts: float | None) -> float:
    """距离某个 monotonic 时间过了多少秒；None 视为很久以前。"""
    if ts is None:
        return float("inf")
    return time.monotonic() - ts


def utc_offset_string() -> str:
    """给日志用的时区说明。"""
    return f"{datetime.now(PLATFORM_TZ).utcoffset() or timedelta(0)}"
