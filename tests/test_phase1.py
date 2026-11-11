# -*- coding: utf-8 -*-
"""第一期纯逻辑测试: 不 import PyQt5, 不联网, 不碰 Windows API。

覆盖: 语录频率/类别、定时提醒(每天/每周/每隔)、免打扰队列、
今日汇总、天气解析、应用使用时长、全局快捷键解析、窗口吸附。
"""

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_standalone(name):
    """不经过 ragdollpet/__init__.py(它会 import PyQt5)直接加载标准库模块。"""
    spec = importlib.util.spec_from_file_location(
        f"phase1_standalone_{name}", ROOT / "ragdollpet" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


phase1 = _load_standalone("phase1")
platform_win = _load_standalone("platform_win")

# 名字从独立加载的 phase1 模块上取, 不经过 ragdollpet/__init__.py。
(AppUsageTracker, DailyStats, DndQueue, default_reminders, due_reminders,
 format_hotkey, format_weather_line, hotkey_to_windows, next_fire,
 next_idle_interval, normalize_city_name, parse_city_search,
 parse_openmeteo_current, parse_reminder, parse_sk_2d, parse_hotkey,
 snap_position, validate_hotkeys, category_enabled) = (
    phase1.AppUsageTracker, phase1.DailyStats, phase1.DndQueue,
    phase1.default_reminders, phase1.due_reminders, phase1.format_hotkey,
    phase1.format_weather_line, phase1.hotkey_to_windows, phase1.next_fire,
    phase1.next_idle_interval, phase1.normalize_city_name,
    phase1.parse_city_search, phase1.parse_openmeteo_current,
    phase1.parse_reminder, phase1.parse_sk_2d, phase1.parse_hotkey,
    phase1.snap_position, phase1.validate_hotkeys, phase1.category_enabled)


class SpeechTests(unittest.TestCase):
    def test_tier_intervals_are_distinct_and_ordered(self):
        # 四档间隔互不重叠、从安静到话痨递减
        quiet = next_idle_interval("quiet")
        normal = next_idle_interval("normal")
        chatty = next_idle_interval("chatty")
        talkative = next_idle_interval("talkative")
        self.assertTrue(60 <= quiet <= 120)
        self.assertTrue(18 <= normal <= 35)
        self.assertTrue(10 <= chatty <= 16)
        self.assertTrue(5 <= talkative <= 8)
        # 非法档位回落到 normal 的范围
        self.assertTrue(18 <= next_idle_interval("not-a-tier") <= 35)

    def test_category_switch(self):
        self.assertTrue(category_enabled({"idle": True}, "idle"))
        self.assertFalse(category_enabled({"idle": False}, "idle"))
        # 配置里没写明的类别默认开(旧 pet.json 兼容)
        self.assertTrue(category_enabled({}, "water"))
        self.assertTrue(category_enabled(None, "water"))


class ReminderTests(unittest.TestCase):
    def test_parse_daily_weekly_interval(self):
        daily = parse_reminder({"kind": "daily", "time": "23:30", "text": "该睡了"})
        self.assertEqual(daily["time"], "23:30")
        weekly = parse_reminder({"kind": "weekly", "time": "09:00",
                                 "weekdays": [0, 2, 4], "text": "开会"})
        self.assertEqual(weekly["weekdays"], [0, 2, 4])
        interval = parse_reminder({"kind": "interval", "minutes": 45, "text": "喝水"})
        self.assertEqual(interval["minutes"], 45)

    def test_parse_rejects_bad_input(self):
        for bad in ({"kind": "daily", "time": "25:00", "text": "x"},
                    {"kind": "daily", "time": "23:30", "text": "  "},
                    {"kind": "weekly", "time": "09:00", "weekdays": [], "text": "x"},
                    {"kind": "interval", "minutes": 0, "text": "x"},
                    {"kind": "monthly", "text": "x"}):
            with self.assertRaises(ValueError, msg=str(bad)):
                parse_reminder(bad)

    def test_default_reminder_is_bedtime(self):
        reminders = default_reminders()
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["kind"], "daily")
        self.assertEqual(reminders[0]["time"], "23:30")

    def test_next_fire_daily(self):
        reminder = parse_reminder({"kind": "daily", "time": "23:30", "text": "睡"})
        now = datetime(2026, 11, 11, 20, 0)
        self.assertEqual(next_fire(reminder, now), datetime(2026, 11, 11, 23, 30))
        now = datetime(2026, 11, 11, 23, 45)
        self.assertEqual(next_fire(reminder, now), datetime(2026, 11, 12, 23, 30))

    def test_next_fire_weekly(self):
        # 2026-11-11 是周三
        reminder = parse_reminder({"kind": "weekly", "time": "09:00",
                                   "weekdays": [0], "text": "周一"})
        now = datetime(2026, 11, 11, 10, 0)
        self.assertEqual(next_fire(reminder, now).date().isoformat(), "2026-11-16")

    def test_due_daily_fires_once_per_day(self):
        reminders = [parse_reminder({"kind": "daily", "time": "23:30", "text": "睡"})]
        now = datetime(2026, 11, 11, 23, 30, 30)
        due = due_reminders(reminders, now)
        self.assertEqual(len(due), 1)
        # 同一天内再检查: 不重复发
        self.assertEqual(due_reminders(reminders, now + timedelta(seconds=30)), [])
        # 第二天同一时刻: 再发
        self.assertEqual(len(due_reminders(reminders, now + timedelta(days=1))), 1)

    def test_due_interval(self):
        reminders = [parse_reminder({"kind": "interval", "minutes": 45, "text": "喝水"})]
        now = datetime(2026, 11, 11, 10, 0)
        self.assertEqual(len(due_reminders(reminders, now)), 1)  # 首次(没发过)即触发
        self.assertEqual(due_reminders(reminders, now + timedelta(minutes=10)), [])
        self.assertEqual(len(due_reminders(reminders, now + timedelta(minutes=45))), 1)


