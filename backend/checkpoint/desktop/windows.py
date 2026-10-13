"""Native quick-note window and non-focus-stealing toast (PySide6, main thread only)."""

from __future__ import annotations

import time
from typing import Callable, Optional

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QFont, QGuiApplication, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..capture import win32
from . import colors
from .colors import css
from .icons import app_logo, control_icon
from .backdrop import capture_backdrop, paint_backdrop

from .styles import note_css, popup_css


CATEGORIES = [("", "No category"), ("bug", "Bug"), ("improvement", "Improvement"), ("idea", "Idea"),
              ("task", "Task"), ("question", "Question"), ("note", "Note")]


def logical_point_for_physical(x: int, y: int) -> QPoint:
    """Map a physical-pixel point (win32/mss) to Qt logical coordinates on mixed-DPI setups."""
    for screen in QGuiApplication.screens():
        g = screen.geometry()
        dpr = screen.devicePixelRatio()
        nx, ny = g.x(), g.y()  # Qt keeps screen origins in native coordinates
        if nx <= x < nx + g.width() * dpr and ny <= y < ny + g.height() * dpr:
            return QPoint(int(nx + (x - nx) / dpr), int(ny + (y - ny) / dpr))
    return QPoint(x, y)


def microphone_icon() -> QIcon:
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor(colors.current().text))
    painter.setBrush(QColor(colors.current().text))
    painter.drawRoundedRect(QRectF(9, 2, 6, 12), 3, 3)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(QRectF(5, 5, 14, 13), 180 * 16, 180 * 16)
    painter.drawLine(12, 18, 12, 22)
    painter.drawLine(8, 22, 16, 22)
    painter.end()
    return QIcon(pixmap)


