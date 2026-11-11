# -*- coding: utf-8 -*-
"""第一期“桌宠功能对齐”的纯逻辑层。

本模块只用 Python 标准库, 不 import PyQt5, 不碰 Windows API, 不联网。
所有函数都是纯函数或轻量状态类, 可以在没有显示器、没有 Windows、
没有 PyQt5 的机器上直接单元测试。

对标来源是 DeepSeek 大肥鱼的桌宠版(deepseek-dafeiyu-pet, PySide6)
和 QQ 版(qq-deepseek-bot) —— 但本期只做桌宠侧的离线功能,
AI 聊天 / 长期记忆 / 自由时间逛 B 站属于第二、三期, 这里不要加。

功能覆盖:
    语录频率四档 / 语录类别开关
    定时提醒(每天 / 每周 / 每隔 N 分钟)
    免打扰队列 + 今日汇总(本地统计, 没有余额)
    天气解析(中国天气网 sk_2d + open-meteo, 免 Key)
    应用使用时长提醒 + 应用启动
    全局快捷键解析 / 校验
    窗口: 大小档位 / 吸附 / 透明度 / 层级校验
"""

import json
import random
import re
from datetime import datetime, timedelta

# ================================ 语录 ================================
# 频率档位: (最短秒, 最长秒)。normal 沿用六一原来的 18~35 秒。
SPEECH_TIERS = {
    "quiet": (60, 120),      # 安静: 约 1~2 分钟一句
    "normal": (18, 35),      # 正常: 沿用 v2.2 的 18~35 秒
    "chatty": (10, 16),      # 话多: 约 10~16 秒
    "talkative": (5, 8),     # 话痨: 约 5~8 秒
}
SPEECH_TIER_NAMES = {
    "quiet": "安静",
    "normal": "正常",
    "chatty": "话多",
    "talkative": "话痨",
}
# 语录类别: idle 日常闲聊 / pet 被摸回嘴 / drag 拖拽 / back 离开返回 /
# water 喝水提醒 / feed 喂食 / sleep 睡眠 / anni 纪念日 / hotkey 快捷键
SPEECH_CATEGORIES = ["idle", "pet", "drag", "back", "water", "feed", "sleep", "anni", "hotkey"]
SPEECH_CATEGORY_NAMES = {
    "idle": "日常闲聊",
    "pet": "被摸回嘴",
    "drag": "拖拽时",
    "back": "离开·返回时",
    "water": "喝水提醒",
    "feed": "喂食时",
    "sleep": "睡眠时",
    "anni": "纪念日",
    "hotkey": "快捷键",
}


def next_idle_interval(tier: str, rng=None) -> int:
    """按档位抽一次闲聊间隔(秒)。档位名非法时回落到 normal。"""
    low, high = SPEECH_TIERS.get(tier, SPEECH_TIERS["normal"])
    rng = rng or random
    return rng.randint(low, high)


def category_enabled(categories, category: str) -> bool:
    """类别开关: 配置里没写明的类别默认开(向后兼容旧 pet.json)。"""
    if not isinstance(categories, dict):
        return True
    if category not in categories:
        return True
    value = categories[category]
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


# ================================ 新动作: 伸懒腰 / 打哈欠 =================
# 待机随机触发的节流: 上次动作后至少隔 5 分钟, 且本次待机超过 90 秒,
# 每个 1 秒 tick 里只有小概率触发, 不会刚待机就连着伸懒腰打哈欠。
# roll 由调用方传 random.random(), 这样逻辑可单元测试(不依赖随机种子)。
IDLE_ACTION_MIN_GAP_SEC = 300    # 上次伸懒腰/打哈欠后至少隔这么久
IDLE_ACTION_MIN_IDLE_SEC = 90    # 本次待机(无交互)超过这么久才考虑触发
IDLE_ACTION_CHANCE = 0.08        # 每秒 tick 的触发概率(约每 12 秒一次期望, 但被上面两道门拦着)
WAKE_STRETCH_CHANCE = 0.35      # 深睡醒来时伸懒腰的概率(照抄 LINES_BACK 的 35% 写法)


