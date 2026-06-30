import json
import os
import sys
from typing import Literal, cast

from PySide6.QtCore import QByteArray, QSize, Qt, QTimer, QUrl, Slot
from PySide6.QtDBus import QDBusAbstractAdaptor, QDBusConnection
from PySide6.QtGui import QAction, QIcon, QResizeEvent
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QListView,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY")


class TranslateWidget(QWidget):

    def __init__(self, manager: QNetworkAccessManager, parent=None):
        super().__init__(parent)
        self.manager = manager

        layout = QVBoxLayout(self)

        self.src_edit = QPlainTextEdit()
        self.src_edit.setPlaceholderText("待翻译文本...")
        self.src_edit.textChanged.connect(self.on_text_changed)
        layout.addWidget(self.src_edit)

        self.dst_text = QPlainTextEdit()
        self.dst_text.setPlaceholderText("翻译结果...")
        self.dst_text.setReadOnly(True)
        layout.addWidget(self.dst_text)

        self.debounce_timer = QTimer()
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.timeout.connect(self.start_translation)
        self.debounce_delay = 400

        self._wait_resp = False
        self.current_reply = None

    def on_text_changed(self):
        self.debounce_timer.stop()
        self.debounce_timer.start(self.debounce_delay)

    def start_translation(self):
        if self._wait_resp:
            return
        text = self.src_edit.toPlainText().strip()
        if not text:
            self.dst_text.clear()
            return

        self.set_translation(dst="...")
        self.call_translation(text)

    def call_translation(self, text, source_lang="auto", target_lang="Chinese"):
        if self.current_reply and self.current_reply.isRunning():
            self.current_reply.abort()
            self.current_reply = None

        url = QUrl("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
        request = QNetworkRequest(url)
        request.setRawHeader(b"Authorization", f"Bearer {DASHSCOPE_API_KEY}".encode())
        request.setRawHeader(b"Content-Type", b"application/json")
        payload = {
            "model": "qwen-mt-flash",
            "messages": [{"role": "user", "content": text}],
            "translation_options": {"source_lang": source_lang, "target_lang": target_lang},
        }
        body = QByteArray(json.dumps(payload).encode())

        reply = self.manager.post(request, body)
        reply.setProperty("src_text", text)
        self.current_reply = reply

        def on_finished():
            self._wait_resp = False
            self.current_reply = None
            if reply.error() == QNetworkReply.NetworkError.OperationCanceledError:
                pass  # cancelled
            elif reply.error() != QNetworkReply.NetworkError.NoError:
                try:
                    data = json.loads(reply.readAll().toStdString())
                    self.on_translation_done({"error": data})
                except Exception:
                    self.on_translation_done({"error": reply.errorString()})
            else:
                try:
                    data = json.loads(reply.readAll().toStdString())
                    if "choices" in data and len(data["choices"]) > 0:
                        content = data["choices"][0]["message"]["content"]
                        self.on_translation_done({"dst": content})
                    else:
                        msg = data.get("error", {}).get("message", data)
                        self.on_translation_done({"error": msg})
                except Exception as e:
                    self.on_translation_done({"error": str(e)})
            reply.deleteLater()

        reply.finished.connect(on_finished)

    def on_translation_done(self, result):
        if "error" in result:
            self.dst_text.setPlainText(f"Error: {result['error']}")
        else:
            self.dst_text.setPlainText(result["dst"])

    def set_translation(self, src: str | None = None, dst: str | None = None):
        if src:
            self.src_edit.setPlainText(src)
        if dst:
            self.dst_text.setPlainText(dst)


class MainWindow(QMainWindow):

    def __init__(self, network_manager):
        super().__init__()
        self.network_manager = network_manager
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
        for i in range(self.nav.count()):
            item = self.nav.item(i)
            if item:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        self.stack = QStackedWidget()
        self.translate_widget = TranslateWidget(self.network_manager, self)
        self.stack.addWidget(self.translate_widget)
        self.settings_widget = QLabel("settings page")
        self.stack.addWidget(self.settings_widget)

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)

        self.splitter = QSplitter()
        self.splitter.addWidget(self.nav)
        self.splitter.addWidget(self.stack)

        self.root_layout.addWidget(self.splitter)
        self._apply_layout(self.size())

        self.nav.setCurrentRow(0)

    def show_page(self, page: Literal["translation", "settings"] = "translation"):
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

        if width < height:
            self.splitter.setOrientation(Qt.Orientation.Vertical)
            self.nav.setFlow(QListView.Flow.LeftToRight)
            self.nav.setFixedWidth(16777215)
            self.nav.setFixedHeight(50)
        else:
            self.splitter.setOrientation(Qt.Orientation.Horizontal)
            self.nav.setFlow(QListView.Flow.TopToBottom)
            self.nav.setFixedHeight(16777215)
            self.nav.setFixedWidth(100)


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

    @Slot()
    def Quit(self):
        parent = cast("MyApp", self.parent())
        parent.quit()


class MyApp(QApplication):

    def __init__(self, argv):
        super().__init__(argv)
        self.network_manager = QNetworkAccessManager()
        self.setQuitOnLastWindowClosed(False)

        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(QIcon.fromTheme("preferences-desktop-locale"))
        tray_menu = QMenu()
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit)
        tray_menu.addAction(quit_action)
        self.tray.setContextMenu(tray_menu)
        self.tray.show()

        self.window = MainWindow(self.network_manager)

        if not self.register_dbus():
            QMessageBox.critical(None, "错误", "翻译服务已运行")
            sys.exit(1)

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
        self.window.translate_widget._wait_resp = True
        self.window.translate_widget.set_translation(text, "...")
        self.window.translate_widget.call_translation(text)


if __name__ == "__main__":
    app = MyApp(sys.argv)

    with open("./style.qss", "r", encoding="utf-8") as f:
        app.setStyleSheet(f.read())

    sys.exit(app.exec())
