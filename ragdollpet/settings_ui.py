# -*- coding: utf-8 -*-
"""统一设置窗口(第一期): 分页签, 设置立即保存到 pet.json。

页签:
    提醒与纪念日  原有字段(喝水/睡觉/纪念日/高 DPI)
    语录          频率四档 + 各类别开关
    定时提醒      每天 / 每周 / 每隔 N 分钟, 可增删改
    模式与窗口    模式 / 层级 / 透明度 / 锁定 / 穿透 / 自启 / 大小档位
    应用与快捷键  应用使用时长提醒 + 启动提醒 + 全局快捷键
    天气          默认城市 + 立即查看

本模块只负责界面和取值; 应用新设置(重排散步、重建快捷键、重刷定时器等)
调用 pet 对象上的 _apply_* 方法, 逻辑仍在 pet.py 里。
"""

from PyQt5.QtCore import QDate, Qt, QTime
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QSlider, QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem, QTimeEdit,
    QVBoxLayout, QWidget, QDateEdit,
)

from .config import PET_NAME
from .phase1 import (
    LAYERS, LAYER_NAMES, MODES, MODE_NAMES, SIZE_PRESETS, SPEECH_CATEGORIES,
    SPEECH_CATEGORY_NAMES, SPEECH_TIER_NAMES, SPEECH_TIERS, parse_reminder,
    validate_hotkeys,
)

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
HOTKEY_KIND_NAMES = {"praise": "夸一句", "comfort": "安慰一句",
                     "tease": "损一句", "custom": "自定义台词"}


# ================================ 定时提醒编辑 ================================
def _edit_reminder_dialog(parent, reminder=None):
    """新增/编辑一条提醒。返回规范化后的 dict, 取消返回 None。"""
    dlg = QDialog(parent)
    dlg.setWindowTitle("编辑定时提醒" if reminder else "新增定时提醒")
    form = QFormLayout(dlg)

    combo_kind = QComboBox()
    combo_kind.addItem("每天指定时间", "daily")
    combo_kind.addItem("每周选几天", "weekly")
    combo_kind.addItem("每隔 N 分钟", "interval")
    time_edit = QTimeEdit()
    time_edit.setDisplayFormat("HH:mm")
    spin_minutes = QSpinBox()
    spin_minutes.setRange(1, 1440)
    spin_minutes.setSuffix(" 分钟")
    spin_minutes.setValue(60)
    weekday_boxes = [QCheckBox(name) for name in WEEKDAY_NAMES]
    weekday_row = QHBoxLayout()
    for box in weekday_boxes:
        weekday_row.addWidget(box)
    weekday_widget = QWidget()
    weekday_widget.setLayout(weekday_row)
    edit_text = QLineEdit()
    edit_text.setPlaceholderText("例如: 该睡了哦～")
    chk_enabled = QCheckBox("启用这条提醒")
    chk_enabled.setChecked(True)

    if reminder:
        kind = reminder.get("kind", "daily")
        combo_kind.setCurrentIndex({"daily": 0, "weekly": 1, "interval": 2}[kind])
        if kind in ("daily", "weekly"):
            hour, minute = (int(p) for p in str(reminder.get("time", "23:30")).split(":"))
            time_edit.setTime(QTime(hour, minute))
        if kind == "weekly":
            for day in reminder.get("weekdays", []):
                if 0 <= int(day) <= 6:
                    weekday_boxes[int(day)].setChecked(True)
        if kind == "interval":
            spin_minutes.setValue(int(reminder.get("minutes", 60)))
        edit_text.setText(str(reminder.get("text", "")))
        chk_enabled.setChecked(bool(reminder.get("enabled", True)))

    def _sync_kind():
        kind = combo_kind.currentData()
        time_edit.setVisible(kind in ("daily", "weekly"))
        weekday_widget.setVisible(kind == "weekly")
        spin_minutes.setVisible(kind == "interval")

    combo_kind.currentIndexChanged.connect(_sync_kind)
    _sync_kind()

    form.addRow("类型", combo_kind)
    form.addRow("时间", time_edit)
    form.addRow("星期", weekday_widget)
    form.addRow("间隔", spin_minutes)
    form.addRow("台词", edit_text)
    form.addRow("", chk_enabled)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    form.addRow(buttons)

    while True:
        if dlg.exec_() != QDialog.Accepted:
            return None
        raw = {
            "id": (reminder or {}).get("id", ""),
            "kind": combo_kind.currentData(),
            "time": time_edit.time().toString("HH:mm"),
            "weekdays": [i for i, box in enumerate(weekday_boxes) if box.isChecked()],
            "minutes": spin_minutes.value(),
            "text": edit_text.text().strip(),
            "enabled": chk_enabled.isChecked(),
            "last_fired": (reminder or {}).get("last_fired", ""),
        }
        try:
            return parse_reminder(raw)
        except ValueError as exc:
            QMessageBox.warning(dlg, "这条提醒写得不对", str(exc))