def should_trigger_idle_action(idle_seconds, since_last_action_seconds, roll) -> bool:
    """待机随机动作该不该触发: 两道时间门都过, 再看 roll 是否小于概率。"""
    try:
        idle_seconds = float(idle_seconds)
        since_last_action_seconds = float(since_last_action_seconds)
        roll = float(roll)
    except (TypeError, ValueError):
        return False
    if idle_seconds < IDLE_ACTION_MIN_IDLE_SEC:
        return False
    if since_last_action_seconds < IDLE_ACTION_MIN_GAP_SEC:
        return False
    return roll < IDLE_ACTION_CHANCE


def pick_idle_action(roll) -> str:
    """待机随机动作二选一: roll < 0.5 伸懒腰, 否则打哈欠。"""
    try:
        return "stretch" if float(roll) < 0.5 else "yawn"
    except (TypeError, ValueError):
        return "stretch"


# ================================ 模式 / 窗口 ================================
MODES = ("free", "follow", "stay")
MODE_NAMES = {"free": "自由散步", "follow": "跟随鼠标", "stay": "原地待着"}

LAYERS = ("top", "normal", "bottom")
LAYER_NAMES = {"top": "始终置顶", "normal": "普通层", "bottom": "置底"}

# 大小五档: 名字 -> 缩放倍数。滚轮仍可在 0.5~2.5 之间无级调。
SIZE_PRESETS = {
    "迷你": 0.5,
    "特小": 0.75,
    "小": 1.0,
    "中": 1.5,
    "大": 2.0,
}
SCALE_MIN, SCALE_MAX = 0.5, 2.5


def clamp_opacity(value) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(0.2, min(1.0, value))


def snap_position(x: int, y: int, w: int, h: int, area, threshold: int = 24):
    """拖拽松手后吸附到屏幕四边。

    area 是 (left, top, right, bottom)。某条边距离在 threshold 内就贴过去,
    返回吸附后的 (x, y)。纯函数, 可单元测试。
    """
    left, top, right, bottom = area
    if abs(x - left) <= threshold:
        x = left
    elif abs(x + w - right) <= threshold:
        x = right - w
    if abs(y - top) <= threshold:
        y = top
    elif abs(y + h - bottom) <= threshold:
        y = bottom - h
    return x, y


# ================================ 定时提醒 ================================
# 一条提醒是 dict:
#   {"id": str, "kind": "daily"|"weekly"|"interval",
#    "time": "HH:MM" (daily/weekly), "weekdays": [0..6, 周一=0] (weekly),
#    "minutes": int (interval), "text": str, "enabled": bool,
#    "last_fired": "" (ISO 日期/时间, 防重复触发)}
REMINDER_KINDS = ("daily", "weekly", "interval")

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_reminder(raw) -> dict:
    """校验并规范化一条提醒, 非法就抛 ValueError(带中文原因)。"""
    if not isinstance(raw, dict):
        raise ValueError("提醒必须是一条记录")
    kind = raw.get("kind")
    if kind not in REMINDER_KINDS:
        raise ValueError(f"提醒类型只能是 daily/weekly/interval, 现在是 {kind!r}")
    text = str(raw.get("text", "")).strip()
    if not text:
        raise ValueError("提醒台词不能为空")
    reminder = {
        "id": str(raw.get("id", "")).strip() or _new_reminder_id(),
        "kind": kind,
        "text": text,
        "enabled": bool(raw.get("enabled", True)),
        "last_fired": str(raw.get("last_fired", "")),
    }
    if kind in ("daily", "weekly"):
        time_text = str(raw.get("time", "")).strip()
        if not _TIME_RE.match(time_text):
            raise ValueError(f"时间必须是 HH:MM, 现在是 {time_text!r}")
        reminder["time"] = time_text
        if kind == "weekly":
            weekdays = raw.get("weekdays", [])
            if not isinstance(weekdays, (list, tuple)) or not weekdays:
                raise ValueError("每周提醒至少选一天")
            days = sorted({int(d) for d in weekdays})
            if any(d < 0 or d > 6 for d in days):
                raise ValueError("星期只能是 0(周一)~6(周日)")
            reminder["weekdays"] = days
    else:
        try:
            minutes = int(raw.get("minutes", 0))
        except (TypeError, ValueError):
            raise ValueError("每隔 N 分钟提醒, N 必须是整数")
        if minutes < 1 or minutes > 60 * 24:
            raise ValueError("间隔必须在 1~1440 分钟之间")
        reminder["minutes"] = minutes
    return reminder


