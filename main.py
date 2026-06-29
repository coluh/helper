import os
import sys
from typing import cast

import requests
from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtDBus import QDBusAbstractAdaptor, QDBusConnection
from PySide6.QtGui import QAction, QIcon, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
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


class TranslateWidget(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)

        self.src = QPlainTextEdit()
        self.src.setPlaceholderText("待翻译文本...")
        layout.addWidget(self.src)

        self.dst = QPlainTextEdit()
        self.dst.setPlaceholderText("翻译结果...")
        self.dst.setReadOnly(True)
        layout.addWidget(self.dst)

    def set_translation(self, src: str | None = None, dst: str | None = None):
        if src:
            self.src.setPlainText(src)
        if dst:
            self.dst.setPlainText(dst)


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Helper")
        self.setWindowFlags(
            # Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.resize(400, 700)

        central = QWidget()
        self.setCentralWidget(central)
        self.root_layout = QVBoxLayout(central)
        self.root_layout.setContentsMargins(0, 0, 0, 0)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.addItems(["翻译", "设置"])
        self.nav.setFlow(QListView.Flow.LeftToRight)

        self.stack = QStackedWidget()
        self.translate_widget = TranslateWidget(self)
        self.stack.addWidget(self.translate_widget)
        self.settings_widget = QLabel("settings page")
        self.stack.addWidget(self.settings_widget)

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)

        self._apply_layout(self.size())

        self.nav.setCurrentRow(0)

    def show_page(self, page="translate"):
        print(f"show page {page}")
        self.nav.setCurrentRow(0)
        self.show()

    def resizeEvent(self, event: QResizeEvent, /):
        super().resizeEvent(event)
        old_size = event.oldSize()
        new_size = event.size()
        if (old_size.width() > old_size.height()) != (new_size.width() > new_size.height()):
            self._apply_layout(new_size)

    def _apply_layout(self, size: QSize):
        width, height = size.width(), size.height()

        old_layout = self.root_layout
        while old_layout.count():
            old_layout.takeAt(0)

        if width < height:
            self.nav.setFlow(QListView.Flow.LeftToRight)
            self.nav.setFixedWidth(16777215)
            self.nav.setFixedHeight(50)
            self.root_layout.addWidget(self.nav)
            self.root_layout.addWidget(self.stack)
        else:
            self.nav.setFlow(QListView.Flow.TopToBottom)
            self.nav.setFixedHeight(16777215)
            self.nav.setFixedWidth(100)
            for i in range(self.nav.count()):
                item = self.nav.item(i)
                if item:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            splitter = QSplitter()
            splitter.addWidget(self.nav)
            splitter.addWidget(self.stack)
            self.root_layout.addWidget(splitter)


class WindowAdapter(QDBusAbstractAdaptor):
    def __init__(self, parent):
        super().__init__(parent)

    @Slot(str)
    def Activate(self, page):
        parent = cast("MyApp", self.parent())
        parent.handle_activate(page)

    @Slot(str)
    def Translate(self, text):
        parent = cast("MyApp", self.parent())
        if text and text.strip():
            parent.handle_translation(text.strip())


class MyApp(QApplication):

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

        self.window = MainWindow()

        if not self.register_dbus():
            QMessageBox.critical(None, "错误", "翻译服务已运行")
            sys.exit(1)

        self.translation_requested.connect(self.window.translate_widget.set_translation)

    def register_dbus(self):
        bus = QDBusConnection.sessionBus()
        if not bus.registerService("com.destywen.helper"):
            print(f"注册服务失败: {bus.lastError().message()}")
            return False

        adapter = WindowAdapter(self)
        if not bus.registerObject(
            "/",
            "com.destywen.helper.m",
            adapter,
            QDBusConnection.RegisterOption.ExportAllSlots,
        ):
            print(f"注册对象失败: {bus.lastError().message()}")
            return False

        print("DBus注册成功，运行中...")
        return True

    def handle_activate(self, page):
        self.window.show_page(page)

    def handle_translation(self, text):
        self.window.show_page()
        self.window.translate_widget.set_translation(dst="...")
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
            self.window.translate_widget.set_translation(dst=f"错误: {result['error']}")
        else:
            self.translation_requested.emit(result["src"], result["dst"])


if __name__ == "__main__":
    app = MyApp(sys.argv)

    with open("./style.qss", "r", encoding="utf-8") as f:
        app.setStyleSheet(f.read())

    sys.exit(app.exec())
