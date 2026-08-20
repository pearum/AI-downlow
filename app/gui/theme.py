"""Dark / light stylesheets for the application."""

from __future__ import annotations

_DARK_QSS = """
QMainWindow, QWidget#PageRoot {
    background-color: #1e1f24;
    color: #e6e6e6;
}
QWidget {
    color: #e6e6e6;
    font-size: 13px;
}
QWidget#Sidebar {
    background-color: #17181c;
    border-right: 1px solid #2c2d33;
}
QPushButton {
    background-color: #2c2d33;
    border: 1px solid #3a3b41;
    border-radius: 6px;
    padding: 6px 14px;
    color: #e6e6e6;
}
QPushButton:hover { background-color: #36373e; }
QPushButton:pressed { background-color: #23242a; }
QPushButton:disabled { color: #6a6b72; background-color: #24252a; }
QPushButton#PrimaryButton {
    background-color: #2f6fed;
    border-color: #2f6fed;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#PrimaryButton:hover { background-color: #3d7bf0; }
QPushButton#DangerButton { background-color: #c0392b; border-color: #c0392b; color: white; }
QPushButton#DangerButton:hover { background-color: #d1483a; }
QPushButton#NavButton {
    text-align: left;
    padding: 10px 14px;
    border: none;
    border-radius: 0;
    background-color: transparent;
    color: #b8b9bd;
    font-size: 14px;
}
QPushButton#NavButton:hover { background-color: #23242a; color: #ffffff; }
QPushButton#NavButton:checked { background-color: #2f6fed; color: #ffffff; }
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #26272d;
    border: 1px solid #3a3b41;
    border-radius: 6px;
    padding: 6px 10px;
    selection-background-color: #2f6fed;
}
QLineEdit:focus, QComboBox:focus { border-color: #2f6fed; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background-color: #26272d;
    border: 1px solid #3a3b41;
    selection-background-color: #2f6fed;
}
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QListWidget, QTableWidget, QTableView {
    background-color: #1e1f24;
    alternate-background-color: #23242a;
    border: 1px solid #2c2d33;
    border-radius: 6px;
    gridline-color: #2c2d33;
}
QHeaderView::section {
    background-color: #23242a;
    border: none;
    border-bottom: 1px solid #3a3b41;
    padding: 8px;
    font-weight: 600;
}
QTableWidget::item:selected, QListWidget::item:selected {
    background-color: #2f6fed;
    color: white;
}
QListWidget::item { padding: 6px; }
QTabWidget::pane { border: 1px solid #2c2d33; }
QTabBar::tab {
    background: #26272d;
    padding: 8px 16px;
    border: 1px solid #2c2d33;
    border-bottom: none;
}
QTabBar::tab:selected { background: #1e1f24; color: #ffffff; }
QGroupBox {
    border: 1px solid #2c2d33;
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #9ba0aa;
}
QProgressBar {
    border: 1px solid #2c2d33;
    border-radius: 6px;
    background-color: #26272d;
    text-align: center;
    color: #ffffff;
    height: 18px;
}
QProgressBar::chunk {
    background-color: #2f6fed;
    border-radius: 5px;
}
QScrollBar:vertical { background: #1e1f24; width: 12px; }
QScrollBar::handle:vertical { background: #3a3b41; border-radius: 6px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar:horizontal { background: #1e1f24; height: 12px; }
QScrollBar::handle:horizontal { background: #3a3b41; border-radius: 6px; min-width: 30px; }
QLabel#Title { font-size: 22px; font-weight: 700; }
QLabel#SectionTitle { font-size: 16px; font-weight: 600; }
QLabel#Muted { color: #9ba0aa; }
QLabel#Error { color: #e74c3c; }
QLabel#Success { color: #2ecc71; }
QStatusBar { background-color: #17181c; color: #9ba0aa; }
QToolTip { background-color: #26272d; color: #e6e6e6; border: 1px solid #3a3b41; }
"""

