import logging
import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from . import db
from . import theme
from .app import MainWindow
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
    # 内部标识保持 memo / memo_app：QSettings、数据目录、凭据沿用，不丢设置
    app.setApplicationName("memo")
    app.setOrganizationName("memo_app")
    app.setApplicationDisplayName(theme.APP_NAME)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    theme.apply(theme.saved_name())  # 全局样式：按上次选择的皮肤

    win = MainWindow()
    controller = Controller(win)  # 托盘 / 全局热键 / 快捷记录 / 桌面挂件
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
