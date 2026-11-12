# -*- coding: utf-8 -*-
"""抛掷测试: 拖拽松手后沿抛物线下落, 落回起抛点同一高度, 全程不出屏幕。

分两层, 按 PR #4 / PR #5 的规矩如实报告:
- 纯逻辑测试不依赖 PyQt5: 抛物线规划(plan_throw / throw_pos /
  drag_velocity, phase1.py, 标准库 only) + 对 config.py / pet.py /
  frames.py 的源码结构断言(AST/文本, 不 import Qt)。这样即使本机
  没装 PyQt5, 落点同高、落点不出屏幕、顶点不出屏幕、速度不够不抛、
  下抛给上抛初速度这些也能被测到。
- 离屏 GUI 测试需要 PyQt5(QT_QPA_PLATFORM=offscreen), 缺失时自动跳过,
  不冒充通过: 4 张图加载、抛掷全流程(飞/落地/晕乎/回待机)、落点与
  起抛点同高、飞行中抓住取消、动画关了/省电/锁定时不抛、缺图降级、
  飞行时不走路不做程序化呼吸。
"""

import ast
import json
import math
import os
import sys
import tempfile
import time
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
(plan_throw, throw_pos, drag_velocity) = (
    _phase1.plan_throw, _phase1.throw_pos, _phase1.drag_velocity)
(THROW_MIN_SPEED, THROW_GRAVITY, THROW_MIN_UP_VY) = (
    _phase1.THROW_MIN_SPEED, _phase1.THROW_GRAVITY, _phase1.THROW_MIN_UP_VY)

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
                and node.targets[0].id in names:
            value = node.value
            if isinstance(value, ast.Constant):
                out[node.targets[0].id] = value.value
            elif isinstance(value, ast.UnaryOp) \
                    and isinstance(value.op, ast.USub) \
                    and isinstance(value.operand, ast.Constant):
                # 负数常量(如 THROW_MIN_UP_VY = -300)
                out[node.targets[0].id] = -value.operand.value
    return out


AREA = (0, 0, 1920, 1040)          # 测试用可用区域
W, H = 220, 284                    # scale 1.0 时的窗口尺寸