def _reminder_summary(reminder: dict) -> str:
    kind = reminder.get("kind")
    text = reminder.get("text", "")
    off = "" if reminder.get("enabled", True) else "（已关闭）"
    if kind == "daily":
        return f"每天 {reminder.get('time')}：{text}{off}"
    if kind == "weekly":
        days = "、".join(WEEKDAY_NAMES[int(d)] for d in reminder.get("weekdays", []))
        return f"每周{days} {reminder.get('time')}：{text}{off}"
    return f"每隔 {reminder.get('minutes')} 分钟：{text}{off}"


# ================================ 快捷键编辑 ================================
def _edit_hotkey_dialog(parent):
    """新增一条全局快捷键。返回 dict, 取消返回 None。"""
    from PyQt5.QtWidgets import QInputDialog
    key, ok = QInputDialog.getText(parent, "新增快捷键", "按键（例如 Ctrl+Alt+1）：")
    if not ok or not key.strip():
        return None
    kinds = ["夸一句", "安慰一句", "损一句", "自定义台词"]
    kind_label, ok = QInputDialog.getItem(parent, "新增快捷键", "按下后做什么：", kinds, 0, False)
    if not ok:
        return None
    kind = {"夸一句": "praise", "安慰一句": "comfort",
            "损一句": "tease", "自定义台词": "custom"}[kind_label]
    text = ""
    if kind == "custom":
        text, ok = QInputDialog.getText(parent, "新增快捷键", "自定义台词：")
        if not ok or not text.strip():
            return None
    return {"key": key.strip(), "kind": kind, "text": text.strip(), "enabled": True}


