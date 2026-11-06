"""Constants for the Tuqiang GPS -> Traccar integration."""
from __future__ import annotations

DOMAIN = "tuqiang_traccar"
NAME = "途强转Traccar"

# ---- 配置键 ----
CONF_PLATFORM = "platform"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_DEVICES = "devices"
CONF_TRACCAR_URL = "traccar_url"
CONF_DEVICE_PREFIX = "device_prefix"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_HEARTBEAT = "heartbeat"        # 开关：位置不变时是否补报
CONF_SPEED_UNIT = "speed_unit"       # Traccar 那边收节还是 km/h
CONF_SEND_BATTERY = "send_battery"
CONF_BATTERY_FIELD = "battery_field"
CONF_SEND_ATTRS = "send_attrs"

# ---- 默认值 ----
DEFAULT_UPDATE_INTERVAL = 30      # 秒
DEFAULT_HEARTBEAT = True          # 默认补报，可在选项里关掉
HEARTBEAT_SECONDS = 300           # 补报间隔：5 分钟（Traccar 默认 10 分钟判离线）
DEFAULT_SEND_BATTERY = True
DEFAULT_BATTERY_FIELD = ""
DEFAULT_SEND_ATTRS = True

# 速度单位：途强给的是 km/h，Traccar 的 OsmAnd 协议默认收节
SPEED_UNIT_KNOTS = "knots"
SPEED_UNIT_KMH = "kmh"
DEFAULT_SPEED_UNIT = SPEED_UNIT_KNOTS
SPEED_UNITS = {
    SPEED_UNIT_KNOTS: "Traccar 默认（节，推荐）",
    SPEED_UNIT_KMH: "km/h（Traccar 里把速度单位设成了 km/h 才选）",
}

MIN_UPDATE_INTERVAL = 10
MAX_UPDATE_INTERVAL = 3600

# 设备列表（电量回落来源、名称）刷新间隔
META_REFRESH_SECONDS = 900

# ---- 平台 ----
PLATFORM_TUQIANGOL = "tuqiangol.com"
PLATFORM_TUQIANG123 = "tuqiang123.com"
PLATFORM_TUQIANGNET = "tuqiang.net"
PLATFORMS = [PLATFORM_TUQIANGOL, PLATFORM_TUQIANG123, PLATFORM_TUQIANGNET]

API_HOSTS = {
    PLATFORM_TUQIANGOL: "https://www.tuqiangol.com",
    PLATFORM_TUQIANG123: "https://www.tuqiang123.com",
    PLATFORM_TUQIANGNET: "https://www.tuqiang.net",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = 20              # 途强接口超时（秒）
TRACCAR_TIMEOUT = 15              # Traccar 超时（秒）
TRACCAR_RETRIES = 3

# 登录失败的冷却时间，避免每轮都去撞密码
LOGIN_COOLDOWN = 20
