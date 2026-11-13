import logging
import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from . import db
from .app import QSS, MainWindow
from .controller import Controller


def _setup_logging() -> None:
    log_dir = db.data_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_dir / "app.log", encoding="utf-8")],
        force=True,
    )

    def _hook(exc_type, exc, tb):
        logging.getLogger("memo_app").critical(
            "未捕获异常", exc_info=(exc_type, exc, tb)
        )

    sys.excepthook = _hook


def main() -> None:
    _setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("memo")
    app.setOrganizationName("memo_app")
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(QSS)  # 全局样式：对话框/菜单/控件统一美化

    win = MainWindow()
    controller = Controller(win)  # 托盘 / 全局热键 / 快捷记录 / 桌面挂件
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