class DndTests(unittest.TestCase):
    def test_queue_and_flush(self):
        dnd = DndQueue()
        now = datetime(2026, 11, 11, 10, 0)
        dnd.start(30, now)
        self.assertTrue(dnd.active(now + timedelta(minutes=10)))
        self.assertEqual(dnd.remaining_minutes(now + timedelta(minutes=10)), 20)
        dnd.queue("该喝水啦")
        dnd.queue("23:30 该睡了")
        self.assertFalse(dnd.active(now + timedelta(minutes=31)))
        summary = dnd.flush_summary()
        self.assertIn("2 条提醒", summary)
        self.assertIn("23:30 该睡了", summary)
        self.assertIsNone(dnd.flush_summary())

    def test_start_rejects_non_positive(self):
        with self.assertRaises(ValueError):
            DndQueue().start(0)


class StatsTests(unittest.TestCase):
    def test_summary_has_no_balance(self):
        stats = DailyStats(date_str="2026-11-11", clicks=3, walks=2,
                           reminders=1, feeds=1, wakes=0)
        text = stats.summary_text()
        self.assertIn("3 次", text)
        self.assertIn("2 次", text)
        self.assertNotIn("余额", text)
        self.assertNotIn("¥", text)

    def test_roundtrip_store_dict(self):
        stats = DailyStats(date_str="2026-11-11", clicks=5)
        data = stats.to_store_dict()
        restored = DailyStats.from_store("2026-11-11", data)
        self.assertEqual(restored.counts["clicks"], 5)


class WeatherParseTests(unittest.TestCase):
    def test_parse_sk_2d(self):
        text = ('var dataSK={"nameen":"shanghai","cityname":"上海","city":"101020100",'
                '"temp":"18.6","WD":"东北风","WS":"1级","SD":"46%","time":"15:50",'
                '"weather":"多云"}')
        info = parse_sk_2d(text)
        self.assertEqual(info["temp"], "18.6")
        self.assertEqual(info["weather"], "多云")
        self.assertEqual(info["wind"], "东北风")
        line = format_weather_line("上海", info)
        self.assertEqual(line, "上海今天 18.6°，天气多云，东北风1级")

    def test_parse_sk_2d_rejects_garbage(self):
        with self.assertRaises(ValueError):
            parse_sk_2d("<html>403 Forbidden</html>")
        with self.assertRaises(ValueError):
            parse_sk_2d("")

    def test_parse_city_search_empty_and_json(self):
        self.assertEqual(parse_city_search("()"), [])
        self.assertEqual(parse_city_search(""), [])
        text = ('{"city": [{"cityname": "上海", "code": "101020100"},'
                ' {"cityname": "上海市", "code": "101020200"}]}')
        self.assertEqual(parse_city_search(text)[0], ("上海", "101020100"))

    def test_openmeteo_parse_and_wmo(self):
        data = {"current": {"temperature_2m": 12.5, "weather_code": 61,
                            "relative_humidity_2m": 80, "wind_speed_10m": 3.2,
                            "time": "2026-11-11T10:00"}}
        info = parse_openmeteo_current(data)
        self.assertEqual(info["weather"], "小雨")
        self.assertEqual(info["temp"], "12.5")
        with self.assertRaises(ValueError):
            parse_openmeteo_current({"current": {}})

    def test_normalize_city_name(self):
        self.assertEqual(normalize_city_name("鹿城区"), "鹿城")
        self.assertEqual(normalize_city_name("上海"), "上海")
        self.assertEqual(normalize_city_name("  北京市 "), "北京")


