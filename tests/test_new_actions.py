# -*- coding: utf-8 -*-
"""新动作测试: 伸懒腰 + 打哈欠。

分两层, 按 Phase 1 的规矩如实报告:
- 纯逻辑测试不依赖 PyQt5: 待机触发 helper(phase1.py, 标准库 only) +
  对 config.py / pet.py / frames.py 的源码结构断言(AST/文本, 不 import Qt)。
  这样即使本机没装 PyQt5, 触发间隔/概率、图名常量、台词池、帧优先级、
  互斥守卫这些也能被测到。
- 离屏 GUI 测试需要 PyQt5(QT_QPA_PLATFORM=offscreen), 缺失时自动跳过,
  不冒充通过: 两组图加载、帧名循环、手动触发回 idle、吃东西互斥、
  缺图降级、睡前打哈欠、睡醒伸懒腰、待机随机触发守卫。
"""

import ast
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_standalone(name):
    """不经过 ragdollpet/__init__.py(它会 import PyQt5)直接加载标准库模块。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        f"phase1_standalone_{name}", ROOT / "ragdollpet" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_phase1 = _load_standalone("phase1")
(IDLE_ACTION_CHANCE, IDLE_ACTION_MIN_GAP_SEC, IDLE_ACTION_MIN_IDLE_SEC,
 WAKE_STRETCH_CHANCE, pick_idle_action,
 should_trigger_idle_action) = (
    _phase1.IDLE_ACTION_CHANCE, _phase1.IDLE_ACTION_MIN_GAP_SEC,
    _phase1.IDLE_ACTION_MIN_IDLE_SEC, _phase1.WAKE_STRETCH_CHANCE,
    _phase1.pick_idle_action, _phase1.should_trigger_idle_action)

try:
    from PyQt5.QtCore import QSettings
    from PyQt5.QtWidgets import QApplication
    from ragdollpet.config import Store
    from ragdollpet.pet import DesktopPet
    import ragdollpet.pet as pet_mod
    PYQT5_AVAILABLE = True
except ImportError:
    PYQT5_AVAILABLE = False


def _load_module_ast(name):
    return ast.parse((ROOT / "ragdollpet" / name).read_text(encoding="utf-8"))


def _assigned_lists(tree, names):
    """取模块顶层 `NAME = [...]` 的字符串元素(常量表用)。"""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in names \
                and isinstance(node.value, ast.List):
            out[node.targets[0].id] = [
                e.value for e in node.value.elts if isinstance(e, ast.Constant)]
    return out


class IdleActionLogicTests(unittest.TestCase):
    """触发间隔/概率 helper: 纯函数, 不依赖 PyQt5。"""

    def test_both_gates_must_pass(self):
        # 待机不够 90 秒: 再小的 roll 也不触发
        self.assertFalse(should_trigger_idle_action(89, 1000, 0.0))
        # 上次动作不到 5 分钟: 也不触发
        self.assertFalse(should_trigger_idle_action(1000, 299, 0.0))
        # 两道门都过 + roll 小于概率: 触发
        self.assertTrue(should_trigger_idle_action(90, 300, 0.0))
        self.assertTrue(should_trigger_idle_action(1000, 1000, IDLE_ACTION_CHANCE - 0.01))
        # roll 大于等于概率: 不触发
        self.assertFalse(should_trigger_idle_action(1000, 1000, IDLE_ACTION_CHANCE))
        self.assertFalse(should_trigger_idle_action(1000, 1000, 0.99))

    def test_bad_input_never_triggers(self):
        self.assertFalse(should_trigger_idle_action(None, 1000, 0.0))
        self.assertFalse(should_trigger_idle_action(1000, "soon", 0.0))
        self.assertFalse(should_trigger_idle_action(1000, 1000, "low"))

    def test_constants(self):
        self.assertEqual(IDLE_ACTION_MIN_IDLE_SEC, 90)
        self.assertEqual(IDLE_ACTION_MIN_GAP_SEC, 300)
        self.assertEqual(WAKE_STRETCH_CHANCE, 0.35)

    def test_pick_idle_action_splits_evenly(self):
        self.assertEqual(pick_idle_action(0.0), "stretch")
        self.assertEqual(pick_idle_action(0.49), "stretch")
        self.assertEqual(pick_idle_action(0.5), "yawn")
        self.assertEqual(pick_idle_action(0.99), "yawn")


class NewActionConfigTests(unittest.TestCase):
    """config.py 常量与台词: AST 读源码, 不 import PyQt5。"""

    def test_image_constants(self):
        tree = _load_module_ast("config.py")
        got = _assigned_lists(tree, {"IMG_STRETCH", "IMG_YAWN", "LINES_STRETCH", "LINES_YAWN"})
        self.assertEqual(got["IMG_STRETCH"], ["cat_stretch_1.png", "cat_stretch_2.png"])
        self.assertEqual(got["IMG_YAWN"], ["cat_yawn_1.png", "cat_yawn_2.png"])
        for lines in (got["LINES_STRETCH"], got["LINES_YAWN"]):
            self.assertGreaterEqual(len(lines), 3)
            for line in lines:
                self.assertIsInstance(line, str)
                self.assertTrue(line.strip())
        # 台词保持六一口吻, 别写成鲸鱼
        all_lines = " ".join(got["LINES_STRETCH"] + got["LINES_YAWN"])
        self.assertNotIn("鲸", all_lines)
        self.assertNotIn("大肥鱼", all_lines)

    def test_action_ms_in_range(self):
        tree = _load_module_ast("config.py")
        values = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name) \
                    and isinstance(node.value, ast.Constant):
                values[node.targets[0].id] = node.value.value
        self.assertIn("ACTION_MS", values)
        self.assertGreaterEqual(values["ACTION_MS"], 400)
        self.assertLessEqual(values["ACTION_MS"], 500)

    def test_no_new_pet_json_keys(self):
        """新动作不新增 pet.json 键: DEFAULTS 里不能出现 stretch/yawn/action。"""
        tree = _load_module_ast("config.py")
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name) \
                    and node.targets[0].id == "DEFAULTS" \
                    and isinstance(node.value, ast.Dict):
                keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
        self.assertFalse(any("stretch" in k or "yawn" in k or "action" in k
                             for k in keys))
        # pet.example.json 同样不能多出新键(36 键, 与 DEFAULTS 一致)
        example = json.loads((ROOT / "pet.example.json").read_text(encoding="utf-8"))
        self.assertEqual(set(example), set(keys))
        self.assertFalse(any("stretch" in k or "yawn" in k for k in example))


class NewActionCodeStructureTests(unittest.TestCase):
    """pet.py / frames.py 的接线方式: 源码结构断言, 不 import Qt。"""

    def test_frame_priority_order(self):
        src = (ROOT / "ragdollpet" / "frames.py").read_text(encoding="utf-8")
        eat_at = src.index('self._eating')
        stretch_at = src.index('self._action_kind == "stretch"')
        yawn_at = src.index('self._action_kind == "yawn"')
        pet_at = src.index('self._petting')
        sleep_at = src.index('self.state in ("sleep", "deep_sleep")')
        # 吃东西 > 伸懒腰/打哈欠 > 被摸 > 睡觉
        self.assertLess(eat_at, stretch_at)
        self.assertLess(stretch_at, pet_at)
        self.assertLess(yawn_at, pet_at)
        self.assertLess(pet_at, sleep_at)
        # 帧名带动作名 + 序号 + 朝向(mask 缓存按名字索引)
        self.assertIn('f"stretch', src)
        self.assertIn('f"yawn', src)

    def test_mutual_exclusion_guards_present(self):
        src = (ROOT / "ragdollpet" / "pet.py").read_text(encoding="utf-8")
        self.assertIn("def _start_action", src)
        self.assertIn("def _stop_action", src)
        self.assertIn("def _doing_action", src)
        # _start_action 守卫: 吃东西/做动作/睡觉/跳跃/被摸/省电/动画关
        start = src[src.index("def _start_action"):]
        for guard in ("self._eating", "self._doing_action()", "self._sleeping()",
                      "self._jumping", "self._petting", "self.power_saver",
                      "not self.anim_enabled"):
            self.assertIn(guard, start[:2000], guard)
        # 吃东西和走路也被新动作拦住
        self.assertIn("if self._doing_action():           # 正在伸懒腰/打哈欠时不抢着吃",
                      src)
        # 睡前哈欠约 1.2 秒后才真正睡; 关动画/重置/退出时停定时器
        self.assertIn("QTimer.singleShot(1200, self._finish_pending_sleep)", src)
        self.assertIn("self._action_timer", src)
        self.assertIn("WAKE_STRETCH_CHANCE", src)
        # 右键菜单两个手动入口
        self.assertIn('"伸个懒腰"', src)
        self.assertIn('"打个哈欠"', src)

    def test_new_images_exist_and_are_valid_frames(self):
        """4 张新图都在 image/ 里, 且是 600x600 透明 RGBA(用 Pillow 验, 不依赖 Qt)。"""
        from PIL import Image
        for name in ("cat_stretch_1.png", "cat_stretch_2.png",
                     "cat_yawn_1.png", "cat_yawn_2.png"):
            path = ROOT / "image" / name
            self.assertTrue(path.exists(), name)
            with Image.open(path) as im:
                self.assertEqual(im.size, (600, 600), name)
                self.assertEqual(im.mode, "RGBA", name)
                self.assertEqual(im.getchannel("A").getextrema(), (0, 255), name)


OLD_PET_JSON = {
    "scale_val": 0.5, "ontop": True, "water_on": True, "water_min": 45,
    "sleep_min": 5, "deep_sleep_min": 10, "anim_on": True, "power_saver": False,
    "high_dpi": True, "anni_name": "", "anni_date": "", "last_anni_shown": "",
    "pos_x": 1161, "pos_y": 597,
}


@unittest.skipUnless(PYQT5_AVAILABLE, "需要 PyQt5")
class NewActionsGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
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

    def _drive_action_to_end(self):
        """手动推动作定时器, 不依赖真实时间流逝。"""
        for _ in range(10):
            if not self.pet._doing_action():
                break
            self.pet._on_action_tick()
        self.assertFalse(self.pet._doing_action())

    def test_images_load(self):
        self.assertEqual(len(self.pet.pix_stretch), 2)
        self.assertEqual(len(self.pet.pix_yawn), 2)
        self.assertEqual(len(self.pet.pix_stretch_l), 2)
        self.assertEqual(len(self.pet.pix_yawn_l), 2)
        for pm in self.pet.pix_stretch + self.pet.pix_yawn:
            self.assertFalse(pm.isNull())

    def test_stretch_frame_cycle_returns_to_idle(self):
        self.assertTrue(self.pet._start_stretch())
        self.assertEqual(self.pet._action_kind, "stretch")
        self.assertTrue(self.pet._frame()[0].startswith("stretch"))
        self._drive_action_to_end()
        self.assertIn(self.pet._frame()[0], ("idle", "idle_l"))

    def test_yawn_frame_cycle_and_flipped_name(self):
        self.pet.facing = -1
        self.assertTrue(self.pet._start_yawn())
        name = self.pet._frame()[0]
        self.assertTrue(name.startswith("yawn_l"), name)
        self._drive_action_to_end()
        self.assertIn(self.pet._frame()[0], ("idle", "idle_l"))

    def test_eat_and_action_are_mutually_exclusive(self):
        self.pet._start_eat()
        self.assertTrue(self.pet._eating)
        self.assertFalse(self.pet._start_stretch())
        self.assertFalse(self.pet._start_yawn())
        # 吃完再伸懒腰就可以; 伸懒腰时喂零食抢不进来
        for _ in range(10):
            if not self.pet._eating:
                break
            self.pet._on_eat_tick()
        self.assertTrue(self.pet._start_stretch())
        self.pet._start_eat()
        self.assertFalse(self.pet._eating)
        self.assertEqual(self.pet._action_kind, "stretch")
        self._drive_action_to_end()

    def test_manual_menu_actions(self):
        menu = self.pet._build_menu()
        texts = [a.text() for a in menu.actions()]
        self.assertIn("伸个懒腰", texts)
        self.assertIn("打个哈欠", texts)
        self.pet._manual_stretch()
        self.assertEqual(self.pet._action_kind, "stretch")
        # 做动作时再点打哈欠, 不重复触发、不串动作
        self.pet._manual_yawn()
        self.assertEqual(self.pet._action_kind, "stretch")
        self._drive_action_to_end()
        self.pet._manual_yawn()
        self.assertEqual(self.pet._action_kind, "yawn")
        self._drive_action_to_end()

    def test_missing_images_degrade_gracefully(self):
        self.pet.pix_stretch = []
        self.pet.pix_yawn = []
        self.assertFalse(self.pet._start_stretch())
        self.assertFalse(self.pet._start_yawn())
        self.assertFalse(self.pet._doing_action())
        self.assertIn(self.pet._frame()[0], ("idle", "idle_l"))
        # 图缺了, 睡前直接睡, 不崩也不卡在哈欠里
        self.pet._enter_sleep()
        self.assertEqual(self.pet.state, "sleep")
        self.assertFalse(self.pet._pending_sleep)

    def test_yawn_before_sleep(self):
        # 待机满 5 分钟才够进睡条件(睡前哈欠收尾时复核)
        self.pet.last_interact = time.monotonic() - 400
        self.pet._enter_sleep()
        self.assertTrue(self.pet._pending_sleep)
        self.assertEqual(self.pet._action_kind, "yawn")
        self.assertEqual(self.pet.state, "idle")
        # 哈欠进行中显示的是哈欠帧, 不是睡觉帧
        self.assertTrue(self.pet._frame()[0].startswith("yawn"))
        self._drive_action_to_end()
        # 模拟 1.2 秒定时器到点
        self.pet._finish_pending_sleep()
        self.assertEqual(self.pet.state, "sleep")
        self.assertFalse(self.pet._pending_sleep)

    def test_yawn_before_sleep_aborts_if_touched(self):
        self.pet.last_interact = time.monotonic() - 400
        self.pet._enter_sleep()
        self.assertTrue(self.pet._pending_sleep)
        # 哈欠期间被摸: 收尾时不睡了, 留在待机
        self.pet._touch()
        self._drive_action_to_end()
        self.pet._finish_pending_sleep()
        self.assertEqual(self.pet.state, "idle")
        self.assertFalse(self.pet._pending_sleep)

    def test_wake_up_deep_sleep_stretch(self):
        self.pet._set_state("deep_sleep")
        with mock.patch.object(pet_mod.random, "random", return_value=0.1):
            self.pet._wake_up(by_input=True)
        self.assertEqual(self.pet.state, "idle")
        self.assertEqual(self.pet._action_kind, "stretch")
        self._drive_action_to_end()

    def test_wake_up_deep_sleep_back_line_when_no_stretch(self):
        self.pet._set_state("deep_sleep")
        with mock.patch.object(pet_mod.random, "random", return_value=0.5):
            self.pet._wake_up(by_input=True)
        self.assertFalse(self.pet._doing_action())
        self.assertTrue(self.pet.bubble.isVisible())
        self.assertTrue(any(word in self.pet.bubble.label.text()
                            for word in ("回来啦", "等你好久", "忘在这儿")),
                        self.pet.bubble.label.text())

    def test_idle_random_trigger_guards(self):
        self.pet.last_interact = time.monotonic() - 100   # 待机超 90 秒
        self.pet._last_action_at = time.monotonic() - 400  # 上次动作超 5 分钟
        with mock.patch.object(pet_mod.random, "random", return_value=0.0):
            self.pet._maybe_idle_action()
        self.assertTrue(self.pet._doing_action())
        self._drive_action_to_end()
        # 吃东西时不触发
        self.pet._start_eat()
        self.pet._last_action_at = time.monotonic() - 400
        with mock.patch.object(pet_mod.random, "random", return_value=0.0):
            self.pet._maybe_idle_action()
        self.assertFalse(self.pet._doing_action())
        for _ in range(10):
            if not self.pet._eating:
                break
            self.pet._on_eat_tick()
        # 刚待机(不到 90 秒)不触发
        self.pet.last_interact = time.monotonic()
        with mock.patch.object(pet_mod.random, "random", return_value=0.0):
            self.pet._maybe_idle_action()
        self.assertFalse(self.pet._doing_action())


if __name__ == "__main__":
    unittest.main()