_LIGHT_QSS = """
QMainWindow, QWidget#PageRoot {
    background-color: #f4f5f7;
    color: #1f2328;
}
QWidget {
    color: #1f2328;
    font-size: 13px;
}
QWidget#Sidebar {
    background-color: #e9ebee;
    border-right: 1px solid #d3d6db;
}
QPushButton {
    background-color: #ffffff;
    border: 1px solid #c9ccd1;
    border-radius: 6px;
    padding: 6px 14px;
    color: #1f2328;
}
QPushButton:hover { background-color: #f0f1f3; }
QPushButton:pressed { background-color: #e2e4e8; }
QPushButton:disabled { color: #9aa0a8; background-color: #ececee; }
QPushButton#PrimaryButton {
    background-color: #2f6fed;
    border-color: #2f6fed;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#PrimaryButton:hover { background-color: #3d7bf0; }
QPushButton#DangerButton { background-color: #c0392b; border-color: #c0392b; color: white; }
QPushButton#DangerButton:hover { background-color: #d1483a; }
QPushButton#NavButton {
    text-align: left;
    padding: 10px 14px;
    border: none;
    border-radius: 0;
    background-color: transparent;
    color: #4b5158;
    font-size: 14px;
}
QPushButton#NavButton:hover { background-color: #dfe1e5; color: #1f2328; }
QPushButton#NavButton:checked { background-color: #2f6fed; color: #ffffff; }
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #ffffff;
    border: 1px solid #c9ccd1;
    border-radius: 6px;
    padding: 6px 10px;
    selection-background-color: #2f6fed;
}
QLineEdit:focus, QComboBox:focus { border-color: #2f6fed; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background-color: #ffffff;
    border: 1px solid #c9ccd1;
    selection-background-color: #2f6fed;
    color: #1f2328;
}
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QListWidget, QTableWidget, QTableView {
    background-color: #ffffff;
    alternate-background-color: #f0f1f3;
    border: 1px solid #d3d6db;
    border-radius: 6px;
    gridline-color: #e2e4e8;
}
QHeaderView::section {
    background-color: #e9ebee;
    border: none;
    border-bottom: 1px solid #d3d6db;
    padding: 8px;
    font-weight: 600;
}
QTableWidget::item:selected, QListWidget::item:selected {
    background-color: #2f6fed;
    color: white;
}
QListWidget::item { padding: 6px; }
QTabWidget::pane { border: 1px solid #d3d6db; }
QTabBar::tab {
    background: #e9ebee;
    padding: 8px 16px;
    border: 1px solid #d3d6db;
    border-bottom: none;
}
QTabBar::tab:selected { background: #ffffff; }
QGroupBox {
    border: 1px solid #d3d6db;
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #6a7078;
}
QProgressBar {
    border: 1px solid #c9ccd1;
    border-radius: 6px;
    background-color: #ffffff;
    text-align: center;
    height: 18px;
}
QProgressBar::chunk {
    background-color: #2f6fed;
    border-radius: 5px;
}
QScrollBar:vertical { background: #f4f5f7; width: 12px; }
QScrollBar::handle:vertical { background: #c9ccd1; border-radius: 6px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar:horizontal { background: #f4f5f7; height: 12px; }
QScrollBar::handle:horizontal { background: #c9ccd1; border-radius: 6px; min-width: 30px; }
QLabel#Title { font-size: 22px; font-weight: 700; }
QLabel#SectionTitle { font-size: 16px; font-weight: 600; }
QLabel#Muted { color: #6a7078; }
QLabel#Error { color: #e74c3c; }
QLabel#Success { color: #27ae60; }
QStatusBar { background-color: #e9ebee; color: #6a7078; }
QToolTip { background-color: #ffffff; color: #1f2328; border: 1px solid #c9ccd1; }
"""

THEMES: dict[str, str] = {
    "dark": _DARK_QSS,
    "light": _LIGHT_QSS,
}


def apply_theme(app, theme: str) -> None:
    """Apply a named theme ('dark' | 'light' | 'system')."""
    if theme == "system":
        from PySide6.QtGui import QGuiApplication

        palette = QGuiApplication.styleHints().colorScheme()
        resolved = "light" if str(palette) == "ColorScheme.Light" else "dark"
    else:
        resolved = theme if theme in THEMES else "dark"
    app.setStyleSheet(THEMES[resolved])
    app.setProperty("theme", resolved)