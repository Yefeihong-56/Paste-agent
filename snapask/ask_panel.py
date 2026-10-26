"""问 AI 面板：模型选择（方案 B：名称+一行说明）+ 流式答案 + 多轮追问。

- 顶部模型下拉：打开即见每个模型的定位说明；首轮携带截图时仅 VL 系列可选；
- 答案区流式渲染，可复制、可停止；
- 多轮追问：保留最近 4 轮上下文；新截图由主程序开新会话；
- Enter 发送 / Shift+Enter 换行 / Esc 收起；网络请求在子线程执行。
"""
import os
import subprocess
import threading

from PySide6.QtCore import QObject, QPoint, QRect, Qt, QThread, Signal
from PySide6.QtGui import QGuiApplication, QPixmap, QTextCursor
from PySide6.QtWidgets import (QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton,
                               QTextEdit, QVBoxLayout, QWidget)

import config
import qa_agent

_LABEL_STYLE = "font-size:13px;font-weight:500;"
_HINT_STYLE = "font-size:11px;color:#888780;"
_DESC_STYLE = "font-size:11px;color:#888780;"
_DESC_DISABLED_STYLE = "font-size:11px;color:#B4B2A9;"
_NAME_DISABLED_STYLE = "color:#B4B2A9;"


class _InputBox(QTextEdit):
    """输入框：Enter 发送、Shift+Enter 换行、Esc 收起。"""

    def __init__(self, panel):
        super().__init__()
        self._panel = panel

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self._panel.send_question()
            return
        if event.key() == Qt.Key.Key_Escape:
            self._panel.close()
            return
        super().keyPressEvent(event)


class _ModelItem(QWidget):
    """下拉面板里的两行模型项（名称 + 一行灰色说明）。"""

    def __init__(self, model_id, desc, enabled=True, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(0)
        name = QLabel(model_id)
        name.setStyleSheet(_LABEL_STYLE if enabled else _NAME_DISABLED_STYLE)
        desc_label = QLabel(desc)
        desc_label.setStyleSheet(
            _DESC_STYLE if enabled else _DESC_DISABLED_STYLE)
        lay.addWidget(name)
        lay.addWidget(desc_label)


class ModelPicker(QWidget):
    """模型选择器：按钮 + 自绘弹窗（打开即见说明，不依赖悬停）。"""

    model_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        cfg = config.load()
        self._current = cfg.get("model", qa_agent.DEFAULT_MODEL)
        # 自愈：历史配置里的模型已不在注册表（如端点更换），回退到默认模型
        if self._current not in [m["id"] for m in self._all_models()]:
            self._current = qa_agent.DEFAULT_MODEL
        self._vision_required = True  # 首轮携带截图
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.btn = QPushButton(self._current)
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.clicked.connect(self._toggle_popup)
        lay.addWidget(self.btn)

        self.popup = QListWidget()
        self.popup.setWindowFlags(Qt.WindowType.Popup)
        self.popup.itemClicked.connect(self._on_item_clicked)
        self._rebuild()

    def current(self) -> str:
        return self._current

    def set_vision_required(self, required: bool):
        self._vision_required = required
        self._rebuild()

    def _all_models(self) -> list:
        custom = config.load().get("custom_models", [])
        builtin = [dict(m) for m in qa_agent.MODELS]
        builtin.append({"id": qa_agent.CUSTOM, "desc": "输入任意千问模型 ID",
                        "vision": True})
        for mid in custom:
            if mid not in [m["id"] for m in builtin]:
                builtin.append({"id": mid, "desc": "自定义模型", "vision": True})
        return builtin

    def _rebuild(self):
        self.popup.clear()
        for m in self._all_models():
            enabled = (not self._vision_required) or m["vision"]
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, m["id"])
            if not enabled:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.popup.addItem(item)
            desc = m["desc"] if enabled else m["desc"] + "（追问轮可用）"
            widget = _ModelItem(m["id"], desc, enabled)
            item.setSizeHint(widget.sizeHint())
            self.popup.setItemWidget(item, widget)
        self.popup.setFixedWidth(max(220, self.btn.width() + 40))

    def _toggle_popup(self):
        if self.popup.isVisible():
            self.popup.hide()
            return
        pos = self.btn.mapToGlobal(QPoint(0, self.btn.height()))
        self.popup.move(pos)
        self.popup.show()

    def _on_item_clicked(self, item):
        if not (item.flags() & Qt.ItemFlag.ItemIsEnabled):
            return
        model_id = item.data(Qt.ItemDataRole.UserRole)
        self.popup.hide()
        if model_id == qa_agent.CUSTOM:
            text, ok = QInputDialog.getText(
                self, "自定义模型", "输入千问模型 ID（如 qwen-vl-max）：",
                QLineEdit.EchoMode.Normal, "")
            if not ok or not text.strip():
                return
            model_id = text.strip()
            cfg = config.load()
            custom = cfg.get("custom_models", [])
            if model_id not in custom and \
                    model_id not in [m["id"] for m in qa_agent.MODELS]:
                custom.append(model_id)
                cfg["custom_models"] = custom
                config.save(cfg)
            self._rebuild()
        self._set_current(model_id)

    def _set_current(self, model_id: str):
        self._current = model_id
        self.btn.setText(model_id)
        cfg = config.load()
        cfg["model"] = model_id
        config.save(cfg)
        self.model_changed.emit(model_id)