class NoteWindow(QWidget):
    """Shown when a capture needs typed context, or for an F9 text-only note."""

    def __init__(self, on_save: Callable[[dict, str, Optional[str]], Optional[str]],
                 on_keep: Callable[[dict], None], on_discard: Callable[[dict], Optional[str]], on_menu=lambda: None):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.on_save, self.on_keep, self.on_discard = on_save, on_keep, on_discard
        self.on_menu = on_menu
        self.payload: dict = {}
        self._esc_armed = 0.0
        self._dictation = None
        self._dictation_save = False
        self.dictation_timer = QTimer(self, interval=100, timeout=self._poll_dictation)
        self._theme = ""
        self._apply_theme()
        root = self.root = QWidget(self)
        root.setObjectName("root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(24, 22, 24, 22)
        lay.setSpacing(12)
        self.heading = QLabel("Screenshot saved")
        self.heading.setObjectName("title")
        header = QHBoxLayout()
        header.addWidget(app_logo(self))
        header.addWidget(self.heading, 1)
        self.menu_btn = QPushButton('Open menu')
        self.menu_btn.clicked.connect(self._open_menu)
        header.addWidget(self.menu_btn)
        self.close_button = QPushButton()
        self.close_button.setObjectName('glassCircle')
        self.close_button.setIcon(control_icon('close'))
        self.close_button.setFixedSize(44, 44)
        self.close_button.setAccessibleName('Close and keep changes')
        self.close_button.setToolTip('Keep changes and close')
        self.close_button.clicked.connect(self._keep)
        header.addWidget(self.close_button)
        lay.addLayout(header)
        self.sub = QLabel("")
        self.sub.setObjectName("muted")
        self.sub.setWordWrap(True)
        self.thumb = QLabel()
        self.thumb.setObjectName("thumb")
        self.thumb.setFixedHeight(170)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.edit_image_btn = QPushButton("Edit image")
        self.edit_image_btn.clicked.connect(self._edit_image)
        self.image_picker = QComboBox()
        self.image_picker.currentIndexChanged.connect(self._selected_image)
        self.snip_btn = QPushButton('Snip image')
        self.snip_btn.clicked.connect(self._snip)
        self.remove_image_btn = QPushButton('Remove image')
        self.remove_image_btn.clicked.connect(self._remove_image)
        self.bundle_btn = QPushButton('Add to last task')
        self.bundle_btn.setToolTip('Combine this task with the preceding open task in this project.')
        self.bundle_btn.clicked.connect(self._bundle)
        for button, icon, label in ((self.edit_image_btn, 'pencil', 'Edit selected image'),
                                     (self.snip_btn, 'images', 'Snip a new image into this task'),
                                     (self.remove_image_btn, 'close', 'Remove selected image from task')):
            button.setText('')
            button.setObjectName('glassCircle')
            button.setFixedSize(44, 44)
            button.setIcon(control_icon(icon))
            button.setAccessibleName(label)
            button.setToolTip(label)
        self.prompt = QLabel("What should we remember about this?")
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Describe what you noticed…")
        self.text.setAccessibleName("What should we remember about this?")
        self.text.setToolTip('Dotted underline: uncertain recognition. Select recorded words and press Play to hear that part.')
        self.text.setMinimumHeight(110)
        self.text.setMaximumHeight(140)
        self.prompt.setBuddy(self.text)
        self.mic_btn = QPushButton("Dictate note")
        self.mic_btn.setIcon(microphone_icon())
        self.mic_btn.setAccessibleName("Dictate note using microphone")
        self.mic_btn.setToolTip("Record a voice note with Whistle. Click again to stop; maximum 60 seconds.")
        self.mic_btn.clicked.connect(self._toggle_dictation)
        self.dictation_status = QLabel("")
        self.dictation_status.setObjectName("muted")
        self.dictation_status.setWordWrap(True)
        row = QHBoxLayout()
        self.category = QComboBox()
        self.category.setAccessibleName("Category (optional)")
        for value, label in CATEGORIES:
            self.category.addItem(label, value)
        row.addWidget(QLabel("Category"))
        row.addWidget(self.category, 1)
        self.error = QLabel("")
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        self.error.hide()
        btns = QHBoxLayout()
        self.discard_btn = QPushButton("Discard")
        self.discard_btn.setObjectName("danger")
        self.keep_btn = QPushButton("Keep for later")
        self.keep_btn.setToolTip('Keep for later (Esc)')
        self.save_btn = QPushButton("Save")
        self.save_btn.setToolTip('Save (Ctrl+Enter)')
        self.save_btn.setObjectName("primary")
        btns.addWidget(self.discard_btn)
        btns.addStretch(1)
        btns.addWidget(self.keep_btn)
        btns.addWidget(self.save_btn)
        for w in (self.sub, self.thumb):
            lay.addWidget(w)
        image_actions = QHBoxLayout()
        for w in (self.image_picker, self.edit_image_btn, self.snip_btn, self.remove_image_btn, self.bundle_btn):
            image_actions.addWidget(w)
        lay.addLayout(image_actions)
        for w in (self.prompt, self.text):
            lay.addWidget(w)
        self.play_btn = QPushButton()
        self.play_btn.setIcon(control_icon('play'))
        self.play_btn.setObjectName('glassCircle')
        self.play_btn.setFixedSize(44, 44)
        self.play_btn.setAccessibleName('Play voice note')
        from .playback import VoicePlayback
        self.playback = VoicePlayback(self.play_btn, self._show_error)
        self.play_btn.hide()
        self.text.selectionChanged.connect(self._select_voice)
        voice_actions = QHBoxLayout()
        voice_actions.addWidget(self.play_btn)
        voice_actions.addWidget(self.mic_btn, 1)
        lay.addLayout(voice_actions)
        lay.addWidget(self.dictation_status)
        lay.addLayout(row)
        lay.addWidget(self.error)
        lay.addLayout(btns)
        self.setFixedWidth(480)
        self.save_btn.clicked.connect(self._save)
        self.keep_btn.clicked.connect(self._keep)
        self.discard_btn.clicked.connect(self._discard)
        for seq in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(seq), self, activated=self._save)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self._keep)

    # ------------------------------------------------------------ show
    def _apply_theme(self) -> None:
        """Restyle when the theme changed since the window was last shown."""
        c = colors.current()
        if c.name != self._theme:
            self._theme = c.name
            self.setStyleSheet(note_css(c))

    def open_for(self, payload: dict) -> None:
        if self.isVisible() and self.payload.get('id') == payload.get('id'):
            self.raise_()
            self.activateWindow()
            return
        self._cancel_dictation()
        self.playback.stop()
        self._apply_theme()
        self.mic_btn.setIcon(microphone_icon())
        if payload.get('id'):
            from ..services.task_evidence import capture_editor_payload
            try:
                payload = {**payload, **capture_editor_payload(payload['id'])}
            except ValueError:
                pass
        self.payload = payload
        is_capture = bool(payload.get("id"))
        self.text.setPlainText(payload.get('text', ''))
        self.category.setCurrentIndex(max(0, self.category.findData(payload.get('category', ''))))
        self._original_text = self.text.toPlainText()
        self.error.hide()
        self._esc_armed = 0.0
        self._refresh_images()
        self.snip_btn.setVisible(bool(payload.get('item_id')))
        self.bundle_btn.setVisible(bool(payload.get('item_id')))
        self.playback.set_evidence(payload.get('evidence', []))
        self._highlight_uncertain()
        if is_capture:
            self.heading.setText('Edit task' if payload.get('item_id') else 'Screenshot saved')
            reason = payload.get("reason")
            self.sub.setText("No audible audio detected, so add a quick note." if reason == "no_audio" else "Add notes or another image to this task.")
            pix = QPixmap(payload.get("thumb_path", ""))
            if not pix.isNull():
                self.thumb.setPixmap(pix.scaled(404, 168, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            self.thumb.show()
            self.edit_image_btn.show()
            self.keep_btn.setText("Keep for later")
            self.discard_btn.show()
        else:
            self.heading.setText("Quick note")
            self.sub.setText("A text-only note, no screenshot.")
            self.thumb.hide()
            self.edit_image_btn.hide()
            self.keep_btn.setText("Cancel")
            self.discard_btn.hide()
        if self.image_picker.count():
            self._selected_image()
        self._fit()
        self._place(payload.get("work_rect"))
        self.backdrop = capture_backdrop(self)
        self.show()
        win32.exclude_from_capture(int(self.winId()))
        self.raise_()
        self.activateWindow()
        win32.restore_foreground(int(self.winId()))
        self.text.setFocus()

    def _refresh_images(self):
        captures = [e for e in self.payload.get('evidence', []) if e['kind'] == 'capture']
        self.image_picker.blockSignals(True)
        self.image_picker.clear()
        for index, capture in enumerate(captures, 1):
            self.image_picker.addItem(f'Image {index}', capture['capture_id'])
        self.image_picker.blockSignals(False)
        self.image_picker.setVisible(len(captures) > 1)
        self.remove_image_btn.setVisible(bool(captures))
        if captures:
            self._selected_image()

    def _selected_image(self):
        cid = self.image_picker.currentData()
        if cid:
            from .editor import capture_path
            self.payload['id'] = cid
            pix = QPixmap(str(capture_path(cid)))
            self.thumb.setPixmap(pix.scaled(404, 168, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def _persist_editor(self):
        text = self.text.toPlainText()
        category = self.category.currentData() or None
        if (text.strip() or self.payload.get('item_id')) and (text != self.payload.get('text', '') or category != self.payload.get('category')):
            error = self.on_save(self.payload, text, self.category.currentData() or None)
            if error:
                self._show_error(error)
                return False
            self.payload['text'] = text
            self.payload['category'] = category
        return True

    def _reload_evidence(self):
        from ..services.review import get_work_item
        item = get_work_item(self.payload['item_id'])
        self.payload['evidence'] = item['evidence']
        self._refresh_images()
        self.playback.set_evidence(item['evidence'])
        self._highlight_uncertain()
        self.thumb.setVisible(self.image_picker.count() > 0)
        self.edit_image_btn.setVisible(self.image_picker.count() > 0)
        if not self.image_picker.count():
            self.payload.pop('id', None)

    def _highlight_uncertain(self):
        import re
        from PySide6.QtGui import QTextCursor
        from PySide6.QtWidgets import QTextEdit
        selections = []
        for entry in self.payload.get('evidence', []):
            for word in entry.get('speech_words') or []:
                if not word.get('low'):
                    continue
                for match in re.finditer(re.escape(word['text']), self.text.toPlainText(), re.I):
                    selection = QTextEdit.ExtraSelection()
                    selection.cursor = self.text.textCursor()
                    # Qt positions count UTF-16 code units.
                    start = len(self.text.toPlainText()[:match.start()].encode('utf-16-le')) // 2
                    end = len(self.text.toPlainText()[:match.end()].encode('utf-16-le')) // 2
                    selection.cursor.setPosition(start)
                    selection.cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                    selection.format.setUnderlineStyle(selection.format.UnderlineStyle.DotLine)
                    selection.format.setUnderlineColor(colors.current().warn)
                    selections.append(selection)
        self.text.setExtraSelections(selections)

    def _select_voice(self):
        selected = self.text.textCursor().selectedText().strip().lower()
        evidence = self.payload.get('evidence', [])
        self.playback.set_evidence(evidence)
        if not selected:
            return
        for entry in evidence:
            words = entry.get('speech_words') or []
            for index, word in enumerate(words):
                for end in range(index, min(len(words), index + 12)):
                    phrase = ' '.join(w['text'] for w in words[index:end + 1]).strip().lower()
                    if phrase == selected and entry.get('audio_url'):
                        self.playback.entries = [{**entry, 'start_s': word['start'], 'end_s': words[end]['end']}]
                        self.play_btn.setToolTip('Play selected words')
                        return

    def _bundle(self):
        if not self._persist_editor():
            return
        from ..services.task_evidence import bundle_previous
        from ..services.review import get_work_item
        try:
            iid = bundle_previous(self.payload['item_id'])
            item = get_work_item(iid)
            self.payload['item_id'], self.payload['text'] = iid, item['description']
            self.text.setPlainText(item['description'])
            self._reload_evidence()
            self.sub.setText('Added to the previous task.')
        except ValueError as error:
            self._show_error(str(error))

    def _snip(self):
        if not self._persist_editor():
            return
        from .snipping import snip_into
        def done(error):
            if error:
                self._show_error(error)
            else:
                self._reload_evidence()
                self._fit()
        snip_into(self.payload['item_id'], self, done)

    def _remove_image(self):
        if not self._persist_editor():
            return
        from ..services.task_evidence import detach_capture
        try:
            detach_capture(self.payload['item_id'], self.payload['id'])
            self._reload_evidence()
        except ValueError as error:
            self._show_error(str(error))

    def _open_menu(self):
        if self._dictation is not None or not self._persist_editor():
            return
        self._close()
        self.on_menu()

    def paintEvent(self, event):
        from .glass import paint_glass
        painter = QPainter(self)
        paint_backdrop(painter, self)
        paint_glass(painter, self.rect().adjusted(1, 1, -1, -1), colors.current(), radius=30)
        painter.end()

    def _edit_image(self) -> None:
        capture_id = self.payload.get("id")
        if not capture_id:
            return
        from .editor import ScreenshotDialog, capture_path

        ScreenshotDialog(capture_id, self).exec()
        pix = QPixmap(str(capture_path(capture_id)))
        if not pix.isNull():
            self.thumb.setPixmap(pix.scaled(404, 168, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        if self.payload.get('item_id'):
            self._reload_evidence()

    def _place(self, work: Optional[dict]) -> None:
        if work:
            br = logical_point_for_physical(work["left"] + work["width"] - 1, work["top"] + work["height"] - 1)
            tl = logical_point_for_physical(work["left"], work["top"])
            area = QRect(tl, br)
        else:
            area = QGuiApplication.primaryScreen().availableGeometry()
        x = area.right() - self.width() - 24
        y = area.bottom() - self.height() - 24
        self.move(max(area.left(), x), max(area.top(), y))

    def _close(self) -> None:
        self._cancel_dictation()
        self.playback.stop()
        prev = self.payload.get("fg_hwnd") or 0
        self.hide()
        if prev:
            win32.restore_foreground(prev)
        self.payload = {}

    def _toggle_dictation(self):
        if self._dictation is not None:
            self._dictation.stop()
            self.mic_btn.setEnabled(False)
            self.dictation_status.setText("Transcribing…")
            return
        from ..audio.dictation import NoteDictation
        from ..settings_store import get_settings
        from ..transcription.engine import model_installed
        if not model_installed("whistle"):
            self._show_error("Download Whistle in Settings → Transcript to dictate notes.")
            return
        self.error.hide()
        self._dictation_save = False
        self._dictation = NoteDictation(get_settings().audio.mic_device)
        self._dictation.project_id = self.payload.get('project_id')
        self.mic_btn.setText("Stop dictation")
        self.dictation_status.setText("Opening microphone…")
        self._dictation.start()
        self.dictation_timer.start()
        self._fit()

    def _poll_dictation(self):
        recording = self._dictation
        if recording is None:
            return
        phase = recording.phase
        audio = getattr(recording, 'audio', b'')
        if audio and self.payload.get('id') and not getattr(recording, 'retained_note_id', None):
            from ..services.voice import retain
            try:
                recording.retained_note_id = retain(self.payload['id'], audio)
            except (ValueError, OSError) as error:
                self._show_error('Could not retain voice recording: ' + str(error))
                return
        if phase != "done":
            self.dictation_status.setText({"starting": "Opening microphone…", "recording": "Listening… click Stop dictation when finished (up to 60 seconds).",
                                           "transcribing": "Transcribing with Whistle…"}[phase])
            if phase == "transcribing":
                self.mic_btn.setEnabled(False)
            return
        save = self._dictation_save
        self._dictation = None
        self.dictation_timer.stop()
        self.mic_btn.setEnabled(True)
        self.mic_btn.setText("Dictate note")
        self._dictation_save = False
        audio = getattr(recording, 'audio', b'')
        if audio and self.payload.get('id'):
            from ..services.voice import retain
            try:
                from ..services.voice import finish_note
                nid = getattr(recording, 'retained_note_id', None) or retain(self.payload['id'], audio, recording.text)
                finish_note(nid, recording.text, getattr(recording, 'words', []))
                self._reload_evidence()
            except (ValueError, OSError) as error:
                self._show_error('Could not retain voice recording: ' + str(error))
                return
        if recording.error:
            self.dictation_status.setText("")
            self._show_error("Couldn't dictate: " + recording.error)
            return
        if not recording.text:
            self.dictation_status.setText("No speech detected. Try again or type your note.")
            return
        # Append to the latest editor contents, preserving anything typed during recording.
        cursor = self.text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText((" " if self.text.toPlainText() and not self.text.toPlainText()[-1].isspace() else "") + recording.text)
        self.text.setTextCursor(cursor)
        self.dictation_status.setText("Dictation added. Edit it or save your note.")
        self._highlight_uncertain()
        self._fit()
        if save:
            self._save()

    def _cancel_dictation(self):
        if self._dictation is not None:
            self._dictation.cancel()
        self._dictation = None
        self._dictation_save = False
        self.dictation_timer.stop()
        self.mic_btn.setEnabled(True)
        self.mic_btn.setText("Dictate note")
        self.dictation_status.setText("")

    # ------------------------------------------------------------ actions
    def _fit(self) -> None:
        """Resize to the content. The layouts keep their old size until their posted relayout runs, so flush it
        first: otherwise the window keeps the previous note's height (cutting off the thumbnail, or leaving a gap)."""
        self.dictation_status.setVisible(bool(self.dictation_status.text()))
        QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        self.root.layout().activate()
        self.layout().activate()
        self.resize(self.width(), self.sizeHint().height())

    def _show_error(self, msg: str) -> None:
        self.error.setText(msg)
        self.error.show()
        self._fit()

    def _save(self) -> None:
        if self._dictation is not None:
            self._dictation_save = True
            self._dictation.stop()
            self.mic_btn.setEnabled(False)
            self.dictation_status.setText("Transcribing before saving…")
            return
        text = self.text.toPlainText()
        if not text.strip():
            self._show_error("Write a short note first — or press Esc to keep the screenshot for later.")
            return
        err = self.on_save(self.payload, text, self.category.currentData() or None)
        if err:
            self._show_error(err)
            return
        self._close()

    def _keep(self) -> None:
        if self._dictation is not None and self.payload.get('id'):
            self._dictation_save = True
            self._dictation.stop()
            self.dictation_status.setText('Finishing your voice note before closing…')
            return
        if self.payload.get('item_id') and self._dictation is None and not self._persist_editor():
            return
        if not self.payload.get("id"):
            if self.text.toPlainText().strip() and time.monotonic() - self._esc_armed > 2:
                self._esc_armed = time.monotonic()
                self._show_error("Press Esc again to discard this note.")
                return
            self._close()
            return
        self.on_keep(self.payload)
        self._close()

    def _discard(self) -> None:
        err = self.on_discard(self.payload)
        if err:
            self._show_error(err)
            return
        self._close()

    def closeEvent(self, e) -> None:  # noqa: ANN001, N802
        e.ignore()
        self._keep()


class Toast(QWidget):
    """Brief confirmation that never takes focus from the game/app."""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        f = QFont()
        f.setPointSize(10)
        self.label.setFont(f)
        self.timer = QTimer(self, singleShot=True, timeout=self.hide)
        self.level = "info"

    def paintEvent(self, _e) -> None:  # noqa: ANN001, N802
        from .glass import paint_glass
        c = colors.current()
        p = QPainter(self)
        paint_glass(p, self.rect().adjusted(1, 1, -1, -1), c, radius=20)
        p.setBrush({"success": c.success, "warning": c.warn, "error": c.danger}.get(self.level, c.accent))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(14, self.height() // 2 - 5, 10, 10)

    def show_message(self, level: str, message: str, work: Optional[dict] = None, duration_ms: Optional[int] = None) -> None:
        self.level = level
        self.label.setText(message)
        self.label.setStyleSheet(f"color:{css(colors.current().text)};")
        self.label.setFixedWidth(320)
        self.label.adjustSize()
        self.resize(self.label.width() + 50, max(44, self.label.height() + 22))
        self.label.move(34, (self.height() - self.label.height()) // 2)
        area = QGuiApplication.primaryScreen().availableGeometry()
        if work:
            tl = logical_point_for_physical(work["left"], work["top"])
            br = logical_point_for_physical(work["left"] + work["width"] - 1, work["top"] + work["height"] - 1)
            area = QRect(tl, br)
        self.move(area.right() - self.width() - 20, area.bottom() - self.height() - 20)
        self.show()
        win32.exclude_from_capture(int(self.winId()))
        self.update()
        self.timer.start(duration_ms or (2600 if level != "error" else 6000))
