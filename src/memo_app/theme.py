"""Shared paper-and-pine palette for the native desktop interface."""
from pathlib import Path

TEXT = "#283D35"
MUTED = "#69766E"
ACCENT = "#2D6550"
PAPER = "#F7F8F4"

_CHECK = (Path(__file__).parent / "assets" / "check.png").as_posix()
_CHEVRON = (Path(__file__).parent / "assets" / "chevron_down.svg").as_posix()

QSS = """
QWidget { color: #283D35; }
QWidget#central { background: #FAFBF8; }
QLabel { background: transparent; border: none; }
QWidget#titleBar { background: #F1F3ED; border-bottom: 1px solid #E3E8DF; }
QLabel#tbTitle { font-size: 12px; color: #526259; }
QLabel#tbSubtitle { font-size: 11px; color: #69766E; }
QToolButton#navBtn, QToolButton#winBtn, QToolButton#closeBtn {
    border: none; background: transparent; border-radius: 5px;
    color: #526259; padding: 4px 10px; font-size: 12px;
}
QToolButton#navBtn:hover, QToolButton#winBtn:hover { background: #E2E8DE; }
QToolButton#navBtn::menu-indicator { image: none; }
QToolButton#closeBtn:hover { background: #F7DFDB; }
QWidget#sidePanel { background: #F1F3ED; border-right: 1px solid #E3E8DF; }
QLabel#wordmark { font-family: Georgia; font-size: 29px; color: #284C3B; }
QLabel#workspaceLabel { font-size: 11px; color: #69766E; }
QLabel#sectionLabel { font-size: 11px; color: #69766E; padding: 0 10px; }
QPushButton#sideNav {
    text-align: left; border: 1px solid transparent; background: transparent;
    border-radius: 8px; padding: 10px 12px; color: #526259; font-size: 12px;
}
QPushButton#sideNav:hover { background: #E5EAE0; }
QPushButton#sideNav:checked { background: #DEE8DB; color: #284F3D; }
QPushButton#sideNav:focus { border: 1px solid #739981; }
QWidget#catRow { border: 1px solid transparent; border-radius: 8px; }
QWidget#catRow:hover { background: #E5EAE0; }
QWidget#catRow[selected="true"] { background: #DEE8DB; }
QWidget#catRow:focus { border: 1px solid #739981; }
QLabel#catName { font-size: 12px; color: #42594B; }
QLabel#catCount { font-size: 11px; color: #69766E; }
QPushButton#catDel {
    border: none; background: transparent; padding: 0; border-radius: 4px;
}
QPushButton#catDel:hover { background: #F7DFDB; }
QPushButton#addCatBtn {
    text-align: left; border: none; border-radius: 7px;
    background: transparent; color: #69766E; padding: 8px 12px; font-size: 12px;
}
QPushButton#addCatBtn:hover { background: #E5EAE0; color: #284F3D; }
QWidget#localCard { background: #E7ECE1; border: 1px solid #DCE3D6; border-radius: 10px; }
QLabel#localTitle { color: #42594B; font-size: 11px; }
QLabel#localHint { color: #62715F; font-size: 11px; }
QWidget#contentPanel {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #FCFCF9, stop:1 #F7F8F4);
}
QLabel#pageTitle { color: #283D35; font-size: 24px; font-weight: 600; }
QLabel#pageTitle[compact="true"] { font-size: 22px; }
QLabel#pageSubtitle { color: #69766E; font-size: 12px; }
QLabel#dateLabel { color: #69766E; font-size: 11px; }
QLineEdit {
    border: 1px solid #DCE2D7; border-radius: 8px;
    padding: 8px 10px; background: #FFFFFF; color: #283D35;
    selection-background-color: #DCE9D9; selection-color: #283D35;
}
QLineEdit:focus { border-color: #648D72; }
QLineEdit#searchInput { background: #F1F3ED; padding: 8px; font-size: 12px; }
QWidget#composer { background: #FFFFFF; border: 1px solid #D8E1D3; border-radius: 12px; }
QWidget#composer[active="true"] { border-color: #648D72; }
QLineEdit#mainInput {
    border: none; background: transparent; padding: 8px 0; font-size: 15px;
}
QLabel#composerHint { color: #69766E; font-size: 11px; }
QPushButton {
    border: 1px solid #DCE2D7; border-radius: 7px;
    padding: 7px 15px; background: #FFFFFF; color: #42594B;
}
QPushButton:hover { background: #EEF2E9; border-color: #B7C9B4; }
QPushButton:focus { border-color: #648D72; }
QPushButton:disabled { color: #7C877E; background: #EEF1EA; border-color: #E1E6DC; }
QPushButton:default, QPushButton#saveBtn, QPushButton#recordBtn:checked {
    background: #2D6550; color: #FFFFFF; border-color: #2D6550;
}
QPushButton:default:hover, QPushButton#saveBtn:hover { background: #244F40; }
QPushButton#saveBtn:disabled { background: #E5ECE1; color: #687B68; border-color: #E5ECE1; }
QWidget#filterGroup { background: #EBEEE6; border-radius: 8px; }
QPushButton#filterBtn {
    border: 1px solid transparent; padding: 6px 14px;
    border-radius: 6px; background: transparent; color: #69766E; font-size: 11px;
}
QPushButton#filterBtn:hover { color: #284F3D; }
QPushButton#filterBtn:checked { background: #FFFFFF; color: #2D6550; border-color: #DCE2D7; }
QPushButton#filterBtn:focus { border-color: #648D72; }
QLabel#listHeading { font-size: 11px; color: #69766E; }
QWidget#memoItem { background: #FFFFFF; border: 1px solid #E1E6DC; border-radius: 10px; }
QWidget#memoItem:hover { border-color: #B7C9B4; }
QWidget#memoItem[selected="true"] { background: #F4F8F0; border-color: #739981; }
QWidget#memoItem[prioHigh="true"] { border-left: 3px solid #B87959; }
QWidget#memoItem[completed="true"] { background: #F2F4EE; }
QLabel#memoContent { font-size: 14px; color: #283D35; }
QLabel#memoContent[completed="true"] { color: #69766E; }
QLabel#memoTime { font-size: 11px; color: #69766E; }
QLabel#memoPin { font-size: 11px; color: #8D633E; }
QLabel#memoTags { font-size: 11px; color: #4F765F; }
QLabel#memoBadges { font-size: 11px; }
QPushButton#cardAction { border: 1px solid transparent; background: transparent; padding: 0; border-radius: 6px; }
QPushButton#cardAction:hover { background: #E9EFE3; }
QPushButton#cardAction:focus { border: 1px solid #648D72; }
QPushButton#deleteAction { border: 1px solid transparent; background: transparent; padding: 0; border-radius: 6px; }
QPushButton#deleteAction:hover { background: #F7E8E2; }
QPushButton#deleteAction:focus { border: 1px solid #B87959; }
QPushButton#doneBtn {
    border: 1.5px solid #C0CDBA; border-radius: 10px;
    padding: 0; background: transparent;
}
QPushButton#doneBtn:hover, QPushButton#doneBtn:focus { border-color: #2D6550; background: #EDF3E7; }
QPushButton#doneBtn[done="true"] { background: #2D6550; border-color: #2D6550; }
QLineEdit#inlineEdit { background: #F4F8F0; padding: 4px 8px; }
QListWidget, QScrollArea { background: transparent; border: none; outline: none; }
QListWidget::item { border: none; }
QListWidget::item:selected { background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px 0; }
QScrollBar::handle:vertical { background: #CBD5C4; border-radius: 4px; min-height: 32px; }
QScrollBar::handle:vertical:hover { background: #A6B69D; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QLabel#emptyTitle { color: #42594B; font-size: 19px; font-weight: 600; }
QLabel#emptyLabel { color: #69766E; font-size: 12px; }
QLabel#statusLabel { color: #69766E; font-size: 11px; }
QWidget#trashBar { background: #F5EDE2; border: 1px solid #E9DCC7; border-radius: 8px; }
QLabel#trashTitle { color: #7D674B; font-size: 11px; }
QPushButton#trashBack { border: none; background: transparent; padding: 4px 8px; color: #7D674B; }
QPushButton#trashPurge { border-color: #E4CABD; color: #9B523D; padding: 5px 12px; }
QPushButton#trashPurge:hover { background: #F7E4DB; }
QMenu { background: #FCFCF9; border: 1px solid #DCE2D7; border-radius: 8px; padding: 5px; }
QMenu::item { padding: 8px 24px 8px 14px; border-radius: 5px; color: #42594B; }
QMenu::item:selected { background: #E5ECE0; }
QMenu::separator { height: 1px; background: #E3E8DF; margin: 4px 8px; }
QDialog { background: #FAFBF8; }
QDialog QLabel { color: #42594B; }
QLabel#dialogTitle { font-size: 20px; font-weight: 600; color: #283D35; }
QLabel#dialogHint { font-size: 11px; color: #69766E; }
QComboBox, QDateTimeEdit {
    border: 1px solid #DCE2D7; border-radius: 7px;
    padding: 7px 34px 7px 10px; background: #FFFFFF; color: #42594B;
}
QComboBox::drop-down, QDateTimeEdit::drop-down {
    subcontrol-origin: padding; subcontrol-position: top right;
    width: 28px; border: none; border-left: 1px solid #E1E6DC;
    border-top-right-radius: 7px; border-bottom-right-radius: 7px;
    background: #F2F5ED;
}
QComboBox::drop-down:hover, QDateTimeEdit::drop-down:hover { background: #E5ECE0; }
QComboBox::down-arrow, QDateTimeEdit::down-arrow {
    image: url(@CHEVRON@); width: 12px; height: 12px;
}
QComboBox:focus, QDateTimeEdit:focus { border-color: #648D72; }
QComboBox:disabled, QDateTimeEdit:disabled { color: #798476; background: #EEF1EA; }
QComboBox QAbstractItemView { background: #FFFFFF; selection-background-color: #DEE8DB; selection-color: #283D35; }
QCheckBox { color: #42594B; spacing: 8px; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #BBCBB4; border-radius: 4px; background: #FFFFFF; }
QCheckBox::indicator:hover, QCheckBox:focus::indicator { border-color: #2D6550; }
QCheckBox::indicator:checked { background: #2D6550; border-color: #2D6550; image: url(@CHECK@); }
QToolTip { background: #284C3B; color: #FFFFFF; border: none; padding: 6px; }
""".replace("@CHECK@", _CHECK).replace("@CHEVRON@", _CHEVRON)