class _ChatWorker(QObject):
    """子线程执行流式请求，信号回传主线程。"""

    delta = Signal(str)
    finished = Signal(bool)
    failed = Signal(str)

    def __init__(self, model: str, messages: list, cancel_event):
        super().__init__()
        self._model = model
        self._messages = messages
        self._cancel = cancel_event

    def run(self):
        try:
            qa_agent.stream_chat(
                self._model, self._messages,
                self.delta.emit,
                lambda cancelled: self.finished.emit(cancelled),
                self.failed.emit,
                should_stop=self._cancel.is_set,
            )
        except Exception as e:  # noqa: BLE001
            self.failed.emit(qa_agent.classify_error(str(e)))


class AskPanel(QWidget):
    """问 AI 浮窗。"""

    closed = Signal()

    WIDTH = 400

    def __init__(self, shot, rect, screen_geo, parent=None):
        super().__init__(parent)
        self.shot = shot
        self.rect = QRect(rect)
        self._screen_geo = screen_geo
        self.history = []               # [{"role","content"}]，图片轮 content 为 list
        self._streaming = False
        self._cancel = threading.Event()
        self._thread = None
        self._worker = None
        self._answer_text = ""
        self._build_ui()
        self._position()
        self._refresh_model_enabled()

    # ---- UI ----
    def _build_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setFixedWidth(self.WIDTH)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("截图问答")
        title.setStyleSheet(_LABEL_STYLE)
        self.picker = ModelPicker()
        self.picker.model_changed.connect(self._refresh_model_enabled)
        btn_close = QPushButton("×")
        btn_close.setFixedWidth(28)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.clicked.connect(self.close)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.picker)
        header.addWidget(btn_close)

        info = QHBoxLayout()
        self.thumb = QLabel()
        self.thumb.setFixedSize(56, 40)
        self.thumb.setScaledContents(False)
        info_text = QVBoxLayout()
        info_text.setSpacing(0)
        ready = QLabel("当前截图已就绪")
        ready.setStyleSheet(_LABEL_STYLE)
        note = QLabel("新截图将自动开启新会话")
        note.setStyleSheet(_HINT_STYLE)
        info_text.addWidget(ready)
        info_text.addWidget(note)
        info.addWidget(self.thumb)
        info.addLayout(info_text)
        info.addStretch(1)

        ans_row = QHBoxLayout()
        self.btn_copy = QPushButton("复制答案")
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.clicked.connect(self._copy_answer)
        self.btn_locate = QPushButton("定位 .env 文件")
        self.btn_locate.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_locate.clicked.connect(self._locate_env)
        self.btn_locate.hide()
        self.status = QLabel("")
        self.status.setStyleSheet(_HINT_STYLE)
        ans_row.addWidget(self.btn_copy)
        ans_row.addWidget(self.btn_locate)
        ans_row.addStretch(1)
        ans_row.addWidget(self.status)

        self.answer = QTextEdit()
        self.answer.setReadOnly(True)
        self.answer.setMinimumHeight(150)

        self.input = _InputBox(self)
        self.input.setFixedHeight(56)
        self.input.setPlaceholderText("针对这张截图提问，Enter 发送")
        self.btn_send = QPushButton("发送")
        self.btn_send.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_send.clicked.connect(self.send_question)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop.clicked.connect(self._stop)
        self.btn_stop.hide()
        buttons = QVBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(4)
        buttons.addWidget(self.btn_send)
        buttons.addWidget(self.btn_stop)
        input_row = QHBoxLayout()
        input_row.addWidget(self.input)
        input_row.addLayout(buttons)

        hint = QLabel("Enter 发送 · Shift+Enter 换行 · Esc 收起")
        hint.setStyleSheet(_HINT_STYLE)

        root.addLayout(header)
        root.addLayout(info)
        root.addLayout(ans_row)
        root.addWidget(self.answer)
        root.addLayout(input_row)
        root.addWidget(hint)

        pix = self.shot.crop_logical_rect(self.rect)
        self.thumb.setPixmap(pix.scaled(
            56, 40, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

    def _position(self):
        """定位到选区下边缘 +8px，越界则上移，并限制在屏幕内。"""
        h = self.sizeHint().height()
        x = self.rect.left()
        y = self.rect.bottom() + 8
        if x + self.WIDTH > self._screen_geo.right():
            x = self._screen_geo.right() - self.WIDTH - 4
        if y + h > self._screen_geo.bottom():
            y = max(4, self.rect.top() - h - 8)
        self.move(x, y)

    # ---- 模型 ----
    def _refresh_model_enabled(self):
        self.picker.set_vision_required(not self.history)

    # ---- 问答 ----
    def send_question(self):
        if self._streaming:
            return
        question = self.input.toPlainText().strip()
        if not question:
            return
        vision = qa_agent.model_vision(self.picker.current())
        image_url = None
        if not self.history:
            image_url = qa_agent.image_to_data_url(
                self.shot.crop_pil_logical(self.rect))
        messages = qa_agent.build_messages(question, self.history, vision,
                                           image_url)
        self._last_user_content = messages[-1]["content"]
        self.input.clear()
        self.answer.clear()
        self._answer_text = ""
        self.status.setText("思考中…")
        self.btn_locate.hide()
        self._set_streaming(True)
        self._cancel.clear()

        self._thread = QThread(self)
        self._worker = _ChatWorker(self.picker.current(), messages, self._cancel)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.delta.connect(self._on_delta)
        self._worker.finished.connect(self._on_worker_done)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.start()

    def _set_streaming(self, on: bool):
        self._streaming = on
        self.btn_send.setVisible(not on)
        self.btn_stop.setVisible(on)
        self.input.setReadOnly(on)

    def _stop(self):
        self._cancel.set()

    def _on_delta(self, text: str):
        self._answer_text += text
        self.status.setText("回答中…")
        cursor = self.answer.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.answer.setTextCursor(cursor)
        self.answer.ensureCursorVisible()

    def _on_worker_done(self, cancelled: bool):
        # 用户消息按“实际发出的内容”入历史（首轮含图片部件），
        # 这样追问轮模型仍能看到截图（修复多轮“失明”缺陷）。
        self.history.append({"role": "user",
                             "content": self._last_user_content})
        self.history.append({"role": "assistant", "content": self._answer_text})
        self.status.setText("已停止" if cancelled else "完成")
        self._set_streaming(False)
        self._refresh_model_enabled()
        self._teardown_thread()

    def _on_worker_failed(self, msg: str):
        if msg == qa_agent.NO_KEY_MSG:
            self.answer.setPlainText(
                "尚未配置千问 API Key。\n\n"
                "请在项目根目录的 .env 文件中填入：\n"
                "DASHSCOPE_API_KEY=你的Key\n\n"
                "（.env 已被 .gitignore 排除，不会提交到 git）")
            self.btn_locate.show()
        else:
            self.answer.setPlainText(msg)
        self.status.setText("出错了")
        self._set_streaming(False)
        self._teardown_thread()

    def _teardown_thread(self):
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
            self._thread = None
            self._worker = None

    # ---- 其他 ----
    def _copy_answer(self):
        QGuiApplication.clipboard().setText(self._answer_text)
        self.status.setText("答案已复制")

    def _locate_env(self):
        path = qa_agent.ensure_env_file()
        if path and os.path.exists(path):
            subprocess.run(["explorer", "/select," + path])

    def closeEvent(self, event):
        self._cancel.set()
        self._teardown_thread()
        self.closed.emit()
        super().closeEvent(event)