def _new_reminder_id() -> str:
    import time as _time

    return "r%d" % int(_time.time() * 1000)


def default_reminders():
    """默认提醒: 每天 23:30 该睡了。"""
    return [parse_reminder({
        "id": "default-bedtime", "kind": "daily",
        "time": "23:30", "text": "都 23:30 啦，该睡了哦～明天也要元气满满",
        "enabled": True,
    })]


def _next_daily_fire(reminder: dict, now: datetime):
    hour, minute = (int(p) for p in reminder["time"].split(":"))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def next_fire(reminder: dict, now: datetime):
    """算一条提醒下一次触发时间; 已禁用或算不出返回 None。"""
    if not reminder.get("enabled", True):
        return None
    kind = reminder.get("kind")
    if kind == "daily":
        return _next_daily_fire(reminder, now)
    if kind == "weekly":
        hour, minute = (int(p) for p in reminder["time"].split(":"))
        weekdays = sorted(reminder.get("weekdays", []))
        if not weekdays:
            return None
        for offset in range(8):
            day = now + timedelta(days=offset)
            if day.weekday() not in weekdays:
                continue
            candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate > now:
                return candidate
        return None
    if kind == "interval":
        last = reminder.get("last_fired", "")
        try:
            last_dt = datetime.fromisoformat(last) if last else None
        except ValueError:
            last_dt = None
        base = last_dt or now
        nxt = base + timedelta(minutes=int(reminder.get("minutes", 60)))
        if nxt <= now:
            # 错过了一次以上(比如电脑刚开机): 下一次按“从现在起”算, 不补发
            nxt = now + timedelta(minutes=int(reminder.get("minutes", 60)))
        return nxt
    return None


def due_reminders(reminders, now: datetime, window_seconds: int = 60):
    """返回现在该触发的提醒列表(并把它们的 last_fired 更新为 now)。

    每天/每周型: 触发时刻落在 [now-window, now] 内且今天还没发过。
    每隔 N 分钟型: 上次触发距今 >= 间隔。
    调用方负责把返回的提醒写回配置(调用 parse_reminder 规范化后保存)。
    """
    due = []
    for raw in reminders or []:
        try:
            reminder = parse_reminder(raw)
        except ValueError:
            continue
        if not reminder["enabled"]:
            continue
        kind = reminder["kind"]
        if kind in ("daily", "weekly"):
            hour, minute = (int(p) for p in reminder["time"].split(":"))
            if kind == "weekly" and now.weekday() not in reminder.get("weekdays", []):
                continue
            fire = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if fire <= now < fire + timedelta(seconds=window_seconds):
                last = reminder.get("last_fired", "")
                if last.startswith(now.date().isoformat()):
                    continue  # 今天已经发过
                reminder["last_fired"] = now.isoformat()
                raw["last_fired"] = reminder["last_fired"]  # 写回原记录, 否则下次还发
                due.append(reminder)
        else:
            last = reminder.get("last_fired", "")
            try:
                last_dt = datetime.fromisoformat(last) if last else None
            except ValueError:
                last_dt = None
            if last_dt is None or (now - last_dt) >= timedelta(minutes=reminder["minutes"]):
                reminder["last_fired"] = now.isoformat()
                raw["last_fired"] = reminder["last_fired"]  # 写回原记录, 否则下次还发
                due.append(reminder)
    return due


