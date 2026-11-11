# -*- coding: utf-8 -*-
"""第一期 GUI 集成测试: 离屏模式(QT_QPA_PLATFORM=offscreen)构造 DesktopPet。

只测能在 Linux 离屏环境验证的部分: 旧配置兼容、模式守卫、免打扰队列、
今日统计、定时提醒触发、大小档位、天气链路(打桩, 不联网)。
Windows 专属(全局快捷键注册、前台应用、开机自启、托盘)不在此覆盖,
需在 Windows 真机上验证, 见 README 的“真机验证清单”。
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from PyQt5.QtCore import QSettings
    from PyQt5.QtWidgets import QApplication
    from ragdollpet.config import Store
    from ragdollpet.pet import DesktopPet
    import ragdollpet.weather as weather_mod
    PYQT5_AVAILABLE = True
except ImportError:
    PYQT5_AVAILABLE = False

OLD_PET_JSON = {
    "scale_val": 0.5, "ontop": True, "water_on": True, "water_min": 45,
    "sleep_min": 5, "deep_sleep_min": 10, "anim_on": True, "power_saver": False,
    "high_dpi": True, "anni_name": "", "anni_date": "", "last_anni_shown": "",
    "pos_x": 1161, "pos_y": 597,
}


@unittest.skipUnless(PYQT5_AVAILABLE, "需要 PyQt5")
class PetPhase1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # 旧版 pet.json: 只有 v2.2 的键, 没有第一期任何新键
        with open(os.path.join(self.tmp, "t.json"), "w", encoding="utf-8") as f:
            json.dump(OLD_PET_JSON, f, ensure_ascii=False)
        qs = QSettings(os.path.join(self.tmp, "t.ini"), QSettings.IniFormat)
        self.store = Store(qs)
        self.pet = DesktopPet(self.store)

    def tearDown(self):
        try:
            self.pet.shutdown()
        except Exception:
            pass
        self.app.processEvents()

    def test_old_config_loads_with_phase1_defaults(self):
        # 旧配置能直接用, 新键补默认值, 不丢旧值
        self.assertEqual(self.pet.scale, 0.5)
        self.assertEqual(self.pet.speech_tier, "normal")
        self.assertEqual(self.pet.mode, "free")
        self.assertEqual(self.pet.window_layer, "top")  # 由旧 ontop=True 推导
        self.assertEqual(self.pet.weather_city, "上海")
        self.assertEqual(len(self.pet.reminders), 1)
        self.assertEqual(self.pet.reminders[0]["time"], "23:30")
        self.assertEqual(self.pet.opacity, 1.0)
        self.assertFalse(self.pet.locked)
        # 落盘后旧文件多了新键, 旧值还在
        saved = json.loads(Path(self.store.path).read_text(encoding="utf-8"))
        self.assertEqual(saved["scale_val"], 0.5)
        self.assertEqual(saved["speech_tier"], "normal")

    def test_stay_mode_never_walks(self):
        self.pet._set_mode("stay")
        self.assertEqual(self.pet.mode, "stay")
        self.assertFalse(self.pet._next_walk.isActive())
        self.pet._start_walk()
        self.assertNotEqual(self.pet.state, "walk")
        self.pet._set_mode("free")
        self.assertEqual(self.pet.mode, "free")

    def test_speech_tier_persists(self):
        self.pet._set_speech_tier("talkative")
        self.assertEqual(self.store.get_str("speech_tier"), "talkative")

    def test_size_preset(self):
        self.pet._apply_size_preset("大")
        self.assertEqual(self.pet.scale, 2.0)
        self.assertEqual(self.pet.size_preset, "大")
        self.pet._apply_size_preset("迷你")
        self.assertEqual(self.pet.scale, 0.5)

    def test_dnd_queues_and_flushes(self):
        self.pet.start_dnd(30)
        self.assertTrue(self.pet._dnd.active())
        self.pet._say("该喝水啦", category="water")
        self.assertEqual(self.pet._dnd.queued, ["该喝水啦"])
        self.assertFalse(self.pet.bubble.isVisible())
        self.pet.end_dnd()
        self.assertTrue(self.pet.bubble.isVisible())
        self.assertIn("1 条提醒", self.pet.bubble.label.text())

    def test_category_switch_blocks_idle_lines(self):
        self.pet.speech_categories = dict(self.pet.speech_categories, idle=False)
        self.pet._say("闲聊一句", category="idle")
        self.assertFalse(self.pet.bubble.isVisible())
        self.pet._say("喝水提醒", category="water")
        self.assertTrue(self.pet.bubble.isVisible())

    def test_stats_bump_and_today_summary(self):
        self.pet._bump_stat("clicks")
        self.pet._bump_stat("clicks")
        self.assertEqual(self.store.get_int("stat_clicks"), 2)
        self.pet.show_today_summary()
        text = self.pet.bubble.label.text()
        self.assertIn("摸了我 2 次", text)
        self.assertNotIn("余额", text)

    def test_scheduled_interval_reminder_fires(self):
        now = datetime.now()
        self.pet.reminders = [{
            "id": "test-1", "kind": "interval", "minutes": 60,
            "text": "测试提醒", "enabled": True, "last_fired": "",
        }]
        self.pet._check_reminders()
        self.assertIn("测试提醒", self.pet.bubble.label.text())
        self.assertEqual(self.store.get_int("stat_reminders"), 1)
        # 同一秒内再检查不重复发
        self.pet.bubble.hide()
        self.pet._check_reminders()
        self.assertFalse(self.pet.bubble.isVisible())

    def test_weather_chain_with_stubbed_http(self):
        sk_2d = ('var dataSK={"cityname":"上海","city":"101020100","temp":"18.6",'
                 '"WD":"东北风","WS":"1级","SD":"46%","time":"15:50","weather":"多云"}')
        real_get = weather_mod._http_get
        weather_mod._http_get = lambda url, timeout=8: sk_2d
        try:
            info = weather_mod.fetch_weather("上海")
            self.assertEqual(info["temp"], "18.6")
            self.assertEqual(info["weather"], "多云")
            self.pet._check_weather(city="上海")
            # 后台线程 + 500ms 轮询: 等天气文本出现再断言(启动问候 1.5 秒后也会冒一句,
            # 所以不能只等气泡可见, 要等到内容是天气)。
            deadline = datetime.now() + timedelta(seconds=5)
            while datetime.now() < deadline \
                    and "上海今天 18.6°" not in self.pet.bubble.label.text():
                self.app.processEvents()
            self.assertIn("上海今天 18.6°", self.pet.bubble.label.text())
        finally:
            weather_mod._http_get = real_get

    def test_menu_builds_with_phase1_sections(self):
        menu = self.pet._build_menu()
        texts = [a.text() for a in menu.actions()]
        for expected in ("模式", "语录频率", "大小", "窗口层级", "今日汇总",
                         "查看天气", "免打扰", "设置…"):
            self.assertTrue(any(expected in t for t in texts), expected)
        self.assertEqual(len(self.pet._mode_actions), 3)
        self.assertEqual(len(self.pet._tier_actions), 4)


if __name__ == "__main__":
    unittest.main()