class ThrowPlanTests(unittest.TestCase):
    """plan_throw / throw_pos / drag_velocity: 纯函数, 不依赖 PyQt5。"""

    def test_landing_same_height_as_start(self):
        # 落点必须与起抛点同一高度: y(T) == y0
        for vx, vy in ((1000, -300), (-800, -600), (0, -900), (1500, 200)):
            plan = plan_throw(500, 800, W, H, vx, vy, AREA)
            self.assertIsNotNone(plan, (vx, vy))
            x, y = throw_pos(plan["x0"], plan["y0"], plan["vx"],
                             plan["vy"], plan["duration"])
            self.assertAlmostEqual(y, 800, places=6, msg=(vx, vy))
            self.assertAlmostEqual(x, plan["x_land"], places=6)

    def test_landing_inside_screen_when_flung_hard(self):
        # 往右猛甩: 落点 x 被夹在屏幕内, 方向不变、速度被等比缩小
        plan = plan_throw(1500, 800, W, H, 6000, -300, AREA)
        self.assertIsNotNone(plan)
        self.assertLessEqual(plan["x_land"], 1920 - W - 8)
        self.assertGreater(plan["vx"], 0)
        self.assertLess(plan["vx"], 6000)
        # 往左猛甩: 同样夹在屏幕内
        plan = plan_throw(300, 800, W, H, -6000, -300, AREA)
        self.assertIsNotNone(plan)
        self.assertGreaterEqual(plan["x_land"], 8)
        self.assertLess(plan["vx"], 0)

    def test_apex_inside_screen_when_flung_up_hard(self):
        # 往上猛甩: 顶点被夹在屏幕内, |vy| 被减小, 落点仍与起点同高
        plan = plan_throw(500, 300, W, H, 800, -2000, AREA)
        self.assertIsNotNone(plan)
        self.assertGreaterEqual(plan["apex_y"], 8)
        self.assertLess(abs(plan["vy"]), 2000)
        x, y = throw_pos(plan["x0"], plan["y0"], plan["vx"],
                         plan["vy"], plan["duration"])
        self.assertAlmostEqual(y, 300, places=6)

    def test_whole_arc_inside_screen(self):
        # x(t) 在起点和落点之间单调, y(t) 在顶点和 y0 之间:
        # 逐点采样, 全程都在屏幕内
        plan = plan_throw(200, 600, W, H, 2500, -1200, AREA)
        self.assertIsNotNone(plan)
        steps = 40
        for i in range(steps + 1):
            t = plan["duration"] * i / steps
            x, y = throw_pos(plan["x0"], plan["y0"], plan["vx"],
                             plan["vy"], t)
            self.assertGreaterEqual(x, 0, msg=(i, x))
            self.assertLessEqual(x + W, 1920, msg=(i, x))
            self.assertGreaterEqual(y, 0, msg=(i, y))
            self.assertLessEqual(y, 1040, msg=(i, y))

    def test_too_slow_does_not_throw(self):
        # 速度不够阈值: 不抛
        self.assertIsNone(plan_throw(500, 800, W, H, 100, 0, AREA))
        self.assertIsNone(plan_throw(500, 800, W, H, 0, -400, AREA))
        self.assertIsNone(plan_throw(500, 800, W, H, 300, 300, AREA))
        # 刚好过线: 抛
        self.assertIsNotNone(plan_throw(500, 800, W, H, 600, 0, AREA))

    def test_downward_flick_gets_upward_velocity(self):
        # 下抛/平抛: 垂直分量被改成向上的小初速度, 还是先上抛再落回 y0
        plan = plan_throw(500, 800, W, H, 1000, 500, AREA)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["vy"], THROW_MIN_UP_VY)
        plan = plan_throw(500, 800, W, H, 1000, 0, AREA)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["vy"], THROW_MIN_UP_VY)
        # 本来就向上: 保持原值
        plan = plan_throw(500, 800, W, H, 1000, -800, AREA)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["vy"], -800)

    def test_no_room_above_top_edge_does_not_throw(self):
        # 起点离顶边太近、连一个小上抛都装不下: 不抛, 走老行为
        self.assertIsNone(plan_throw(500, 5, W, H, 1000, -500, AREA))
        self.assertIsNone(plan_throw(500, 0, W, H, 1000, -500, AREA))

    def test_throw_pos_parabola(self):
        # x 线性、y 是抛物线: 中点在最高点附近, y 小于 y0
        x0, y0, vx, vy = 100, 500, 800, -600
        duration = -2 * vy / THROW_GRAVITY
        mid_x, mid_y = throw_pos(x0, y0, vx, vy, duration / 2)
        self.assertAlmostEqual(mid_x, x0 + vx * duration / 2)
        self.assertAlmostEqual(mid_y, y0 - vy * vy / (2 * THROW_GRAVITY))
        self.assertLess(mid_y, y0)
        # t=0 回到起点
        self.assertEqual(throw_pos(x0, y0, vx, vy, 0), (100.0, 500.0))

    def test_drag_velocity(self):
        # 90ms 内横向 90px、纵向 -45px: vx=1000, vy=-500
        now = time.monotonic()
        samples = [(now - 0.09, 0, 0), (now - 0.045, 45, -22),
                   (now, 90, -45)]
        vx, vy = drag_velocity(samples)
        self.assertAlmostEqual(vx, 1000, delta=1)
        self.assertAlmostEqual(vy, -500, delta=1)
        # 超过 100ms 的旧样本不算: 只看最后 100ms 内的
        samples = [(now - 0.5, 0, 0), (now - 0.09, 1000, 0), (now, 1090, 0)]
        vx, vy = drag_velocity(samples)
        self.assertAlmostEqual(vx, 1000, delta=1)
        # 样本不足 / 跨度太小 / 输入非法: 回 (0, 0)
        self.assertEqual(drag_velocity([(now, 0, 0)]), (0.0, 0.0))
        self.assertEqual(drag_velocity([]), (0.0, 0.0))
        self.assertEqual(drag_velocity([(now, 0, 0), (now, 10, 0)]),
                         (0.0, 0.0))
        self.assertEqual(drag_velocity(None), (0.0, 0.0))
        self.assertEqual(drag_velocity("nope"), (0.0, 0.0))

    def test_plan_throw_bad_input_returns_none(self):
        self.assertIsNone(plan_throw(None, 800, W, H, 1000, -300, AREA))
        self.assertIsNone(plan_throw(500, 800, W, H, 1000, -300, "nope"))
        self.assertIsNone(plan_throw(500, 800, 0, H, 1000, -300, AREA))