# ================================ 免打扰 ================================
class DndQueue:
    """免打扰: 开始后攒提醒, 结束后汇总一句。

    用 wall clock(datetime), 不用 monotonic: 免打扰是“到几点为止”的概念,
    用户改系统时间的影响可接受, 且和定时提醒共用一套时间口径。
    """

    def __init__(self):
        self.until = None        # datetime | None
        self.queued = []         # 攒下的台词

    def start(self, minutes: int, now: datetime = None):
        now = now or datetime.now()
        if minutes <= 0:
            raise ValueError("免打扰时长必须是正数分钟")
        self.until = now + timedelta(minutes=minutes)
        self.queued = []
        return self.until

    def stop(self):
        self.until = None

    def active(self, now: datetime = None) -> bool:
        if self.until is None:
            return False
        now = now or datetime.now()
        if now >= self.until:
            self.until = None
            return False
        return True

    def remaining_minutes(self, now: datetime = None) -> int:
        if not self.active(now):
            return 0
        now = now or datetime.now()
        return max(0, int((self.until - now).total_seconds() // 60))

    def queue(self, text: str):
        if text:
            self.queued.append(text)

    def flush_summary(self, pet_name: str = "六一") -> str | None:
        """免打扰结束时汇总一句; 没攒下东西返回 None。"""
        if not self.queued:
            self.until = None
            return None
        count = len(self.queued)
        last = self.queued[-1]
        self.queued = []
        self.until = None
        return f"静完了，刚才攒了 {count} 条提醒，最近一条「{last}」"


# ================================ 今日汇总 ================================
class DailyStats:
    """本地当日统计: 互动 / 散步 / 提醒 / 喂食 / 唤醒次数。

    只有本地计数, 没有余额、没有联网数据, 不要在这里编造余额。
    """

    FIELDS = ("clicks", "walks", "reminders", "feeds", "wakes")

    def __init__(self, date_str: str = "", **counts):
        self.date_str = date_str
        self.counts = {f: int(counts.get(f, 0) or 0) for f in self.FIELDS}

    @classmethod
    def from_store(cls, date_str: str, raw: dict):
        counts = {}
        for field in cls.FIELDS:
            try:
                counts[field] = int(raw.get("stat_" + field, 0) or 0)
            except (TypeError, ValueError):
                counts[field] = 0
        return cls(date_str=date_str, **counts)

    def to_store_dict(self) -> dict:
        out = {"stats_date": self.date_str}
        for field in self.FIELDS:
            out["stat_" + field] = self.counts[field]
        return out

    def bump(self, field: str, amount: int = 1):
        if field in self.counts:
            self.counts[field] += amount

    def summary_text(self, pet_name: str = "六一") -> str:
        c = self.counts
        return (
            f"今天的汇总来啦：你摸了我 {c['clicks']} 次，"
            f"我散了 {c['walks']} 次步，提醒了你 {c['reminders']} 次，"
            f"吃了 {c['feeds']} 次小零食，被你叫醒 {c['wakes']} 次～"
        )


# ================================ 天气解析 ================================
# 中国天气网城市代码(常用城市兜底表): toy1 搜索不到时用。
# 代码来自 weather.com.cn 的公开城市编码, 只做“名字 -> 代码”映射。
COMMON_CITY_CODES = {
    "北京": "101010100", "上海": "101020100", "天津": "101030100",
    "重庆": "101040100", "广州": "101280101", "深圳": "101280601",
    "成都": "101270101", "杭州": "101210101", "武汉": "101200101",
    "南京": "101190101", "西安": "101110101", "苏州": "101190401",
    "长沙": "101250101", "郑州": "101180101", "济南": "101120101",
    "青岛": "101120201", "沈阳": "101070101", "大连": "101070201",
    "哈尔滨": "101050101", "长春": "101060101", "石家庄": "101090101",
    "太原": "101100101", "呼和浩特": "101080101", "合肥": "101220101",
    "福州": "101230101", "厦门": "101230201", "南昌": "101240101",
    "昆明": "101290101", "贵阳": "101260101", "南宁": "101300101",
    "海口": "101310101", "兰州": "101160101", "西宁": "101150101",
    "银川": "101170101", "乌鲁木齐": "101130101", "拉萨": "101140101",
    "宁波": "101210201", "温州": "101210101", "汕头": "101280201",
}

# open-meteo WMO 天气代码 -> 中文(只覆盖常见码, 认不出返回“未知天气”)。
WMO_ZH = {
    0: "晴", 1: "多云", 2: "多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨",
    56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "阵雨", 82: "暴雨",
    85: "阵雪", 86: "阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "雷阵雨伴冰雹",
}


def normalize_city_name(name: str) -> str:
    """城市名规范化: 去空白, 去掉末尾的 区/县/市 后缀(鹿城区->鹿城)。"""
    name = (name or "").strip()
    for suffix in ("区", "县", "市"):
        if len(name) > 2 and name.endswith(suffix):
            name = name[:-1]
            break
    return name


def parse_city_search(text: str):
    """解析 toy1.weather.com.cn/search 的返回。

    实测该接口有时只返回 "()"。返回 [(城市名, 代码), ...], 解析失败返回 []。
    兼容 JSON 数组 / {"city": [...]} / 带回调包裹的 JS。
    """
    if not text:
        return []
    text = text.strip()
    if text in ("()", "", "null"):
        return []
    payload = text
    if payload.startswith("{") is False and "(" in payload and payload.endswith(")"):
        payload = payload[payload.index("(") + 1:-1]
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return []
    items = []
    if isinstance(data, dict):
        items = data.get("city") or data.get("data") or []
    elif isinstance(data, list):
        items = data
    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or item.get("citycode") or "")
        name = str(item.get("cityname") or item.get("name") or "")
        if code.isdigit() and name:
            out.append((name, code))
    return out


def parse_sk_2d(text: str) -> dict:
    """解析 d1.weather.com.cn/sk_2d/{code}.html 的返回。

    格式是 `var dataSK={...}`。返回 {"temp","weather","wind","humidity","time"}。
    """
    if not text:
        raise ValueError("天气实况返回为空")
    match = re.search(r"var\s+dataSK\s*=\s*(\{.*\})\s*;?\s*$", text.strip(), re.S)
    if not match:
        raise ValueError("天气实况格式不对")
    try:
        data = json.loads(match.group(1))
    except ValueError as exc:
        raise ValueError("天气实况 JSON 解析失败") from exc
    temp = str(data.get("temp", "")).strip()
    weather = str(data.get("weather", "")).strip()
    if not temp or not weather:
        raise ValueError("天气实况缺少温度或天气")
    return {
        "temp": temp,
        "weather": weather,
        "wind": str(data.get("WD", "")).strip(),
        "wind_level": str(data.get("WS", "")).strip(),
        "humidity": str(data.get("SD", "")).strip(),
        "time": str(data.get("time", "")).strip(),
        "city": str(data.get("cityname", "")).strip(),
    }


def parse_openmeteo_current(data: dict) -> dict:
    """解析 open-meteo forecast 的 current 段(兜底源)。"""
    if not isinstance(data, dict):
        raise ValueError("open-meteo 返回格式不对")
    current = data.get("current") or {}
    if "temperature_2m" not in current or "weather_code" not in current:
        raise ValueError("open-meteo 返回缺少温度或天气代码")
    try:
        code = int(current["weather_code"])
    except (TypeError, ValueError):
        code = -1
    return {
        "temp": str(current["temperature_2m"]),
        "weather": WMO_ZH.get(code, "未知天气"),
        "wind": "",
        "wind_level": str(current.get("wind_speed_10m", "")),
        "humidity": str(current.get("relative_humidity_2m", "")),
        "time": str(current.get("time", "")),
        "city": "",
    }


def format_weather_line(city: str, info: dict) -> str:
    """播报用的中文气泡台词, 全中文, 不留英文原文。"""
    temp = info.get("temp", "")
    weather = info.get("weather", "未知天气")
    wind = info.get("wind", "")
    level = info.get("wind_level", "")
    wind_text = f"，{wind}{level}" if wind or level else ""
    return f"{city}今天 {temp}°，天气{weather}{wind_text}"


# ================================ 应用使用时长 ================================
class AppUsageTracker:
    """前台应用使用时长跟踪(纯逻辑, 不碰系统 API)。

    update(app_name, now_seconds, idle_seconds) -> [事件, ...]
    事件是 dict: {"kind": "usage", "app":..., "minutes":...} 或
    {"kind": "launch", "app":...} (本会话第一次见到该应用)。
    规则: 换应用重新计时; 键鼠空闲超过 idle_pause 秒不计时;
    达到阈值只提醒一次, 直到换应用或阈值被改过。
    """

    def __init__(self, watch, idle_pause_seconds: int = 60):
        # watch: [{"app": "Steam.exe", "minutes": 60, "enabled": True}, ...]
        self.watch = {}
        for item in watch or []:
            try:
                name = str(item.get("app", "")).strip().lower()
                minutes = int(item.get("minutes", 60))
                if name and minutes >= 1 and item.get("enabled", True):
                    self.watch[name] = minutes
            except (TypeError, ValueError, AttributeError):
                continue
        self.idle_pause_seconds = idle_pause_seconds
        self.current = None          # 当前应用(小写)
        self.active_seconds = 0.0
        self.notified = set()        # 已提醒过的应用
        self.seen = set()            # 本会话见过的应用(启动提醒用)

    def update(self, app_name, now_seconds: float, idle_seconds: float,
               launch_on: bool = True):
        events = []
        app = (app_name or "").strip().lower() or None
        if app is None:
            return events
        # 挂机/看电影(键鼠空闲超过阈值): 不计时, 也不发任何事件
        if idle_seconds is not None and idle_seconds >= 0 \
                and idle_seconds >= self.idle_pause_seconds:
            return events
        if app != self.current:
            self.current = app
            self.active_seconds = 0.0
            if launch_on and app in self.watch and app not in self.seen:
                self.seen.add(app)
                events.append({"kind": "launch", "app": app_name})
        if app not in self.watch:
            return events
        self.active_seconds += 1.0  # 调用方每秒调一次
        threshold = self.watch[app] * 60
        if self.active_seconds >= threshold and app not in self.notified:
            self.notified.add(app)
            events.append({"kind": "usage", "app": app_name,
                           "minutes": self.watch[app]})
        return events


# ================================ 全局快捷键 ================================
HOTKEY_MODIFIERS = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt",
                    "shift": "shift", "win": "win", "windows": "win"}
