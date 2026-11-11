# -*- coding: utf-8 -*-
"""呼吸帧测试: 待机 / 睡觉呼吸循环 + 走路 8 帧。

分两层, 按 PR #4 的规矩如实报告:
- 纯逻辑测试不依赖 PyQt5: 呼吸循环 helper(phase1.py, 标准库 only) +
  对 config.py / pet.py / frames.py 的源码结构断言(AST/文本, 不 import Qt)。
  这样即使本机没装 PyQt5, 循环顺序、缺图降级、每帧时长这些也能被测到。
- 离屏 GUI 测试需要 PyQt5(QT_QPA_PLATFORM=offscreen), 缺失时自动跳过,
  不冒充通过: 两组图加载、走路 8 帧一轮约 1 秒、待机/睡觉呼吸循环顺序、
  眨眼盖住呼吸帧、动画关了回单张图、缺图降级、程序化呼吸不再叠加。
"""

import ast
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

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
(next_breath_index, should_advance_breath) = (
    _phase1.next_breath_index, _phase1.should_advance_breath)

try:
    from PyQt5.QtCore import QAbstractAnimation, QSettings
    from PyQt5.QtWidgets import QApplication
    from ragdollpet.config import FRAME_MS, Store
    from ragdollpet.pet import DesktopPet
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


def _assigned_consts(tree, names):
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in names \
                and isinstance(node.value, ast.Constant):
            out[node.targets[0].id] = node.value.value
    return out


class BreathLogicTests(unittest.TestCase):
    """循环下标 / 推进守卫: 纯函数, 不依赖 PyQt5。"""

    def test_next_breath_index_cycles_three_frames(self):
        # 基础帧 0 -> 吸气 1 -> 呼气 2 -> 回基础帧 0
        self.assertEqual(next_breath_index(0, 3), 1)
        self.assertEqual(next_breath_index(1, 3), 2)
        self.assertEqual(next_breath_index(2, 3), 0)

    def test_next_breath_index_single_frame_pool_stays_zero(self):
        # 图缺了池子里只剩基础帧: 永远停在 0, 不循环
        self.assertEqual(next_breath_index(0, 1), 0)
        self.assertEqual(next_breath_index(2, 1), 0)
        self.assertEqual(next_breath_index(0, 0), 0)

    def test_next_breath_index_bad_input_returns_zero(self):
        self.assertEqual(next_breath_index(None, 3), 0)
        self.assertEqual(next_breath_index(0, "three"), 0)

    def test_should_advance_breath_idle_and_sleep_only(self):
        base = dict(anim_enabled=True, power_saver=False, eating=False,
                    doing_action=False, petting=False, holding=False,
                    jumping=False)
        for state in ("idle", "sleep", "deep_sleep"):
            self.assertTrue(should_advance_breath(state, **base), state)
        # 走路 / 别的忙的时候不推进
        self.assertFalse(should_advance_breath("walk", **base))
        for key in ("eating", "doing_action", "petting", "holding", "jumping"):
            kw = dict(base, **{key: True})
            self.assertFalse(should_advance_breath("idle", **kw), key)
        # 动画关了 / 省电不推进
        self.assertFalse(should_advance_breath(
            "idle", **dict(base, anim_enabled=False)))
        self.assertFalse(should_advance_breath(
            "sleep", **dict(base, power_saver=True)))


