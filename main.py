import os
import sys
from typing import cast

import requests
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtDBus import QDBusAbstractAdaptor, QDBusConnection
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY")


class TranslateWorker(QObject):
    finished = Signal(dict)

    def __init__(self, text, source_lang="auto", target_lang="Chinese") -> None:
        super().__init__()
        self.text = text
        self.source_lang = source_lang
        self.target_lang = target_lang

    def run(self):
        try:
            result = translate_text(self.text, self.source_lang, self.target_lang)
            self.finished.emit(result)
        except Exception as e:
            self.finished.emit({"error": str(e)})


def translate_text(text, source_lang, target_lang):
    url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    headers = {"Authorization": f"Bearer {DASHSCOPE_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "qwen-mt-flash",
        "messages": [{"role": "user", "content": text}],
        "translation_options": {"source_lang": source_lang, "target_lang": target_lang},
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if "choices" in data and len(data["choices"]) > 0:
            translated = data["choices"][0]["message"]["content"]
            return {"src": text, "dst": translated}
        else:
            error_msg = data.get("error", {}).get("message", "未知 API 错误")
            return {"error": f"API 返回异常: {error_msg}"}
    except requests.exceptions.RequestException as e:
        return {"error": f"网络请求失败: {str(e)}"}


class TranslateWindow(QWidget):
    """翻译结果弹出窗口（半透明、自动关闭）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        # self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(450, 200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        # 原文
        self.src_label = QLabel("原文")
        self.src_label.setStyleSheet("color: #333; font-size: 14px;")
        self.src_label.setWordWrap(True)
        layout.addWidget(self.src_label)

        # 分隔线
        line = QLabel()
        line.setStyleSheet("border: 1px solid #ccc;")
        layout.addWidget(line)

        # 译文
        self.dst_label = QLabel("译文")
        self.dst_label.setStyleSheet("color: #1a73e8; font-size: 16px; font-weight: bold;")
        self.dst_label.setWordWrap(True)
        layout.addWidget(self.dst_label)

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.close_btn = QPushButton("关闭 (5s)")
        self.close_btn.setFixedWidth(100)
        self.close_btn.clicked.connect(self.close)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        # 背景样式
        self.setStyleSheet(
            """
            QWidget#window {
                background: rgba(255, 255, 255, 240);
                border-radius: 10px;
                border: 1px solid #aaa;
            }
        """
        )
        self.setObjectName("window")

        # 自动关闭定时器
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.close)
        self.timer.setSingleShot(True)
        self.auto_close_seconds = 5

        # 居中
        self.center()

    def center(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            int((screen.width() - self.width()) / 2), int((screen.height() - self.height()) / 2)
        )

    def show_translation(self, src, dst):
        self.src_label.setText(f"📖 {src}")
        self.dst_label.setText(f"📝 {dst}")
        self.close_btn.setText(f"关闭 ({self.auto_close_seconds}s)")
        self.timer.start(self.auto_close_seconds * 1000)
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        self.timer.stop()
        event.accept()


class TranslateAdapter(QDBusAbstractAdaptor):
    def __init__(self, parent):
        super().__init__(parent)

    @Slot(str)
    def Translate(self, text):
        parent = cast("TranslatorApp", self.parent())
        if text and text.strip():
            parent.handle_translation_request(text.strip())


class TranslatorApp(QApplication):

    translation_requested = Signal(str, str)

    def __init__(self, argv):
        super().__init__(argv)
        self.setQuitOnLastWindowClosed(False)

        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(QIcon.fromTheme("preferences-desktop-locale"))
        tray_menu = QMenu()
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit)
        tray_menu.addAction(quit_action)
        self.tray.setContextMenu(tray_menu)
        self.tray.show()

        self.window = TranslateWindow()

        if not self.register_dbus():
            QMessageBox.critical(None, "错误", "翻译服务已运行")
            sys.exit(1)

        self.translation_requested.connect(self.window.show_translation)

    def register_dbus(self):
        bus = QDBusConnection.sessionBus()
        if not bus.registerService("com.destywen.translator"):
            print(f"注册服务失败: {bus.lastError().message()}")
            return False

        adapter = TranslateAdapter(self)
        if not bus.registerObject(
            "/",
            "com.destywen.translator.Translate",
            adapter,
            QDBusConnection.RegisterOption.ExportAllSlots,
        ):
            print(f"注册对象失败: {bus.lastError().message()}")
            return False

        return True

    def handle_translation_request(self, text):
        self.worker_thread = QThread()
        self.worker = TranslateWorker(text)
        self.worker.moveToThread(self.worker_thread)

        self.worker_thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.on_translation_done)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)

        self.worker_thread.start()

    def on_translation_done(self, result):
        if "error" in result:
            self.tray.showMessage(
                "翻译失败", result["error"], QSystemTrayIcon.MessageIcon.Warning, 3000
            )
            self.window.show_translation("错误", result["error"])
        else:
            self.translation_requested.emit(result["src"], result["dst"])


if __name__ == "__main__":
    app = TranslatorApp(sys.argv)
    sys.exit(app.exec())