# Windows MOD_* 常量, 供 platform_win 注册用。
WIN_MOD_FLAGS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "win": 0x8}
# 虚拟键码: 数字 / 字母 / F1~F12。
VK_CODES = {}
for _d in range(10):
    VK_CODES[str(_d)] = 0x30 + _d
for _i, _c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    VK_CODES[_c] = 0x41 + _i
for _i in range(1, 13):
    VK_CODES[f"F{_i}"] = 0x6F + _i


def parse_hotkey(text: str):
    """解析 "Ctrl+Alt+1" -> ({"ctrl","alt"}, "1")。非法抛 ValueError。"""
    if not text:
        raise ValueError("快捷键不能为空")
    parts = [p.strip().lower() for p in str(text).split("+") if p.strip()]
    if len(parts) < 2:
        raise ValueError("快捷键至少要一个修饰键加一个按键, 例如 Ctrl+Alt+1")
    *mods, key = parts
    mod_set = set()
    for mod in mods:
        if mod not in HOTKEY_MODIFIERS:
            raise ValueError(f"不支持的修饰键: {mod}")
        mod_set.add(HOTKEY_MODIFIERS[mod])
    key_up = key.upper()
    if key_up not in VK_CODES:
        raise ValueError(f"不支持的按键: {key}(只支持 0-9 / A-Z / F1-F12)")
    if not mod_set:
        raise ValueError("快捷键至少要一个修饰键")
    return mod_set, key_up