class BreathConfigTests(unittest.TestCase):
    """config.py 常量: AST 读源码, 不 import PyQt5。"""

    def test_image_constants(self):
        tree = _load_module_ast("config.py")
        got = _assigned_lists(tree, {"IMG_IDLE_BREATH", "IMG_SLEEP_BREATH"})
        self.assertEqual(got["IMG_IDLE_BREATH"],
                         ["cat_idle_breath_1.png", "cat_idle_breath_2.png"])
        self.assertEqual(got["IMG_SLEEP_BREATH"],
                         ["cat_sleep_breath_1.png", "cat_sleep_breath_2.png"])

    def test_breath_ms_is_slow(self):
        # 呼吸要慢: 每帧约 1.2 秒, 比吃东西(EAT_MS=180)慢得多
        tree = _load_module_ast("config.py")
        values = _assigned_consts(tree, {"BREATH_MS", "EAT_MS"})
        self.assertEqual(values["BREATH_MS"], 1200)
        self.assertGreater(values["BREATH_MS"], values["EAT_MS"] * 5)

    def test_no_new_pet_json_keys(self):
        """呼吸帧不新增 pet.json 键: DEFAULTS 里不能出现 breath。"""
        tree = _load_module_ast("config.py")
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name) \
                    and node.targets[0].id == "DEFAULTS" \
                    and isinstance(node.value, ast.Dict):
                keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
        self.assertFalse(any("breath" in k for k in keys))
        # pet.example.json 同样不能多出新键(与 DEFAULTS 一致)
        example = json.loads((ROOT / "pet.example.json").read_text(encoding="utf-8"))
        self.assertEqual(set(example), set(keys))
        self.assertFalse(any("breath" in k for k in example))