class AppUsageTests(unittest.TestCase):
    def test_usage_threshold_once(self):
        tracker = AppUsageTracker([{"app": "Steam.exe", "minutes": 1, "enabled": True}])
        events = []
        for second in range(61):
            events += tracker.update("Steam.exe", second, idle_seconds=0)
        usage = [e for e in events if e["kind"] == "usage"]
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0]["minutes"], 1)
        # 继续用也不重复提醒
        for second in range(61, 130):
            events += tracker.update("Steam.exe", second, idle_seconds=0)
        self.assertEqual(len([e for e in events if e["kind"] == "usage"]), 1)

    def test_switch_resets_timer(self):
        tracker = AppUsageTracker([{"app": "Steam.exe", "minutes": 1, "enabled": True}])
        for second in range(50):
            tracker.update("Steam.exe", second, idle_seconds=0)
        tracker.update("Code.exe", 50, idle_seconds=0)  # 换应用
        events = []
        for second in range(51, 110):
            events += tracker.update("Steam.exe", second, idle_seconds=0)
        # 换走又换回来要重新计时 60 秒: 前 59 秒不应提醒
        self.assertEqual([e for e in events if e["kind"] == "usage"], [])
        # 第 60 秒(累计满 60 秒)提醒一次
        events = tracker.update("Steam.exe", 110, idle_seconds=0)
        self.assertEqual(len([e for e in events if e["kind"] == "usage"]), 1)

    def test_idle_does_not_count(self):
        tracker = AppUsageTracker([{"app": "Steam.exe", "minutes": 1, "enabled": True}])
        events = []
        for second in range(120):
            events += tracker.update("Steam.exe", second, idle_seconds=300)
        self.assertEqual(events, [])

    def test_launch_event_once_per_session(self):
        tracker = AppUsageTracker([{"app": "Steam.exe", "minutes": 60, "enabled": True}])
        first = tracker.update("Steam.exe", 0, idle_seconds=0)
        self.assertEqual([e["kind"] for e in first], ["launch"])
        second = tracker.update("Steam.exe", 1, idle_seconds=0)
        self.assertEqual(second, [])


class HotkeyTests(unittest.TestCase):
    def test_parse_and_windows_mapping(self):
        mods, key = parse_hotkey("Ctrl+Alt+1")
        self.assertEqual(mods, {"ctrl", "alt"})
        self.assertEqual(key, "1")
        flags, vk = hotkey_to_windows("Ctrl+Alt+1")
        self.assertEqual(flags, 0x2 | 0x1)
        self.assertEqual(vk, 0x31)
        self.assertEqual(format_hotkey("ctrl+alt+1"), "Ctrl+Alt+1")
        self.assertEqual(format_hotkey("Shift+F5"), "Shift+F5")

    def test_parse_rejects_bad_hotkeys(self):
        for bad in ("", "1", "Ctrl+1+2", "Ctrl+Foo", "Meta+1"):
            with self.assertRaises(ValueError, msg=bad):
                parse_hotkey(bad)

    def test_validate_detects_duplicates(self):
        good = [{"key": "Ctrl+Alt+1", "kind": "praise", "text": "", "enabled": True}]
        self.assertEqual(validate_hotkeys(good), [])
        bad = [{"key": "Ctrl+Alt+1", "kind": "praise", "text": "", "enabled": True},
               {"key": "ctrl+alt+1", "kind": "tease", "text": "", "enabled": True},
               {"key": "Ctrl+Alt+9", "kind": "custom", "text": "", "enabled": True}]
        errors = validate_hotkeys(bad)
        self.assertEqual(len(errors), 2)  # 1 条重复 + 1 条自定义没写台词


class WindowTests(unittest.TestCase):
    def test_snap_to_edges(self):
        area = (0, 0, 1920, 1080)
        self.assertEqual(snap_position(5, 100, 200, 200, area), (0, 100))
        self.assertEqual(snap_position(1715, 100, 200, 200, area), (1720, 100))
        self.assertEqual(snap_position(100, 3, 200, 200, area), (100, 0))
        self.assertEqual(snap_position(500, 500, 200, 200, area), (500, 500))
        # 右边差 5px、底边差 10px: 两边同时贴过去
        self.assertEqual(snap_position(1715, 870, 200, 200, area), (1720, 880))
        # 离边超过 24px 不贴
        self.assertEqual(snap_position(1900, 870, 200, 200, area), (1900, 880))

    def test_size_presets_within_wheel_range(self):
        for name, scale in phase1.SIZE_PRESETS.items():
            self.assertTrue(phase1.SCALE_MIN <= scale <= phase1.SCALE_MAX, name)
        self.assertEqual(phase1.SIZE_PRESETS["小"], 1.0)


class PlatformGuardTests(unittest.TestCase):
    def test_non_windows_degrades_gracefully(self):
        # 本机是 Linux: 所有 Windows 专属调用都必须返回“不可用”, 不抛异常。
        import sys as _sys
        if _sys.platform == "win32":
            self.skipTest("仅在非 Windows 下验证降级")
        self.assertFalse(platform_win.is_windows())
        self.assertIsNone(platform_win.get_foreground_app())
        self.assertFalse(platform_win.get_autostart())
        self.assertFalse(platform_win.set_autostart(True))
        self.assertFalse(platform_win.register_hotkey(0, 1, 0, 0))
        self.assertFalse(platform_win.unregister_hotkey(0, 1))


if __name__ == "__main__":
    unittest.main()