class ThrowConfigTests(unittest.TestCase):
    """config.py 常量: AST 读源码, 不 import PyQt5。"""

    def test_image_constants(self):
        tree = _load_module_ast("config.py")
        got = _assigned_lists(tree, {"IMG_THROW"})
        self.assertEqual(got["IMG_THROW"],
                         ["cat_throw_1.png", "cat_throw_2.png"])
        consts = _assigned_consts(
            tree, {"IMG_LAND", "IMG_DIZZY", "THROW_MS", "LAND_MS", "DIZZY_MS"})
        self.assertEqual(consts["IMG_LAND"], "cat_land_1.png")
        self.assertEqual(consts["IMG_DIZZY"], "cat_dizzy_1.png")
        self.assertEqual(consts["THROW_MS"], 120)
        self.assertEqual(consts["LAND_MS"], 250)
        self.assertEqual(consts["DIZZY_MS"], 1200)

    def test_dizzy_lines(self):
        tree = _load_module_ast("config.py")
        got = _assigned_lists(tree, {"LINES_DIZZY"})
        self.assertEqual(len(got["LINES_DIZZY"]), 3)
        for line in got["LINES_DIZZY"]:
            self.assertTrue(line.strip())

    def test_throw_physics_constants(self):
        tree = _load_module_ast("phase1.py")
        consts = _assigned_consts(
            tree, {"THROW_MIN_SPEED", "THROW_GRAVITY", "THROW_MIN_UP_VY"})
        self.assertEqual(consts["THROW_MIN_SPEED"], 500)
        self.assertEqual(consts["THROW_GRAVITY"], 2400)
        self.assertEqual(consts["THROW_MIN_UP_VY"], -300)

    def test_no_new_pet_json_keys(self):
        """抛掷不新增 pet.json 键: DEFAULTS 里不能出现 throw/dizzy。"""
        tree = _load_module_ast("config.py")
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name) \
                    and node.targets[0].id == "DEFAULTS" \
                    and isinstance(node.value, ast.Dict):
                keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
        self.assertFalse(any("throw" in k or "dizzy" in k for k in keys))
        # pet.example.json 同样不能多出新键(与 DEFAULTS 一致)
        example = json.loads((ROOT / "pet.example.json").read_text(encoding="utf-8"))
        self.assertEqual(set(example), set(keys))
        self.assertFalse(any("throw" in k or "dizzy" in k for k in example))


