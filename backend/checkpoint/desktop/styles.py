"""Shared Pearl Glass controls for every native window and nested dialog."""
from . import colors
from .colors import css


def popup_css(c):
    top = css(colors.alpha(c.button, 232))
    bottom = css(colors.alpha(c.surface_3, 210))
    rim = css(c.rim)
    field = css(colors.alpha(c.field, 238))
    selected = css(colors.alpha(c.selected, 242))
    return f"""
QWidget {{ color: {css(c.text)}; font-family: 'Segoe UI'; font-size: 14px; }}
QDialog, QFileDialog, QFrame#inbox, QWidget#root, QWidget#settingsPage {{ background: transparent; border: none; }}
QLabel {{ background: transparent; color: {css(c.text)}; }}
QLabel#muted {{ color: {css(c.muted)}; font-size: 13px; }}
QLabel#title {{ font-size: 23px; font-weight: 600; }}
QLabel#error {{ color: {css(c.danger)}; font-size: 13px; }}
QLabel#thumb, QLabel#imagePreview {{ background: {field}; border: 1px solid {rim}; border-radius: 16px; padding: 4px; }}
QLabel#counter {{ background: {top}; border: 1px solid {rim}; border-radius: 16px; padding: 5px 18px; font-weight: 600; }}
QLineEdit, QSpinBox, QPlainTextEdit {{ background: {field}; color: {css(c.text)}; border: 1px solid {css(c.border)};
    border-radius: 12px; padding: 10px 12px; min-height: 20px; selection-background-color: {css(c.accent)}; selection-color: {css(c.on_accent)}; }}
QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus {{ border-color: {css(c.accent)}; }}
QLineEdit#taskSearch {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {top},stop:1 {bottom});
    border-color: {rim}; border-radius: 24px; padding-left: 12px; min-height: 26px; max-height: 26px; }}
QComboBox, QPushButton {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {top},stop:1 {bottom});
    color: {css(c.text)}; border: 1px solid {rim}; border-radius: 20px; min-height: 20px; padding: 10px 18px; }}
QComboBox {{ padding-right: 32px; }}
QComboBox#toolbarPicker {{ min-height: 26px; max-height: 26px; border-radius: 24px; }}
QComboBox::drop-down {{ border: none; width: 30px; }}
QComboBox::down-arrow {{ image: url({colors.arrow_icon(c.text_2)}); width: 10px; height: 6px; margin-right: 8px; }}
QComboBox QLineEdit {{ background: transparent; border: none; padding: 0; }}
QComboBox:focus, QPushButton:focus, QToolButton:focus {{ border-color: {css(c.accent)}; }}
QPushButton:hover, QToolButton:hover {{ background: {selected}; border-color: {css(c.border_strong)}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {css(c.selected)}; }}
QPushButton#primary {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {css(c.accent)},stop:1 {css(c.accent_strong)});
    border-color: {css(c.focus)}; color: {css(c.on_accent)}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {css(c.accent_strong)}; }}
QPushButton#primary:focus {{ border: 2px solid {css(c.focus)}; }}
QPushButton#danger {{ color: {css(c.danger)}; }}
QPushButton#glassCircle, QPushButton#recordButton {{ border-radius: 22px; padding: 0; min-height: 42px; max-height: 42px; min-width: 42px; max-width: 42px; }}
QPushButton#recordButton[recording="true"] {{ background: {css(colors.alpha(c.danger, 30))}; border-color: {css(colors.alpha(c.danger, 110))}; }}
QPushButton:disabled, QComboBox:disabled {{ color: {css(c.faint)}; background: {css(colors.alpha(c.surface, 155))}; }}
QToolButton {{ background: {top}; color: {css(c.text_2)}; border: 1px solid {rim}; border-radius: 14px; padding: 6px; }}
QTabWidget::pane, QListWidget, QFrame#activityCard {{ background: {css(colors.alpha(c.card, 225))};
    color: {css(c.text)}; border: 1px solid {rim}; border-radius: 22px; }}
QTabWidget::pane {{ padding: 12px; }}
QTabBar {{ background: {css(colors.alpha(c.button, 170))}; border: 1px solid {rim}; border-radius: 22px; }}
QTabBar::tab {{ background: transparent; color: {css(c.muted)}; padding: 10px 18px; border: 1px solid transparent; border-radius: 18px; margin: 3px; }}
QTabBar::tab:selected {{ background: {selected}; color: {css(c.text)}; border-color: {rim}; }}
QTabBar::tab:hover {{ color: {css(c.text)}; }}
QAbstractItemView, QComboBox QAbstractItemView {{ background: {css(c.surface_2)}; color: {css(c.text)};
    alternate-background-color: {css(c.card)}; border: 1px solid {css(c.border)}; border-radius: 12px;
    selection-background-color: {css(c.selected)}; selection-color: {css(c.text)}; outline: none; }}
QAbstractItemView::item {{ min-height: 28px; padding: 3px 6px; }}
QAbstractItemView::item:selected {{ background: {css(c.selected)}; color: {css(c.text)}; }}
QHeaderView::section {{ background: {css(c.surface_3)}; color: {css(c.text_2)}; padding: 10px; border: none; border-bottom: 1px solid {css(c.border)}; }}
QMenu {{ background: {css(c.surface_2)}; color: {css(c.text)}; border: 1px solid {css(c.border)}; border-radius: 12px; padding: 6px; }}
QMenu::item {{ padding: 8px 18px; border-radius: 8px; }}
QMenu::item:selected {{ background: {css(c.selected)}; color: {css(c.text)}; }}
QMenu::separator {{ height: 1px; background: {css(c.border)}; margin: 4px 8px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ width: 7px; background: transparent; margin: 3px; }}
QScrollBar:horizontal {{ height: 7px; background: transparent; margin: 3px; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: {css(c.border_strong)}; border-radius: 3px; min-height: 20px; min-width: 20px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QCheckBox {{ color: {css(c.text)}; padding: 6px 2px; spacing: 8px; }}
QProgressBar {{ background: {field}; border: 1px solid {css(c.border)}; border-radius: 5px; min-height: 10px; max-height: 10px; }}
QProgressBar::chunk {{ background: {css(c.accent)}; border-radius: 5px; }}
"""


def note_css(c):
    return popup_css(c)