def hotkey_to_windows(text: str):
    """解析快捷键并换算成 Windows 注册用的 (mod_flags, vk)。"""
    mods, key = parse_hotkey(text)
    flags = 0
    for mod in mods:
        flags |= WIN_MOD_FLAGS[mod]
    return flags, VK_CODES[key]


def format_hotkey(text: str) -> str:
    """规范化显示, 例如 "ctrl+alt+1" -> "Ctrl+Alt+1"。"""
    mods, key = parse_hotkey(text)
    order = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
    return "+".join([order[m] for m in ("ctrl", "alt", "shift", "win") if m in mods] + [key])


def validate_hotkeys(hotkeys):
    """校验快捷键列表: 返回错误信息列表(空表示全合法), 含重复键检测。"""
    errors = []
    seen = {}
    for index, item in enumerate(hotkeys or []):
        if not isinstance(item, dict):
            errors.append(f"第 {index + 1} 条快捷键不是一条记录")
            continue
        key = str(item.get("key", "")).strip()
        try:
            normalized = format_hotkey(key)
        except ValueError as exc:
            errors.append(f"第 {index + 1} 条: {exc}")
            continue
        if normalized in seen:
            errors.append(f"快捷键 {normalized} 重复了(第 {seen[normalized] + 1} 条和第 {index + 1} 条)")
        else:
            seen[normalized] = index
        if str(item.get("kind", "")) not in ("praise", "comfort", "tease", "custom"):
            errors.append(f"第 {index + 1} 条: 类型只能是 praise/comfort/tease/custom")
        if item.get("kind") == "custom" and not str(item.get("text", "")).strip():
            errors.append(f"第 {index + 1} 条: 自定义类型要写一句台词")
    return errors