class ThrowCodeStructureTests(unittest.TestCase):
    """pet.py / frames.py 的接线方式: 源码结构断言, 不 import Qt。"""

    def test_frame_priority_throw_below_hold_above_jump(self):
        src = (ROOT / "ragdollpet" / "frames.py").read_text(encoding="utf-8")
        hold_at = src.index('if self._holding and self.pix_hold is not None:')
        throw_at = src.index('if getattr(self, "_throwing", False):')
        jump_at = src.index('if (self._jumping or time.monotonic() < self._angry_until)')
        walk_at = src.index('self.state == "walk"')
        # 抛掷在被拎起之下、跳跃/走路/待机之上
        self.assertLess(hold_at, throw_at)
        self.assertLess(throw_at, jump_at)
        self.assertLess(throw_at, walk_at)
        # 三个阶段的名字都要有
        for name in ('"throw', '"land', '"dizzy'):
            self.assertIn(name, src[throw_at:throw_at + 2500], name)

    def test_throw_methods_and_wiring_present(self):
        src = (ROOT / "ragdollpet" / "pet.py").read_text(encoding="utf-8")
        for name in ("def _try_throw", "def _on_throw_tick",
                     "def _cancel_throw", "def _end_throw"):
            self.assertIn(name, src)
        self.assertIn("plan_throw(", src)
        self.assertIn("throw_pos(", src)
        self.assertIn("drag_velocity(", src)
        # 松手时试抛, 抛起来就不再贴边/保存(落地时再做)
        release = src[src.index("def mouseReleaseEvent"):]
        self.assertIn("elif self._try_throw():", release[:1200])
        # 飞行中抓住取消抛掷; 拖动时记指针位置
        press = src[src.index("def mousePressEvent"):]
        self.assertIn("self._cancel_throw()", press[:800])
        move = src[src.index("def mouseMoveEvent"):]
        self.assertIn("self._throw_samples.append(", move[:1800])
        # 退出时停抛掷定时器; 关动画/省电/重置时落下来
        self.assertIn("self._throw_timer", src)
        self.assertIn("self._throw_timer.start(FRAME_MS)", src)
        self.assertIn("self._end_throw()", src)

    def test_throw_guards_present(self):
        src = (ROOT / "ragdollpet" / "pet.py").read_text(encoding="utf-8")
        # 飞行时不睡觉、不随机动作、不走路、不做程序化呼吸
        self.assertIn("not self._throwing", src)
        update = src[src.index("def _update_breath"):]
        self.assertIn("or self._throwing:", update[:600])
        # 呼吸帧推进也被抛掷拦住
        self.assertIn("self._throwing):", src)

    def test_new_images_exist_and_are_valid_frames(self):
        """4 张新图都在 image/ 里, 且是 600x600 透明 RGBA(用 Pillow 验, 不依赖 Qt)。"""
        from PIL import Image
        for name in ("cat_throw_1.png", "cat_throw_2.png",
                     "cat_land_1.png", "cat_dizzy_1.png"):
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
class ThrowGuiTests(unittest.TestCase):
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

    def _fast_samples(self, vx=1000, vy=-500):
        """90ms 内按 (vx, vy) 匀速拖过的指针记录。"""
        now = time.monotonic()
        dt = 0.09
        return [(now - dt, 0, 0), (now - dt / 2, vx * dt / 2, vy * dt / 2),
                (now, vx * dt, vy * dt)]

    def _start_throw(self, vx=1000, vy=-500):
        self.pet._throw_samples = self._fast_samples(vx, vy)
        self.assertTrue(self.pet._try_throw())
        self.assertTrue(self.pet._throwing)
        self.assertEqual(self.pet._throw_phase, "fly")
        return self.pet._throw_plan

    def test_images_load(self):
        self.assertEqual(len(self.pet.pix_throw), 2)
        self.assertEqual(len(self.pet.pix_throw_l), 2)
        self.assertIsNotNone(self.pet.pix_land)
        self.assertIsNotNone(self.pet.pix_dizzy)
        self.assertFalse(self.pet.pix_land.isNull())
        self.assertFalse(self.pet.pix_dizzy.isNull())
        for pm in self.pet.pix_throw:
            self.assertFalse(pm.isNull())

    def test_full_throw_lands_at_start_height(self):
        plan = self._start_throw()
        x0, y0 = self.pet.x(), self.pet.y()
        self.assertEqual(plan["x0"], x0)
        self.assertEqual(plan["y0"], y0)
        # 逐步推进, 直到抛掷结束(飞行 -> 落地 -> 晕乎 -> 待机)
        seen = set()
        for _ in range(300):
            if not self.pet._throwing:
                break
            self.pet._on_throw_tick()
            seen.add(self.pet._frame()[0].split("_")[0])
            # 飞行中 y 先上后下, 但始终在起点和顶点之间
            self.assertGreaterEqual(self.pet.y(), 0)
        self.assertFalse(self.pet._throwing)
        self.assertEqual(self.pet._frame()[0], "idle")
        # 落点与起抛点同一高度(贴边只动 x 不动 y, 除非本来就贴边)
        self.assertEqual(self.pet.y(), y0)
        # 落点在屏幕内
        area = self.pet._screen().availableGeometry()
        self.assertGreaterEqual(self.pet.x(), area.left())
        self.assertLessEqual(self.pet.x() + self.pet.width(), area.right())
        self.assertIn("throw", seen)

    def test_flight_frames_alternate_and_land_dizzy_show(self):
        self._start_throw()
        names = []
        for _ in range(60):
            if not self.pet._throwing:
                break
            self.pet._on_throw_tick()
            names.append(self.pet._frame()[0])
        # 飞行帧在 throw_0/throw_1 之间交替, 之后出现 land、dizzy
        throw_names = [n for n in names if n.startswith("throw")]
        self.assertTrue(throw_names)
        self.assertIn("throw_0", throw_names)
        self.assertIn("throw_1", throw_names)
        self.assertIn("land", names)
        self.assertIn("dizzy", names)

    def test_slow_drag_does_not_throw(self):
        self.pet._throw_samples = self._fast_samples(vx=100, vy=0)
        self.assertFalse(self.pet._try_throw())
        self.assertFalse(self.pet._throwing)

    def test_no_throw_when_anim_off_power_saver_or_locked(self):
        self.pet.anim_enabled = False
        self.pet._throw_samples = self._fast_samples()
        self.assertFalse(self.pet._try_throw())
        self.pet.anim_enabled = True
        self.pet.power_saver = True
        self.assertFalse(self.pet._try_throw())
        self.pet.power_saver = False
        self.pet.locked = True
        self.assertFalse(self.pet._try_throw())
        self.pet.locked = False
        self.assertTrue(self.pet._try_throw())

    def test_no_throw_while_eating(self):
        self.pet._start_eat()
        self.assertTrue(self.pet._eating)
        self.pet._throw_samples = self._fast_samples()
        self.assertFalse(self.pet._try_throw())

    def test_cancel_throw_on_grab(self):
        self._start_throw()
        self.pet._cancel_throw()
        self.assertFalse(self.pet._throwing)
        self.assertIsNone(self.pet._throw_plan)
        self.assertFalse(self.pet._throw_timer.isActive())
        self.assertEqual(self.pet._frame()[0], "idle")

    def test_walk_does_not_start_while_throwing(self):
        self._start_throw()
        self.pet._start_walk()
        self.assertNotEqual(self.pet.state, "walk")
        self.assertFalse(self.pet._walk_timer.isActive())

    def test_no_programmatic_breath_while_throwing(self):
        # 飞行时程序化呼吸(±3px 位移)停掉; 图缺了本来会跑程序化呼吸
        self.pet.pix_idle_breath = []
        self.pet.pix_idle_breath_l = []
        self._start_throw()
        self.pet._update_breath()
        self.assertEqual(self.pet.breath_group.state(),
                         QAbstractAnimation.Stopped)
        self.pet._cancel_throw()
        self.pet._update_breath()
        self.assertEqual(self.pet.breath_group.state(),
                         QAbstractAnimation.Running)

    def test_missing_images_degrade_gracefully(self):
        # 飞行图缺了: 照样飞, 帧退回被拎起/待机, 不崩
        self.pet.pix_throw = []
        self.pet.pix_throw_l = []
        self._start_throw()
        for _ in range(300):
            if not self.pet._throwing:
                break
            self.pet._on_throw_tick()
            self.assertIn(self.pet._frame()[0],
                          ("hold", "hold_l", "idle", "idle_l",
                           "land", "land_l", "dizzy", "dizzy_l"))
        self.assertFalse(self.pet._throwing)
        # 落地/晕乎图缺了: 阶段照样走完, 直接回待机
        self.pet.pix_land = None
        self.pet.pix_land_l = None
        self.pet.pix_dizzy = None
        self.pet.pix_dizzy_l = None
        self._start_throw()
        for _ in range(300):
            if not self.pet._throwing:
                break
            self.pet._on_throw_tick()
        self.assertFalse(self.pet._throwing)
        self.assertEqual(self.pet._frame()[0], "idle")


if __name__ == "__main__":
    unittest.main()