# ================================ 设置窗口 ================================
def open_settings_dialog(pet):
    """打开统一设置窗口; 点确定后写 pet.json 并让 pet 应用新设置。"""
    store = pet.store
    dlg = QDialog(pet)
    dlg.setWindowTitle(f"{PET_NAME} 的设置")
    dlg.resize(520, 460)
    layout = QVBoxLayout(dlg)
    tabs = QTabWidget()
    layout.addWidget(tabs)

    # ---- 页签 1: 提醒与纪念日(原有字段, 原样保留) ----
    tab1 = QWidget()
    form1 = QFormLayout(tab1)
    chk_water = QCheckBox("启用喝水提醒")
    chk_water.setChecked(pet.water_enabled)
    spin_min = QSpinBox()
    spin_min.setRange(1, 600)
    spin_min.setSuffix(" 分钟")
    spin_min.setValue(pet.water_interval)
    spin_sleep = QSpinBox()
    spin_sleep.setRange(1, 60)
    spin_sleep.setSuffix(" 分钟")
    spin_sleep.setValue(pet.sleep_minutes)
    spin_deep = QSpinBox()
    spin_deep.setRange(1, 180)
    spin_deep.setSuffix(" 分钟")
    spin_deep.setValue(pet.deep_sleep_min)
    edit_name = QLineEdit(pet.anni_name)
    edit_name.setPlaceholderText("例如: 我家猫生日 / 考研倒计时")
    edit_date = QDateEdit()
    edit_date.setCalendarPopup(True)
    edit_date.setDisplayFormat("yyyy-MM-dd")
    parsed = QDate.fromString(pet.anni_date, "yyyy-MM-dd") if pet.anni_date else QDate()
    edit_date.setDate(parsed if parsed.isValid() else QDate.currentDate().addDays(30))
    chk_dpi = QCheckBox("启用高 DPI 适配(需重启生效)")
    chk_dpi.setChecked(store.get_bool("high_dpi"))
    form1.addRow("喝水提醒", chk_water)
    form1.addRow("提醒间隔", spin_min)
    form1.addRow("无宠物交互多久睡觉", spin_sleep)
    form1.addRow("全局空闲多久深睡", spin_deep)
    form1.addRow("纪念日名称", edit_name)
    form1.addRow("纪念日日期", edit_date)
    form1.addRow("显示", chk_dpi)
    tabs.addTab(tab1, "提醒与纪念日")

    # ---- 页签 2: 语录 ----
    tab2 = QWidget()
    form2 = QFormLayout(tab2)
    combo_tier = QComboBox()
    for key in ("quiet", "normal", "chatty", "talkative"):
        low, high = SPEECH_TIERS[key]
        combo_tier.addItem(f"{SPEECH_TIER_NAMES[key]}（约 {low}~{high} 秒一句）", key)
    combo_tier.setCurrentIndex(list(SPEECH_TIERS).index(
        pet.speech_tier if pet.speech_tier in SPEECH_TIERS else "normal"))
    form2.addRow("闲聊频率", combo_tier)
    form2.addRow(QLabel("哪些话可以说（关掉的整类不再说）："))
    category_boxes = {}
    categories = store.get_dict("speech_categories")
    for key in SPEECH_CATEGORIES:
        box = QCheckBox(SPEECH_CATEGORY_NAMES[key])
        box.setChecked(bool(categories.get(key, True)))
        category_boxes[key] = box
        form2.addRow("", box)
    form2.addRow(QLabel("触发类的话（被摸、喝水、提醒等）优先于闲聊，不会被闲聊盖掉。"))
    tabs.addTab(tab2, "语录")

    # ---- 页签 3: 定时提醒 ----
    tab3 = QWidget()
    layout3 = QVBoxLayout(tab3)
    list_reminders = QListWidget()
    for reminder in pet.reminders:
        item = QListWidgetItem(_reminder_summary(reminder))
        item.setData(Qt.UserRole, dict(reminder))
        list_reminders.addItem(item)
    layout3.addWidget(list_reminders)
    row3 = QHBoxLayout()
    btn_add_rem = QPushButton("新增")
    btn_edit_rem = QPushButton("编辑")
    btn_del_rem = QPushButton("删除")
    row3.addWidget(btn_add_rem)
    row3.addWidget(btn_edit_rem)
    row3.addWidget(btn_del_rem)
    row3.addStretch()
    layout3.addLayout(row3)

    def _selected_reminder_row():
        return list_reminders.currentRow()

    def _on_add_rem():
        result = _edit_reminder_dialog(dlg)
        if result is None:
            return
        item = QListWidgetItem(_reminder_summary(result))
        item.setData(Qt.UserRole, result)
        list_reminders.addItem(item)

    def _on_edit_rem():
        row = _selected_reminder_row()
        if row < 0:
            return
        item = list_reminders.item(row)
        result = _edit_reminder_dialog(dlg, item.data(Qt.UserRole))
        if result is None:
            return
        item.setData(Qt.UserRole, result)
        item.setText(_reminder_summary(result))

    def _on_del_rem():
        row = _selected_reminder_row()
        if row >= 0:
            list_reminders.takeItem(row)

    btn_add_rem.clicked.connect(_on_add_rem)
    btn_edit_rem.clicked.connect(_on_edit_rem)
    btn_del_rem.clicked.connect(_on_del_rem)
    tabs.addTab(tab3, "定时提醒")

    # ---- 页签 4: 模式与窗口 ----
    tab4 = QWidget()
    form4 = QFormLayout(tab4)
    combo_mode = QComboBox()
    for key in MODES:
        combo_mode.addItem(MODE_NAMES[key], key)
    combo_mode.setCurrentIndex(MODES.index(pet.mode if pet.mode in MODES else "free"))
    combo_layer = QComboBox()
    for key in LAYERS:
        combo_layer.addItem(LAYER_NAMES[key], key)
    combo_layer.setCurrentIndex(LAYERS.index(
        pet.window_layer if pet.window_layer in LAYERS else "top"))
    slider_opacity = QSlider(Qt.Horizontal)
    slider_opacity.setRange(20, 100)
    slider_opacity.setValue(int(pet.opacity * 100))
    label_opacity = QLabel(f"{int(pet.opacity * 100)}%")
    slider_opacity.valueChanged.connect(lambda v: label_opacity.setText(f"{v}%"))
    opacity_row = QHBoxLayout()
    opacity_row.addWidget(slider_opacity)
    opacity_row.addWidget(label_opacity)
    opacity_widget = QWidget()
    opacity_widget.setLayout(opacity_row)
    chk_locked = QCheckBox("锁定位置（拖不动，点它、右键照旧）")
    chk_locked.setChecked(pet.locked)
    chk_through = QCheckBox("鼠标穿透（点不中猫，从托盘右键解除）")
    chk_through.setChecked(pet.click_through)
    chk_autostart = QCheckBox("开机自启（仅 Windows）")
    chk_autostart.setChecked(pet.autostart)
    combo_size = QComboBox()
    combo_size.addItem("自定义（滚轮调的倍数）", "")
    for name, scale in SIZE_PRESETS.items():
        combo_size.addItem(f"{name}（{scale}×）", name)
    current_preset = pet.size_preset if pet.size_preset in SIZE_PRESETS else ""
    combo_size.setCurrentIndex([combo_size.itemData(i) for i in range(combo_size.count())].index(current_preset))
    form4.addRow("模式", combo_mode)
    form4.addRow("窗口层级", combo_layer)
    form4.addRow("透明度", opacity_widget)
    form4.addRow("大小档位", combo_size)
    form4.addRow("", chk_locked)
    form4.addRow("", chk_through)
    form4.addRow("", chk_autostart)
    tabs.addTab(tab4, "模式与窗口")

    # ---- 页签 5: 应用与快捷键 ----
    tab5 = QWidget()
    layout5 = QVBoxLayout(tab5)
    layout5.addWidget(QLabel("应用使用时长提醒（连续用满这么久提醒一次，空闲不计时）："))
    table_apps = QTableWidget(0, 3)
    table_apps.setHorizontalHeaderLabels(["应用（exe 名）", "分钟", "启用"])
    table_apps.horizontalHeader().setStretchLastSection(True)
    for item in pet.app_watch:
        row = table_apps.rowCount()
        table_apps.insertRow(row)
        table_apps.setItem(row, 0, QTableWidgetItem(str(item.get("app", ""))))
        table_apps.setItem(row, 1, QTableWidgetItem(str(item.get("minutes", 60))))
        check = QTableWidgetItem()
        check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        check.setCheckState(Qt.Checked if item.get("enabled", True) else Qt.Unchecked)
        table_apps.setItem(row, 2, check)
    layout5.addWidget(table_apps)
    row5a = QHBoxLayout()
    btn_add_app = QPushButton("新增应用")
    btn_del_app = QPushButton("删除选中")
    row5a.addWidget(btn_add_app)
    row5a.addWidget(btn_del_app)
    row5a.addStretch()
    layout5.addLayout(row5a)
    chk_launch = QCheckBox("打开受关注的应用时说一句")
    chk_launch.setChecked(pet.app_launch_on)
    layout5.addWidget(chk_launch)
    layout5.addWidget(QLabel("全局快捷键（在哪个程序里按都管用）："))
    list_hotkeys = QListWidget()
    for item in pet.hotkeys:
        text = str(item.get("text", "")).strip()
        label = (f"{item.get('key', '')} → {HOTKEY_KIND_NAMES.get(item.get('kind'), '')}"
                 + (f"「{text}」" if text else "")
                 + ("" if item.get("enabled", True) else "（已关闭）"))
        list_item = QListWidgetItem(label)
        list_item.setData(Qt.UserRole, dict(item))
        list_hotkeys.addItem(list_item)
    layout5.addWidget(list_hotkeys)
    row5b = QHBoxLayout()
    btn_add_hk = QPushButton("新增")
    btn_del_hk = QPushButton("删除选中")
    row5b.addWidget(btn_add_hk)
    row5b.addWidget(btn_del_hk)
    row5b.addStretch()
    layout5.addLayout(row5b)

    def _on_add_app():
        table_apps.insertRow(table_apps.rowCount())
        row = table_apps.rowCount() - 1
        table_apps.setItem(row, 0, QTableWidgetItem("Steam.exe"))
        table_apps.setItem(row, 1, QTableWidgetItem("60"))
        check = QTableWidgetItem()
        check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        check.setCheckState(Qt.Checked)
        table_apps.setItem(row, 2, check)

    def _on_del_app():
        row = table_apps.currentRow()
        if row >= 0:
            table_apps.removeRow(row)

    btn_add_app.clicked.connect(_on_add_app)
    btn_del_app.clicked.connect(_on_del_app)

    def _on_add_hk():
        result = _edit_hotkey_dialog(dlg)
        if result is None:
            return
        label = (f"{result['key']} → {HOTKEY_KIND_NAMES.get(result['kind'], '')}"
                 + (f"「{result['text']}」" if result["text"] else ""))
        list_item = QListWidgetItem(label)
        list_item.setData(Qt.UserRole, result)
        list_hotkeys.addItem(list_item)

    def _on_del_hk():
        row = list_hotkeys.currentRow()
        if row >= 0:
            list_hotkeys.takeItem(row)

    btn_add_hk.clicked.connect(_on_add_hk)
    btn_del_hk.clicked.connect(_on_del_hk)
    tabs.addTab(tab5, "应用与快捷键")

    # ---- 页签 6: 天气 ----
    tab6 = QWidget()
    form6 = QFormLayout(tab6)
    chk_weather = QCheckBox("启用天气（免 Key，联网查）")
    chk_weather.setChecked(pet.weather_on)
    edit_city = QLineEdit(pet.weather_city)
    edit_city.setPlaceholderText("例如: 上海")
    btn_weather_now = QPushButton("立即查看天气")
    btn_weather_now.clicked.connect(lambda: pet._check_weather(city=edit_city.text().strip()))
    form6.addRow("", chk_weather)
    form6.addRow("默认城市", edit_city)
    form6.addRow("", btn_weather_now)
    form6.addRow(QLabel("先查中国天气网，查不到退到 open-meteo；\n"
                        "后台线程查，不会卡住桌宠。"))
    tabs.addTab(tab6, "天气")

    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    layout.addWidget(buttons)

    if dlg.exec_() != QDialog.Accepted:
        return

    # ---- 应用: 页签 1 ----
    dpi_before = store.get_bool("high_dpi")
    pet.water_enabled = chk_water.isChecked()
    pet.water_interval = spin_min.value()
    pet.sleep_minutes = spin_sleep.value()
    pet.deep_sleep_min = spin_deep.value()
    pet.anni_name = edit_name.text().strip()
    pet.anni_date = edit_date.date().toString("yyyy-MM-dd")
    store.set("water_on", pet.water_enabled)
    store.set("water_min", pet.water_interval)
    store.set("sleep_min", pet.sleep_minutes)
    store.set("deep_sleep_min", pet.deep_sleep_min)
    store.set("anni_name", pet.anni_name)
    store.set("anni_date", pet.anni_date)
    store.set("high_dpi", chk_dpi.isChecked())
    store.set("last_anni_shown", "")
    pet._apply_water_timer()
    # ---- 应用: 页签 2 ----
    pet.speech_tier = combo_tier.currentData()
    pet.speech_categories = {key: box.isChecked() for key, box in category_boxes.items()}
    store.set("speech_tier", pet.speech_tier)
    store.set("speech_categories", pet.speech_categories)
    pet._apply_idle_talk_interval()
    # ---- 应用: 页签 3 ----
    pet.reminders = []
    for row in range(list_reminders.count()):
        data = list_reminders.item(row).data(Qt.UserRole)
        try:
            pet.reminders.append(parse_reminder(data))
        except ValueError:
            continue
    store.set("reminders", pet.reminders)
    # ---- 应用: 页签 4 ----
    pet.mode = combo_mode.currentData()
    pet.window_layer = combo_layer.currentData()
    pet.opacity = slider_opacity.value() / 100.0
    pet.locked = chk_locked.isChecked()
    pet.click_through = chk_through.isChecked()
    pet.autostart = chk_autostart.isChecked()
    pet.size_preset = combo_size.currentData()
    store.set("mode", pet.mode)
    store.set("window_layer", pet.window_layer)
    store.set("opacity", pet.opacity)
    store.set("locked", pet.locked)
    store.set("click_through", pet.click_through)
    store.set("autostart", pet.autostart)
    store.set("size_preset", pet.size_preset)
    if pet.size_preset in SIZE_PRESETS:
        pet._apply_size_preset(pet.size_preset)
    pet._apply_layer()
    pet._apply_opacity()
    pet._apply_click_through()
    pet._apply_autostart()
    pet._apply_runtime()
    # ---- 应用: 页签 5 ----
    app_watch = []
    for row in range(table_apps.rowCount()):
        app_item = table_apps.item(row, 0)
        min_item = table_apps.item(row, 1)
        check_item = table_apps.item(row, 2)
        if app_item is None or not app_item.text().strip():
            continue
        try:
            minutes = int((min_item.text() if min_item else "60").strip())
        except ValueError:
            minutes = 60
        app_watch.append({
            "app": app_item.text().strip(),
            "minutes": max(1, minutes),
            "enabled": bool(check_item and check_item.checkState() == Qt.Checked),
        })
    pet.app_watch = app_watch
    pet.app_launch_on = chk_launch.isChecked()
    store.set("app_watch", pet.app_watch)
    store.set("app_launch_on", pet.app_launch_on)
    pet._rebuild_app_tracker()
    # 快捷键: 设置窗口里只做增删, 新增的默认是自定义台词(空台词会被校验拦下)
    new_hotkeys = []
    for row in range(list_hotkeys.count()):
        new_hotkeys.append(list_hotkeys.item(row).data(Qt.UserRole))
    errors = validate_hotkeys(new_hotkeys)
    if errors:
        QMessageBox.warning(dlg, "快捷键有问题", "\n".join(errors) + "\n\n这次先不改快捷键，其他设置已保存。")
    else:
        pet.hotkeys = new_hotkeys
        store.set("hotkeys", pet.hotkeys)
        pet._apply_hotkeys()
    # ---- 应用: 页签 6 ----
    pet.weather_on = chk_weather.isChecked()
    pet.weather_city = edit_city.text().strip()
    store.set("weather_on", pet.weather_on)
    store.set("weather_city", pet.weather_city)

    # 气泡不能连发, 否则后一条立刻盖掉前一条: DPI 提示先说, 2.5 秒后再报“记好了”
    from PyQt5.QtCore import QTimer
    if chk_dpi.isChecked() != dpi_before:
        pet._say("高 DPI 设置下次启动才生效哦～")
        QTimer.singleShot(2500, lambda: pet._say("设置好啦～我记住了"))
    else:
        pet._say("设置好啦～我记住了")