class BreathCodeStructureTests(unittest.TestCase):
    """pet.py / frames.py 的接线方式: 源码结构断言, 不 import Qt。"""

    def test_frame_priority_order(self):
        src = (ROOT / "ragdollpet" / "frames.py").read_text(encoding="utf-8")
        sleep_at = src.index('self.state in ("sleep", "deep_sleep")')
        sleep_breath_at = src.index("sleep_breath_")
        walk_at = src.index('self.state == "walk"')
        blink_at = src.index("self.blink and self.anim_enabled")
        idle_breath_at = src.index("idle_breath")
        # 睡觉呼吸在睡觉分支里、走路之前; 待机呼吸在眨眼之后(眨眼盖得住呼吸帧)
        self.assertLess(sleep_at, sleep_breath_at)
        self.assertLess(sleep_breath_at, walk_at)
        self.assertLess(blink_at, idle_breath_at)
        # 下标 0 是基础帧, 名字保持 "idle"/"sleep" 不变(老测试不断言失效)
        self.assertIn('return ("sleep", pool[0])', src)
        self.assertIn('return "idle", pool[0]', src)

    def test_breath_tick_and_helpers_present(self):
        src = (ROOT / "ragdollpet" / "pet.py").read_text(encoding="utf-8")
        self.assertIn("def _on_breath_tick", src)
        self.assertIn("def _breath_pool", src)
        self.assertIn("def _has_breath_frames", src)
        self.assertIn("should_advance_breath(", src)
        self.assertIn("next_breath_index(", src)
        # 有真实呼吸帧时 _update_breath 不再叠加程序化位移
        update = src[src.index("def _update_breath"):]
        self.assertIn("if self._has_breath_frames():", update[:800])
        # 换状态从基础帧重新开始; 退出时停呼吸定时器
        self.assertIn("self._breath_index = 0            # 换状态从基础帧重新开始呼吸循环",
                      src)
        self.assertIn("self._breath_timer", src)
        self.assertIn("self._breath_timer.start(BREATH_MS)", src)

    def test_new_images_exist_and_are_valid_frames(self):
        """8 张新图都在 image/ 里, 且是 600x600 透明 RGBA(用 Pillow 验, 不依赖 Qt)。"""
        from PIL import Image
        for name in ("cat_walk_5.png", "cat_walk_6.png",
                     "cat_walk_7.png", "cat_walk_8.png",
                     "cat_idle_breath_1.png", "cat_idle_breath_2.png",
                     "cat_sleep_breath_1.png", "cat_sleep_breath_2.png"):
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
class BreathGuiTests(unittest.TestCase):
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

    def test_images_load(self):
        self.assertEqual(len(self.pet.pix_idle_breath), 2)
        self.assertEqual(len(self.pet.pix_sleep_breath), 2)
        self.assertEqual(len(self.pet.pix_idle_breath_l), 2)
        for pm in self.pet.pix_idle_breath + self.pet.pix_sleep_breath:
            self.assertFalse(pm.isNull())

    def test_walk_eight_frames_about_one_second_per_cycle(self):
        self.assertEqual(len(self.pet.pix_walk), 8)
        # 8 帧时每帧 4 tick: 4 tick x 33ms x 8 帧 = 约 1.06 秒, 和 4 帧时一样
        self.assertEqual(self.pet._walk_step_ticks, 4)
        cycle_ms = self.pet._walk_step_ticks * FRAME_MS * len(self.pet.pix_walk)
        self.assertGreaterEqual(cycle_ms, 900)
        self.assertLessEqual(cycle_ms, 1200)

    def test_idle_breath_cycle_order(self):
        self.assertEqual(self.pet.state, "idle")
        self.assertEqual(self.pet._frame()[0], "idle")
        self.pet._on_breath_tick()
        self.assertEqual(self.pet._frame()[0], "idle_breath_1")
        self.pet._on_breath_tick()
        self.assertEqual(self.pet._frame()[0], "idle_breath_2")
        self.pet._on_breath_tick()
        self.assertEqual(self.pet._frame()[0], "idle")

    def test_sleep_breath_cycle_order(self):
        self.pet._set_state("sleep")
        self.assertEqual(self.pet._frame()[0], "sleep")
        self.pet._on_breath_tick()
        self.assertEqual(self.pet._frame()[0], "sleep_breath_1")
        self.pet._on_breath_tick()
        self.assertEqual(self.pet._frame()[0], "sleep_breath_2")
        self.pet._on_breath_tick()
        self.assertEqual(self.pet._frame()[0], "sleep")

    def test_blink_overrides_breath_frame(self):
        # 眨眼仍能在待机呼吸循环上正常触发
        self.pet._breath_index = 1
        self.assertEqual(self.pet._frame()[0], "idle_breath_1")
        self.pet.blink = True
        self.assertIn(self.pet._frame()[0], ("blink", "blink_l"))
        self.pet.blink = False
        self.assertEqual(self.pet._frame()[0], "idle_breath_1")

    def test_idle_breath_flipped_name(self):
        self.pet.facing = -1
        self.pet._breath_index = 1
        self.assertEqual(self.pet._frame()[0], "idle_breath_l_1")
        self.pet._breath_index = 0
        self.assertEqual(self.pet._frame()[0], "idle_l")

    def test_anim_off_returns_to_single_frame(self):
        self.pet._breath_index = 1
        self.assertEqual(self.pet._frame()[0], "idle_breath_1")
        self.pet.anim_enabled = False
        self.assertEqual(self.pet._frame()[0], "idle")
        self.pet._set_state("sleep")
        self.assertEqual(self.pet._frame()[0], "sleep")

    def test_missing_images_degrade_gracefully(self):
        # 待机呼吸图缺了: 推进也不循环, 帧名还是 idle
        self.pet.pix_idle_breath = []
        self.pet.pix_idle_breath_l = []
        for _ in range(5):
            self.pet._on_breath_tick()
        self.assertEqual(self.pet._frame()[0], "idle")
        # 睡觉呼吸图缺了: 同样退回单张睡觉图
        self.pet.pix_sleep_breath = []
        self.pet._set_state("sleep")
        for _ in range(5):
            self.pet._on_breath_tick()
        self.assertEqual(self.pet._frame()[0], "sleep")

    def test_no_programmatic_breath_when_real_frames_exist(self):
        # 有真实呼吸帧时程序化呼吸(±3px 位移)停掉, 避免双重抖动
        self.assertTrue(self.pet._has_breath_frames())
        self.assertEqual(self.pet.breath_group.state(),
                         QAbstractAnimation.Stopped)
        # 图缺了就恢复程序化呼吸
        self.pet.pix_idle_breath = []
        self.pet._update_breath()
        self.assertEqual(self.pet.breath_group.state(),
                         QAbstractAnimation.Running)

    def test_breath_does_not_advance_while_eating(self):
        self.pet._start_eat()
        self.assertTrue(self.pet._eating)
        self.pet._on_breath_tick()
        self.assertEqual(self.pet._breath_index, 0)
        self.assertEqual(self.pet._frame()[0].split("_")[0], "eat")


if __name__ == "__main__":
    unittest.main()
