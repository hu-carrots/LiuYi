# -*- coding: utf-8 -*-
"""扒拉文件测试: 待机第三个新动作。

分两层, 按 test_new_actions.py 的规矩如实报告:
- 纯逻辑测试不依赖 PyQt5: 三选一 helper(phase1.py, 标准库 only) +
  对 config.py / pet.py / frames.py 的源码结构断言(AST/文本, 不 import Qt)。
  这样即使本机没装 PyQt5, 三选一分布、图名常量、台词池、帧优先级、
  互斥守卫这些也能被测到。
- 离屏 GUI 测试需要 PyQt5(QT_QPA_PLATFORM=offscreen), 缺失时自动跳过,
  不冒充通过: 四张图加载、帧名循环(一轮约 1.8 秒)、手动触发回 idle、
  吃东西互斥、缺图降级、待机随机三选一及图缺时回落到伸懒腰/打哈欠。
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
pick_idle_action = _phase1.pick_idle_action

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


class PawPickLogicTests(unittest.TestCase):
    """三选一分布: 纯函数, 不依赖 PyQt5。"""

    def test_pick_idle_action_splits_in_thirds(self):
        self.assertEqual(pick_idle_action(0.0), "stretch")
        self.assertEqual(pick_idle_action(0.32), "stretch")
        self.assertEqual(pick_idle_action(1 / 3), "yawn")
        self.assertEqual(pick_idle_action(0.65), "yawn")
        self.assertEqual(pick_idle_action(2 / 3), "paw")
        self.assertEqual(pick_idle_action(0.99), "paw")

    def test_pick_idle_action_bad_input(self):
        self.assertEqual(pick_idle_action("bad"), "stretch")
        self.assertEqual(pick_idle_action(None), "stretch")


class PawConfigTests(unittest.TestCase):
    """config.py 常量与台词: AST 读源码, 不 import PyQt5。"""

    def test_image_constants(self):
        tree = _load_module_ast("config.py")
        got = _assigned_lists(tree, {"IMG_PAW", "LINES_PAW"})
        self.assertEqual(got["IMG_PAW"], ["cat_paw_1.png", "cat_paw_2.png",
                                          "cat_paw_3.png", "cat_paw_4.png"])
        self.assertGreaterEqual(len(got["LINES_PAW"]), 3)
        for line in got["LINES_PAW"]:
            self.assertIsInstance(line, str)
            self.assertTrue(line.strip())
        # 台词保持六一口吻, 别写成鲸鱼
        all_lines = " ".join(got["LINES_PAW"])
        self.assertNotIn("鲸", all_lines)
        self.assertNotIn("大肥鱼", all_lines)

    def test_no_new_pet_json_keys(self):
        """扒拉文件不新增 pet.json 键: DEFAULTS 里不能出现 paw。"""
        tree = _load_module_ast("config.py")
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name) \
                    and node.targets[0].id == "DEFAULTS" \
                    and isinstance(node.value, ast.Dict):
                keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
        self.assertFalse(any("paw" in k for k in keys))
        example = json.loads((ROOT / "pet.example.json").read_text(encoding="utf-8"))
        self.assertEqual(set(example), set(keys))
        self.assertFalse(any("paw" in k for k in example))


class PawCodeStructureTests(unittest.TestCase):
    """pet.py / frames.py 的接线方式: 源码结构断言, 不 import Qt。"""

    def test_frame_priority_order(self):
        src = (ROOT / "ragdollpet" / "frames.py").read_text(encoding="utf-8")
        eat_at = src.index('self._eating')
        paw_at = src.index('self._action_kind == "paw"')
        pet_at = src.index('self._petting')
        sleep_at = src.index('self.state in ("sleep", "deep_sleep")')
        # 吃东西 > 扒拉文件 > 被摸 > 睡觉(和伸懒腰/打哈欠放一起)
        self.assertLess(eat_at, paw_at)
        self.assertLess(paw_at, pet_at)
        self.assertLess(pet_at, sleep_at)
        # 帧名带动作名 + 序号 + 朝向(mask 缓存按名字索引)
        self.assertIn('f"paw', src)

    def test_paw_wiring_present(self):
        src = (ROOT / "ragdollpet" / "pet.py").read_text(encoding="utf-8")
        self.assertIn("def _start_paw", src)
        self.assertIn("def _manual_paw", src)
        self.assertIn('"扒拉一下文件"', src)
        self.assertIn('"paw": self._start_paw', src)
        # _start_action 按 kind 挑帧池, 扒拉走 pix_paw
        self.assertIn('"paw": self.pix_paw', src)
        # 扒拉默认播 1 轮(四帧约 1.8 秒), 不是两轮
        self.assertIn("def _start_paw(self, rounds: int = 1)", src)

    def test_paw_images_exist_and_are_valid_frames(self):
        """4 张新图都在 image/ 里, 且是 600x600 透明 RGBA(用 Pillow 验, 不依赖 Qt)。"""
        from PIL import Image
        for name in ("cat_paw_1.png", "cat_paw_2.png",
                     "cat_paw_3.png", "cat_paw_4.png"):
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
class PawGuiTests(unittest.TestCase):
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
        self.assertEqual(len(self.pet.pix_paw), 4)
        self.assertEqual(len(self.pet.pix_paw_l), 4)
        for pm in self.pet.pix_paw:
            self.assertFalse(pm.isNull())

    def test_paw_frame_cycle_one_round_returns_to_idle(self):
        self.assertTrue(self.pet._start_paw())
        self.assertEqual(self.pet._action_kind, "paw")
        # 四帧播 1 轮: 4 个 tick, 约 1.8 秒(4 x 450ms)
        self.assertEqual(self.pet._action_left, 4)
        self.assertTrue(self.pet._frame()[0].startswith("paw"))
        self.pet._on_action_tick()
        self.assertTrue(self.pet._frame()[0].startswith("paw_1"))
        self._drive_action_to_end()
        self.assertIn(self.pet._frame()[0], ("idle", "idle_l"))

    def test_paw_flipped_name(self):
        self.pet.facing = -1
        self.assertTrue(self.pet._start_paw())
        name = self.pet._frame()[0]
        self.assertTrue(name.startswith("paw_l"), name)
        self._drive_action_to_end()

    def test_eat_and_paw_are_mutually_exclusive(self):
        self.pet._start_eat()
        self.assertTrue(self.pet._eating)
        self.assertFalse(self.pet._start_paw())
        # 吃完再扒拉就可以; 扒拉时喂零食抢不进来
        for _ in range(10):
            if not self.pet._eating:
                break
            self.pet._on_eat_tick()
        self.assertTrue(self.pet._start_paw())
        self.pet._start_eat()
        self.assertFalse(self.pet._eating)
        self.assertEqual(self.pet._action_kind, "paw")
        self._drive_action_to_end()

    def test_manual_menu_action(self):
        menu = self.pet._build_menu()
        texts = [a.text() for a in menu.actions()]
        self.assertIn("扒拉一下文件", texts)
        self.pet._manual_paw()
        self.assertEqual(self.pet._action_kind, "paw")
        # 做动作时再点伸懒腰, 不重复触发、不串动作
        self.pet._manual_stretch()
        self.assertEqual(self.pet._action_kind, "paw")
        self._drive_action_to_end()
        self.pet._manual_stretch()
        self.assertEqual(self.pet._action_kind, "stretch")
        self._drive_action_to_end()

    def test_missing_images_degrade_gracefully(self):
        self.pet.pix_paw = []
        self.assertFalse(self.pet._start_paw())
        self.assertFalse(self.pet._doing_action())
        self.assertIn(self.pet._frame()[0], ("idle", "idle_l"))

    def test_idle_random_can_pick_paw(self):
        self.pet.last_interact = time.monotonic() - 100   # 待机超 90 秒
        self.pet._last_action_at = time.monotonic() - 400  # 上次动作超 5 分钟
        # 第一次 random 是触发判定(0.0 < 0.08 过), 第二次是三选一(0.9 -> 扒拉)
        with mock.patch.object(pet_mod.random, "random",
                               side_effect=[0.0, 0.9]):
            self.pet._maybe_idle_action()
        self.assertTrue(self.pet._doing_action())
        self.assertEqual(self.pet._action_kind, "paw")
        self._drive_action_to_end()

    def test_idle_random_falls_back_when_paw_missing(self):
        self.pet.pix_paw = []
        self.pet.last_interact = time.monotonic() - 100
        self.pet._last_action_at = time.monotonic() - 400
        # 选中扒拉但图缺了, 回落到伸懒腰/打哈欠, 不空手而归也不崩
        with mock.patch.object(pet_mod.random, "random",
                               side_effect=[0.0, 0.9]):
            self.pet._maybe_idle_action()
        self.assertTrue(self.pet._doing_action())
        self.assertIn(self.pet._action_kind, ("stretch", "yawn"))
        self._drive_action_to_end()


if __name__ == "__main__":
    unittest.main()
