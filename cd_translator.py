"""Crimson Desert – translation comparer.

Scans the game's language folders, exports two languages side by side,
lets you review and fix the translation, prepares tasks for an AI agent
and installs the corrections back into the game.
"""

import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QAbstractTableModel, QLocale, QModelIndex, QSortFilterProxyModel, Qt, QTimer, QUrl
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtGui import QColor, QFont, QKeySequence, QShortcut, QSyntaxHighlighter, QTextCharFormat
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QGroupBox, QInputDialog,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox,
    QSplitter, QTableView, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

import cd_ai_api as ai_api
import cd_audio as audio
import cd_core as core
import cd_patch as patch
import cd_stt as stt
import i18n
from cd_ui_common import start_worker
from cd_ui_voice import VoiceTab
from i18n import tr

FILTERS = ["all", "errors", "warnings", "edited", "flagged", "ai", "voice", "timing", "outdated",
           "reviewed", "unreviewed"]
AI_MODES = ["errors", "problems", "voice", "flagged", "edited", "all"]

# Open windows; a new one replaces the old when the interface language changes
WINDOWS = []

COLOR_ERROR = QColor(220, 60, 60, 70)
COLOR_AI = QColor(60, 120, 230, 70)
COLOR_FLAG = QColor(230, 180, 30, 90)
COLOR_EDITED = QColor(60, 170, 80, 70)
COLOR_WARNING = QColor(230, 140, 30, 35)


def one_line(text, limit=300):
    text = text.replace("<br/>", " ⏎ ").replace("\n", " ⏎ ")
    return text if len(text) <= limit else text[:limit] + "…"


def status_text(e):
    parts = []
    if e["_errors"]:
        parts.append(tr("status.error"))
    elif e["_warnings"]:
        parts.append(tr("status.warning"))
    if e["target"] != e["original"]:
        parts.append("✎")
    if e.get("ai") is not None:
        parts.append("AI")
    if e.get("flag"):
        parts.append("⚑")
    if e["_voice_issue"]:
        parts.append("🔊")
    if e["_timing_issue"]:
        parts.append("⏱")
    if e.get("outdated"):
        parts.append("⟳")
    if e.get("reviewed"):
        parts.append("✔")
    return " ".join(parts)


def entry_color(e):
    if e["_errors"]:
        return COLOR_ERROR
    if e.get("ai") is not None:
        return COLOR_AI
    if e.get("flag") or e.get("outdated"):
        return COLOR_FLAG
    if e["target"] != e["original"]:
        return COLOR_EDITED
    if e["_warnings"]:
        return COLOR_WARNING
    return None


class TokenHighlighter(QSyntaxHighlighter):
    """Colours {Key:...}, {StaticInfo:...#text} and <br/> so they are easy to keep intact."""

    def __init__(self, document):
        super().__init__(document)
        self.tag = QTextCharFormat()
        self.tag.setForeground(QColor(130, 130, 130))
        self.token = QTextCharFormat()
        self.token.setForeground(QColor(40, 110, 220))
        self.token.setFontWeight(QFont.Weight.Bold)
        self.display = QTextCharFormat()
        self.display.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SingleUnderline)

    def highlightBlock(self, text):
        for m in core.TOKEN_RE.finditer(text):
            if m.group(3) is not None:
                self.setFormat(m.start(), m.end() - m.start(), self.tag)
                continue
            self.setFormat(m.start(), m.end() - m.start(), self.token)
            if m.group(2):
                start = m.start(2) + 1
                self.setFormat(start, m.end(2) - start, self.display)


class EntriesModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.entries = []
        self.headers = [tr("col.key"), tr("detail.source").rstrip(":"), tr("detail.target").rstrip(":"),
                        tr("col.status")]
        self.on_edit = None

    def set_entries(self, entries, src_name, tgt_name):
        self.beginResetModel()
        self.entries = entries
        self.headers = [tr("col.key"), src_name, tgt_name, tr("col.status")]
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()):
        return 4

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        e = self.entries[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return [e["key"], one_line(e["source"]), one_line(e["target"]), status_text(e)][col]
        if role == Qt.ItemDataRole.EditRole and col == 2:
            return e["target"]
        if role == Qt.ItemDataRole.ToolTipRole:
            if col == 1:
                return e["source"]
            if col == 2:
                return e["target"]
            if col == 3:
                return "\n".join(e["_errors"] + e["_warnings"]) or None
        if role == Qt.ItemDataRole.BackgroundRole:
            return entry_color(e)
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or index.column() != 2:
            return False
        e = self.entries[index.row()]
        if value == e["target"]:
            return False
        e["target"] = value
        core.refresh_issues(e)
        self.row_changed(index.row())
        if self.on_edit:
            self.on_edit(index.row())
        return True

    def flags(self, index):
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 2:
            f |= Qt.ItemFlag.ItemIsEditable
        return f

    def row_changed(self, row):
        self.dataChanged.emit(self.index(row, 0), self.index(row, 3))


class EntriesFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.mode = "all"
        self.text = ""

    def set_filter(self, mode, text):
        self.mode = mode
        self.text = text.strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        e = self.sourceModel().entries[row]
        m = self.mode
        if m == "errors" and not e["_errors"]:
            return False
        if m == "warnings" and not e["_warnings"]:
            return False
        if m == "edited" and e["target"] == e["original"]:
            return False
        if m == "flagged" and not e.get("flag"):
            return False
        if m == "ai" and e.get("ai") is None:
            return False
        if m == "voice" and not e["_voice_issue"]:
            return False
        if m == "timing" and not e["_timing_issue"]:
            return False
        if m == "outdated" and not e.get("outdated"):
            return False
        if m == "reviewed" and not e.get("reviewed"):
            return False
        if m == "unreviewed" and (e.get("reviewed") or e["target"] == e["original"]):
            return False
        if self.text:
            t = self.text
            return t in e["key"] or t in e["source"].lower() or t in e["target"].lower()
        return True


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("app.title"))
        self.resize(1260, 780)
        self.cfg = core.load_config()
        self.scan = []
        self.project = None
        self.current_file = None
        self.current_entry = None
        self.current_row = None
        self.loading = False

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.tabs.addTab(self._build_setup_tab(), tr("tab.setup"))
        self.tabs.addTab(self._build_compare_tab(), tr("tab.compare"))
        self.tabs.addTab(self._build_ai_tab(), tr("tab.ai"))
        self.tabs.addTab(self._build_install_tab(), tr("tab.install"))
        self.voice_tab = VoiceTab(self)
        self.tabs.addTab(self.voice_tab, tr("tab.voice"))
        self.tabs.currentChanged.connect(lambda i: self.voice_tab.refresh() if i == 4 else None)
        for i in (1, 2, 3, 4):
            self.tabs.setTabEnabled(i, False)

        self.player = QMediaPlayer(self)
        self.audio_out = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_out)
        self.transcriber = None
        self.workers = []

        QShortcut(QKeySequence.StandardKey.Save, self, self.save)
        QShortcut(QKeySequence("Alt+Down"), self, lambda: self.move_selection(1))
        QShortcut(QKeySequence("Alt+Up"), self, lambda: self.move_selection(-1))

        self.stats_timer = QTimer(self, singleShot=True, interval=400)
        self.stats_timer.timeout.connect(self.update_current_file_item)

        self._init_game_dir()

    # ------------------------------------------------------------ tab 1

    def _build_setup_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(QLabel(tr("setup.ui_language")))
        self.ui_lang_combo = QComboBox()
        for code, name in i18n.available().items():
            self.ui_lang_combo.addItem(name, code)
        self.ui_lang_combo.setCurrentIndex(max(self.ui_lang_combo.findData(i18n.current()), 0))
        self.ui_lang_combo.currentIndexChanged.connect(self.change_ui_language)
        row.addWidget(self.ui_lang_combo)
        lay.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel(tr("setup.game_dir")))
        self.game_edit = QLineEdit()
        row.addWidget(self.game_edit, 1)
        browse = QPushButton(tr("setup.browse"))
        browse.clicked.connect(self.browse_game)
        row.addWidget(browse)
        scan_btn = QPushButton(tr("setup.scan"))
        scan_btn.clicked.connect(lambda: self.run_scan(force=True))
        row.addWidget(scan_btn)
        lay.addLayout(row)

        self.lang_table = QTableWidget(0, 4)
        self.lang_table.setHorizontalHeaderLabels(
            [tr("setup.col_folder"), tr("setup.col_language"), tr("setup.col_files"), tr("setup.col_sample")])
        self.lang_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.lang_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.lang_table.verticalHeader().setVisible(False)
        lay.addWidget(self.lang_table, 2)

        row = QHBoxLayout()
        row.addWidget(QLabel(tr("setup.source")))
        self.src_combo = QComboBox()
        row.addWidget(self.src_combo, 1)
        row.addWidget(QLabel(tr("setup.target")))
        self.tgt_combo = QComboBox()
        row.addWidget(self.tgt_combo, 1)
        lay.addLayout(row)
        self.src_combo.currentIndexChanged.connect(self.fill_export_files)
        self.tgt_combo.currentIndexChanged.connect(self.fill_export_files)

        row = QHBoxLayout()
        row.addWidget(QLabel(tr("setup.files")))
        row.addStretch(1)
        for text, state in ((tr("common.check_all"), True), (tr("common.uncheck_all"), False)):
            b = QPushButton(text)
            b.clicked.connect(lambda _, s=state: self.check_all(self.export_files, s))
            row.addWidget(b)
        lay.addLayout(row)
        self.export_files = QListWidget()
        self.export_files.setFlow(QListWidget.Flow.LeftToRight)
        self.export_files.setWrapping(True)
        self.export_files.setResizeMode(QListWidget.ResizeMode.Adjust)
        lay.addWidget(self.export_files, 1)

        row = QHBoxLayout()
        export_btn = QPushButton(tr("setup.export"))
        export_btn.setMinimumHeight(38)
        export_btn.clicked.connect(self.export)
        row.addWidget(export_btn, 2)
        row.addSpacing(30)
        row.addWidget(QLabel(tr("setup.or_open")))
        self.projects_combo = QComboBox()
        row.addWidget(self.projects_combo, 2)
        open_btn = QPushButton(tr("setup.open"))
        open_btn.clicked.connect(self.open_selected_project)
        row.addWidget(open_btn)
        lay.addLayout(row)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        lay.addWidget(self.progress)
        self.refresh_projects()
        return w

    def _init_game_dir(self):
        game = self.cfg.get("game_dir")
        if not game:
            found = core.find_game_dirs()
            game = found[0] if found else ""
        self.game_edit.setText(game)
        cached = self.cfg.get("scan", {}).get(game)
        if cached:
            self.set_scan(cached)
        elif game:
            QTimer.singleShot(200, self.run_scan)

    def change_ui_language(self):
        code = self.ui_lang_combo.currentData()
        if code == i18n.current():
            return
        if not self.confirm_discard():
            self.ui_lang_combo.blockSignals(True)
            self.ui_lang_combo.setCurrentIndex(self.ui_lang_combo.findData(i18n.current()))
            self.ui_lang_combo.blockSignals(False)
            return
        self.cfg["ui_language"] = code
        core.save_config(self.cfg)
        i18n.set_language(code)
        win = MainWindow()
        win.setGeometry(self.geometry())
        if self.project:
            win.open_project(self.project.path)
            win.tabs.setCurrentIndex(0)
        win.show()
        WINDOWS.append(win)
        self.project = None
        self.close()
        if self in WINDOWS:
            WINDOWS.remove(self)

    def browse_game(self):
        path = QFileDialog.getExistingDirectory(self, tr("setup.browse_title"), self.game_edit.text())
        if path:
            self.game_edit.setText(path)
            self.run_scan(force=True)

    def report_progress(self, i, total, label):
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(i)
        self.progress.setFormat(f"{label}  %p%" if label else "%p%")
        QApplication.processEvents()

    def run_scan(self, force=False):
        game = self.game_edit.text().strip()
        if not game or not Path(game).is_dir():
            QMessageBox.warning(self, tr("scan.no_folder_title"), tr("scan.no_folder"))
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = core.scan_game(game, self.report_progress)
        except Exception as e:
            QMessageBox.critical(self, tr("scan.error_title"), str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()
        if not result:
            QMessageBox.warning(self, tr("scan.nothing_title"), tr("scan.nothing"))
            return
        self.progress.setFormat(tr("scan.found", n=len(result)))
        self.cfg["game_dir"] = game
        self.cfg.setdefault("scan", {})[game] = result
        core.save_config(self.cfg)
        self.set_scan(result)

    def set_scan(self, result):
        self.scan = result
        labels = core.scan_labels(result)
        self.lang_table.setRowCount(len(result))
        for i, r in enumerate(result):
            for col, val in enumerate((r["folder"], labels[i], str(len(r["files"])), r["sample"])):
                self.lang_table.setItem(i, col, QTableWidgetItem(val))
        self.lang_table.resizeColumnsToContents()
        for combo, default in ((self.src_combo, "en"), (self.tgt_combo, "pl")):
            combo.blockSignals(True)
            combo.clear()
            for idx, r in enumerate(result):
                combo.addItem(tr("setup.combo_item", label=labels[idx], folder=r["folder"]), idx)
            pick = next((i for i, r in enumerate(result) if r["lang"] == default), 0)
            combo.setCurrentIndex(pick)
            combo.blockSignals(False)
        self.fill_export_files()

    def fill_export_files(self):
        self.export_files.clear()
        if not self.scan:
            return
        src = self.scan[self.src_combo.currentData()]
        tgt = self.scan[self.tgt_combo.currentData()]
        for name in sorted(set(src["files"]) & set(tgt["files"])):
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            hint = core.file_hint(name)
            if hint:
                item.setToolTip(hint)
            self.export_files.addItem(item)

    @staticmethod
    def check_all(widget, state):
        for i in range(widget.count()):
            widget.item(i).setCheckState(Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)

    @staticmethod
    def checked(widget):
        return [widget.item(i).text() for i in range(widget.count())
                if widget.item(i).checkState() == Qt.CheckState.Checked]

    def export(self):
        if not self.scan:
            return
        src = self.scan[self.src_combo.currentData()]
        tgt = self.scan[self.tgt_combo.currentData()]
        if src["folder"] == tgt["folder"]:
            QMessageBox.warning(self, tr("export.same_title"), tr("export.same"))
            return
        files = self.checked(self.export_files)
        if not files:
            QMessageBox.warning(self, tr("export.no_files_title"), tr("export.no_files"))
            return
        if not self.confirm_discard():
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            project = core.export_project(self.game_edit.text().strip(), src, tgt, files, self.report_progress)
        except Exception as e:
            QMessageBox.critical(self, tr("export.error_title"), str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.progress.setFormat(tr("export.done"))
        self.refresh_projects()
        self.open_project(project.path)

    def refresh_projects(self):
        self.projects_combo.clear()
        for p in core.list_projects():
            try:
                self.projects_combo.addItem(core.Project(p).title, str(p))
            except (OSError, ValueError, KeyError):
                continue

    def open_selected_project(self):
        path = self.projects_combo.currentData()
        if path and self.confirm_discard():
            self.open_project(path)

    # ------------------------------------------------------------ project

    def open_project(self, path):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.project = core.Project(path)
            index = audio.load_index(self.project)
            if index:
                self.project.attach_voice(audio.VoiceLibrary(self.project, index), stt.load_transcripts(self.project))
            files = self.project.files
            for i, name in enumerate(files):
                self.report_progress(i, len(files), tr("project.loading", name=name))
                self.project.load_file(name)
            self.report_progress(len(files), len(files), "")
        finally:
            QApplication.restoreOverrideCursor()
        p = self.project
        self.model.set_entries([], core.lang_name(p.src_lang), core.lang_name(p.tgt_lang))
        self.src_label.setText(tr("project.source_label", lang=core.lang_name(p.src_lang)))
        self.tgt_label.setText(tr("project.target_label", lang=core.lang_name(p.tgt_lang)))
        self.file_list.clear()
        self.ai_files.clear()
        for name in files:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.file_list.addItem(item)
            self.update_file_item(item)
            ai_item = QListWidgetItem(name)
            ai_item.setFlags(ai_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            ai_item.setCheckState(Qt.CheckState.Unchecked)
            self.ai_files.addItem(ai_item)
        self.setWindowTitle(tr("project.window_title", app=tr("app.title"), project=p.title))
        for i in (1, 2, 3, 4):
            self.tabs.setTabEnabled(i, True)
        self.tabs.setCurrentIndex(1)
        start = files.index("ui.paloc") if "ui.paloc" in files else 0
        self.file_list.setCurrentRow(start)
        self.update_ai_count()
        self.voice_tab.refresh()
        if core.game_updated(p) and QMessageBox.question(
                self, tr("update.group"), tr("update.ask")) == QMessageBox.StandardButton.Yes:
            QTimer.singleShot(0, self.refresh_from_game)

    def attach_voice(self, library):
        """Use a (new) voice index and the transcripts in the checks and the detail panel."""
        self.project.attach_voice(library, stt.load_transcripts(self.project))
        self.update_all_file_items()
        if self.current_file:
            self.model.beginResetModel()
            self.model.endResetModel()
            self.apply_filter()
        self.show_entry(self.current_entry)

    def refresh_from_game(self):
        """Re-read the texts after a game update; corrections stay, changed originals are marked ⟳."""
        if not self.confirm_discard():
            return
        path = self.project.path
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            core.refresh_project(self.project, self.report_progress)
        except Exception as e:
            QMessageBox.critical(self, tr("export.error_title"), str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.project = None
        self.open_project(path)
        n = sum(self.project.file_stats(name)["outdated"] for name in self.project.files)
        QMessageBox.information(self, tr("update.group"), tr("update.done", n=n))

    def goto_entry(self, name, key):
        """Show one entry in the comparison tab (clears the filter so it is visible)."""
        row = self.project.files.index(name)
        self.tabs.setCurrentIndex(1)
        self.search.clear()
        self.filter_combo.setCurrentIndex(0)
        if self.file_list.currentRow() != row:
            self.file_list.setCurrentRow(row)
        self.apply_filter()
        src_row = next((i for i, e in enumerate(self.model.entries) if e["key"] == key), None)
        if src_row is not None:
            index = self.proxy.mapFromSource(self.model.index(src_row, 0))
            self.table.selectRow(index.row())
            self.table.scrollTo(index)

    def update_file_item(self, item):
        name = item.data(Qt.ItemDataRole.UserRole)
        s = self.project.file_stats(name)
        extra = []
        if s["errors"]:
            extra.append(f"⚠{s['errors']}")
        if s["edited"]:
            extra.append(f"✎{s['edited']}")
        if s["ai"]:
            extra.append(f"AI {s['ai']}")
        if s["flagged"]:
            extra.append(f"⚑{s['flagged']}")
        if s["voice"]:
            extra.append(f"🔊{s['voice']}")
        if s["timing"]:
            extra.append(f"⏱{s['timing']}")
        if s["outdated"]:
            extra.append(f"⟳{s['outdated']}")
        unsaved = " *" if name in self.project.dirty else ""
        item.setText(f"{name}{unsaved}\n   {tr('project.entries', n=s['total'])}  {'  '.join(extra)}")
        item.setToolTip(core.file_hint(name))

    def update_current_file_item(self):
        item = self.file_list.currentItem()
        if item and self.project:
            self.update_file_item(item)

    def update_all_file_items(self):
        for i in range(self.file_list.count()):
            self.update_file_item(self.file_list.item(i))

    def confirm_discard(self):
        if not self.project or not self.project.dirty:
            return True
        answer = QMessageBox.question(
            self, tr("unsaved.title"), tr("unsaved.text"),
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            self.save()
        return True

    def save(self):
        if self.project:
            self.project.save_all()
            self.update_all_file_items()
            self.statusBar().showMessage(tr("saved"), 3000)

    def closeEvent(self, event):
        if self.confirm_discard():
            event.accept()
        else:
            event.ignore()

    # ------------------------------------------------------------ tab 2

    def _build_compare_tab(self):
        split = QSplitter(Qt.Orientation.Horizontal)

        self.file_list = QListWidget()
        self.file_list.setMinimumWidth(200)
        self.file_list.currentItemChanged.connect(self.select_file)
        split.addWidget(self.file_list)

        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("compare.search"))
        self.search.setClearButtonEnabled(True)
        bar.addWidget(self.search, 3)
        self.filter_combo = QComboBox()
        for key in FILTERS:
            self.filter_combo.addItem(tr(f"filter.{key}"), key)
        bar.addWidget(self.filter_combo, 2)
        self.count_label = QLabel()
        bar.addWidget(self.count_label)
        save_btn = QPushButton(tr("compare.save"))
        save_btn.clicked.connect(self.save)
        bar.addWidget(save_btn)
        rlay.addLayout(bar)

        self.search_timer = QTimer(self, singleShot=True, interval=300)
        self.search_timer.timeout.connect(self.apply_filter)
        self.search.textChanged.connect(lambda: self.search_timer.start())
        self.filter_combo.currentIndexChanged.connect(self.apply_filter)

        self.model = EntriesModel()
        self.model.on_edit = self.on_table_edit
        self.proxy = EntriesFilter()
        # Don't hide a row while it is being edited; the filter is re-applied on demand
        self.proxy.setDynamicSortFilter(False)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setWordWrap(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 110)
        self.table.setColumnWidth(3, 90)
        self.table.selectionModel().currentRowChanged.connect(self.select_entry)

        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.addWidget(self.table)
        vsplit.addWidget(self._build_detail())
        vsplit.setSizes([380, 320])
        rlay.addWidget(vsplit, 1)

        split.addWidget(right)
        split.setSizes([230, 1030])
        return split

    def _build_detail(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)

        texts = QHBoxLayout()
        col = QVBoxLayout()
        self.src_label = QLabel(tr("detail.source"))
        col.addWidget(self.src_label)
        self.src_view = QPlainTextEdit(readOnly=True)
        TokenHighlighter(self.src_view.document())
        col.addWidget(self.src_view)
        texts.addLayout(col)

        col = QVBoxLayout()
        self.tgt_label = QLabel(tr("detail.target"))
        col.addWidget(self.tgt_label)
        self.tgt_edit = QPlainTextEdit()
        TokenHighlighter(self.tgt_edit.document())
        self.tgt_edit.textChanged.connect(self.on_detail_edit)
        col.addWidget(self.tgt_edit)
        texts.addLayout(col)

        self.ai_box = QGroupBox(tr("detail.ai_box"))
        col = QVBoxLayout(self.ai_box)
        self.ai_view = QPlainTextEdit(readOnly=True)
        TokenHighlighter(self.ai_view.document())
        col.addWidget(self.ai_view)
        self.ai_reason = QLabel(wordWrap=True)
        col.addWidget(self.ai_reason)
        row = QHBoxLayout()
        for text, slot in ((tr("detail.accept"), self.accept_ai), (tr("detail.reject"), self.reject_ai),
                           (tr("detail.edit"), self.copy_ai_to_editor)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        col.addLayout(row)
        texts.addWidget(self.ai_box)
        self.ai_box.setVisible(False)
        lay.addLayout(texts, 1)

        self.issue_label = QLabel(wordWrap=True)
        self.issue_label.setStyleSheet("color: #c0392b;")
        lay.addWidget(self.issue_label)

        row = QHBoxLayout()
        self.play_btn = QPushButton(tr("detail.play"))
        self.play_btn.setToolTip(tr("detail.play_tip"))
        self.play_btn.clicked.connect(self.play_voice)
        row.addWidget(self.play_btn)
        self.autoplay_check = QCheckBox(tr("detail.autoplay"))
        self.autoplay_check.setChecked(bool(self.cfg.get("autoplay")))
        self.autoplay_check.toggled.connect(self.on_autoplay)
        row.addWidget(self.autoplay_check)
        self.voice_label = QLabel(wordWrap=True)
        row.addWidget(self.voice_label, 1)
        self.heard_btn = QPushButton(tr("detail.transcribe"))
        self.heard_btn.clicked.connect(self.transcribe_line)
        row.addWidget(self.heard_btn)
        lay.addLayout(row)

        self.context_view = QPlainTextEdit(readOnly=True)
        self.context_view.setMaximumHeight(92)
        self.context_view.setPlaceholderText(tr("detail.context"))
        lay.addWidget(self.context_view)

        row = QHBoxLayout()
        self.reviewed_check = QCheckBox(tr("detail.reviewed"))
        self.reviewed_check.setToolTip(tr("detail.reviewed_tip"))
        self.reviewed_check.toggled.connect(self.on_reviewed)
        row.addWidget(self.reviewed_check)
        self.outdated_btn = QPushButton(tr("detail.outdated_ok"))
        self.outdated_btn.clicked.connect(self.resolve_outdated)
        row.addWidget(self.outdated_btn)
        self.flag_check = QCheckBox(tr("detail.flag"))
        self.flag_check.toggled.connect(self.on_flag)
        row.addWidget(self.flag_check)
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText(tr("detail.note"))
        self.note_edit.textEdited.connect(self.on_note)
        row.addWidget(self.note_edit, 1)
        self.revert_btn = QPushButton(tr("detail.revert"))
        self.revert_btn.clicked.connect(self.revert_entry)
        row.addWidget(self.revert_btn)
        for text, step in ((tr("detail.prev"), -1), (tr("detail.next"), 1)):
            b = QPushButton(text)
            b.clicked.connect(lambda _, s=step: self.move_selection(s))
            row.addWidget(b)
        lay.addLayout(row)
        return w

    def select_file(self, item, _previous=None):
        if not item or not self.project:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        self.current_file = name
        self.current_entry = None
        self.current_row = None
        p = self.project
        self.model.set_entries(p.load_file(name), core.lang_name(p.src_lang), core.lang_name(p.tgt_lang))
        self.apply_filter()
        self.show_entry(None)
        if self.proxy.rowCount():
            self.table.selectRow(0)

    def apply_filter(self):
        self.proxy.set_filter(self.filter_combo.currentData(), self.search.text())
        self.count_label.setText(f"{self.proxy.rowCount()} / {self.model.rowCount()}")

    def select_entry(self, current, _previous=None):
        if not current.isValid():
            self.current_row = None
            self.show_entry(None)
            return
        self.current_row = self.proxy.mapToSource(current).row()
        self.show_entry(self.model.entries[self.current_row])

    def show_entry(self, e):
        self.current_entry = e
        self.loading = True
        try:
            enabled = e is not None
            for widget in (self.tgt_edit, self.flag_check, self.note_edit, self.revert_btn, self.reviewed_check):
                widget.setEnabled(enabled)
            self.src_view.setPlainText(e["source"] if e else "")
            self.tgt_edit.setPlainText(e["target"] if e else "")
            self.flag_check.setChecked(bool(e and e.get("flag")))
            self.reviewed_check.setChecked(bool(e and e.get("reviewed")))
            self.note_edit.setText(e.get("note", "") if e else "")
            self.refresh_detail_status()
            self.show_voice_and_context(e)
        finally:
            self.loading = False
        if e and self.autoplay_check.isChecked() and self.voice_lines(e):
            self.play_voice()

    # ------------------------------------------------------------ voice and context

    def voice_lines(self, e):
        p = self.project
        return p.voice.lines(e["_file"], e["key"]) if e and p and p.voice else []

    def show_voice_and_context(self, e):
        self.player.stop()
        lines = self.voice_lines(e)
        self.play_btn.setEnabled(bool(lines))
        self.heard_btn.setEnabled(bool(lines) and stt.available())
        self.outdated_btn.setVisible(bool(e and e.get("outdated")))
        if not e:
            self.voice_label.setText("")
            self.context_view.setPlainText("")
            return
        if lines:
            speaker, _, seconds = lines[0]
            text = tr("detail.voice_info", speaker=audio.speaker_label(speaker), sec=seconds or "?")
            if len(lines) > 1:
                text += tr("detail.more_speakers", n=len(lines) - 1)
            heard = e.get("_heard")
            if heard:
                text += "\n" + tr("detail.heard", text=heard["text"], pct=round(heard.get("ratio", 0) * 100))
            self.voice_label.setText(text)
        elif self.project.voice is None and e["_file"] in audio.VOICED_FILES:
            self.voice_label.setText(tr("detail.no_index"))
        else:
            self.voice_label.setText(tr("detail.no_voice"))
        before, after = self.project.neighbours(e["_file"], e["key"])
        rows = [f"   {one_line(x['source'], 110)}  →  {one_line(x['target'], 110)}" for x in before]
        rows.append(f"▶ {one_line(e['source'], 110)}")
        rows += [f"   {one_line(x['source'], 110)}  →  {one_line(x['target'], 110)}" for x in after]
        self.context_view.setPlainText("\n".join(rows))

    def play_voice(self):
        lines = self.voice_lines(self.current_entry)
        if not lines:
            return
        try:
            wav = self.project.voice.wav(lines[0][1])
        except Exception as e:
            QMessageBox.warning(self, tr("detail.play"), str(e))
            return
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(str(wav)))
        self.player.play()

    def on_autoplay(self, checked):
        self.cfg["autoplay"] = checked
        core.save_config(self.cfg)

    def transcribe_line(self):
        e = self.current_entry
        lines = self.voice_lines(e)
        if not lines:
            return
        p, lib = self.project, self.project.voice
        model = self.cfg.get("stt_model", stt.DEFAULT_MODEL)
        self.heard_btn.setEnabled(False)
        self.voice_label.setText(self.voice_label.text() + "\n" + tr("stt.loading_model"))

        def job(_progress, _stop):
            if self.transcriber is None or self.transcriber.model_name != model:
                self.transcriber = stt.Transcriber(model)
            heard = self.transcriber.transcribe(lib.wav(lines[0][1]), e["source"])
            rec = {"id": f"{e['_file']}|{e['key']}", "text": heard, "model": model,
                   "ratio": round(stt.similarity(e["source"], heard), 3)}
            stt.save_transcript(p, rec)
            p.transcripts[rec["id"]] = rec
            return rec

        def done(_rec):
            p._annotate(e["_file"], e)
            core.refresh_issues(e)
            self.entry_changed()
            self.show_entry(e)

        self.run_worker(job, done)

    def run_worker(self, job, done, on_progress=None):
        def finished(result):
            self.workers.remove(worker)
            done(result)

        def failed(msg):
            self.workers.remove(worker)
            QMessageBox.critical(self, tr("install.error_title"), msg)
            self.show_entry(self.current_entry)

        worker = start_worker(self, job, finished, failed, on_progress)
        self.workers.append(worker)
        return worker

    def on_reviewed(self, checked):
        if self.loading or not self.current_entry:
            return
        if checked:
            self.current_entry["reviewed"] = True
        else:
            self.current_entry.pop("reviewed", None)
        self.entry_changed()

    def resolve_outdated(self):
        e = self.current_entry
        if e and e.get("outdated"):
            core.resolve_outdated(e)
            self.entry_changed()
            self.show_entry(e)

    def refresh_detail_status(self):
        e = self.current_entry
        if not e:
            self.issue_label.setText("")
            self.ai_box.setVisible(False)
            return
        lines = [f"⚠ {x}" for x in e["_errors"]] + [f"• {x}" for x in e["_warnings"]]
        odd = core.odd_chars(e["target"], self.project.charset())
        if odd:
            lines.append("⚠ " + tr("detail.odd_chars", chars="".join(odd)))
        if e["target"] != e["original"]:
            lines.append("✎ " + tr("detail.in_game", text=one_line(e["original"], 200)))
        self.issue_label.setText("\n".join(lines))
        has_ai = e.get("ai") is not None
        self.ai_box.setVisible(has_ai)
        if has_ai:
            self.ai_view.setPlainText(e["ai"])
            reason = e.get("ai_reason", "")
            problems = e["_ai_errors"] + (
                [tr("detail.ai_odd", chars="".join(core.odd_chars(e["ai"], self.project.charset())))]
                if core.odd_chars(e["ai"], self.project.charset()) else [])
            text = tr("detail.reason", reason=reason) if reason else ""
            if problems:
                text += "\n⚠ " + "; ".join(problems)
            self.ai_reason.setText(text)

    def entry_changed(self):
        if self.current_row is not None:
            self.model.row_changed(self.current_row)
        self.project.dirty.add(self.current_file)
        self.stats_timer.start()

    def on_detail_edit(self):
        if self.loading or not self.current_entry:
            return
        e = self.current_entry
        e["target"] = self.tgt_edit.toPlainText()
        core.refresh_issues(e)
        self.entry_changed()
        self.refresh_detail_status()

    def on_table_edit(self, row):
        self.project.dirty.add(self.current_file)
        self.stats_timer.start()
        if self.model.entries[row] is self.current_entry:
            self.show_entry(self.current_entry)

    def on_flag(self, checked):
        if self.loading or not self.current_entry:
            return
        if checked:
            self.current_entry["flag"] = True
        else:
            self.current_entry.pop("flag", None)
        self.entry_changed()

    def on_note(self, text):
        if self.loading or not self.current_entry:
            return
        if text.strip():
            self.current_entry["note"] = text
        else:
            self.current_entry.pop("note", None)
        self.entry_changed()

    def revert_entry(self):
        e = self.current_entry
        if e and e["target"] != e["original"]:
            self.tgt_edit.setPlainText(e["original"])

    def accept_ai(self):
        e = self.current_entry
        if e and e.get("ai") is not None:
            core.accept_ai(e)
            self.entry_changed()
            self.show_entry(e)

    def reject_ai(self):
        e = self.current_entry
        if e and e.get("ai") is not None:
            core.reject_ai(e)
            self.entry_changed()
            self.show_entry(e)

    def copy_ai_to_editor(self):
        e = self.current_entry
        if e and e.get("ai") is not None:
            text = e["ai"]
            core.reject_ai(e)
            self.show_entry(e)
            self.tgt_edit.setPlainText(text)

    def move_selection(self, step):
        if self.tabs.currentIndex() != 1 or not self.proxy.rowCount():
            return
        cur = self.table.currentIndex()
        row = 0 if not cur.isValid() else min(max(cur.row() + step, 0), self.proxy.rowCount() - 1)
        self.table.selectRow(row)
        self.table.scrollTo(self.proxy.index(row, 0))

    # ------------------------------------------------------------ tab 3

    def _build_ai_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        box = QGroupBox(tr("ai.group_prepare"))
        blay = QVBoxLayout(box)
        blay.addWidget(QLabel(tr("ai.prepare_help"), wordWrap=True))
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("ai.files")))
        row.addStretch(1)
        for text, state in ((tr("common.check_all"), True), (tr("common.uncheck_all"), False)):
            b = QPushButton(text)
            b.clicked.connect(lambda _, s=state: (self.check_all(self.ai_files, s), self.update_ai_count()))
            row.addWidget(b)
        blay.addLayout(row)
        self.ai_files = QListWidget()
        self.ai_files.setFlow(QListWidget.Flow.LeftToRight)
        self.ai_files.setWrapping(True)
        self.ai_files.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.ai_files.setMaximumHeight(130)
        self.ai_files.itemChanged.connect(self.update_ai_count)
        blay.addWidget(self.ai_files)

        row = QHBoxLayout()
        row.addWidget(QLabel(tr("ai.which")))
        self.ai_mode = QComboBox()
        for key in AI_MODES:
            self.ai_mode.addItem(tr(f"aimode.{key}"), key)
        self.ai_mode.currentIndexChanged.connect(self.update_ai_count)
        row.addWidget(self.ai_mode, 2)
        row.addWidget(QLabel(tr("ai.chunk")))
        self.ai_chunk = QSpinBox(minimum=20, maximum=2000, value=200, singleStep=50)
        self.ai_chunk.valueChanged.connect(self.update_ai_count)
        row.addWidget(self.ai_chunk)
        self.ai_count = QLabel()
        row.addWidget(self.ai_count, 1)
        cont = QPushButton(tr("ai.continue"))
        cont.setToolTip(tr("ai.continue_tip"))
        cont.clicked.connect(self.continue_ai)
        row.addWidget(cont)
        gen = QPushButton(tr("ai.generate"))
        gen.clicked.connect(self.generate_ai)
        row.addWidget(gen)
        blay.addLayout(row)

        blay.addWidget(QLabel(tr("ai.prompt_label")))
        self.ai_prompt = QPlainTextEdit(readOnly=True)
        self.ai_prompt.setMaximumHeight(110)
        blay.addWidget(self.ai_prompt)
        row = QHBoxLayout()
        copy_btn = QPushButton(tr("ai.copy"))
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(self.ai_prompt.toPlainText()))
        row.addWidget(copy_btn)
        self.open_task_btn = QPushButton(tr("ai.open_folder"))
        self.open_task_btn.clicked.connect(lambda: self.open_folder(self.last_task))
        self.open_task_btn.setEnabled(False)
        row.addWidget(self.open_task_btn)
        row.addStretch(1)
        blay.addLayout(row)
        lay.addWidget(box)

        box = QGroupBox(tr("api.group"))
        blay = QVBoxLayout(box)
        blay.addWidget(QLabel(tr("api.help"), wordWrap=True))
        row = QHBoxLayout()
        self.api_provider = QComboBox()
        for key, info in ai_api.PROVIDERS.items():
            self.api_provider.addItem(info["label"], key)
        api_cfg = self.cfg.get("api", {})
        self.api_provider.setCurrentIndex(max(self.api_provider.findData(api_cfg.get("provider", "anthropic")), 0))
        row.addWidget(self.api_provider, 2)
        row.addWidget(QLabel(tr("api.model")))
        self.api_model = QLineEdit(api_cfg.get("model", ""))
        row.addWidget(self.api_model, 1)
        row.addWidget(QLabel(tr("api.url")))
        self.api_url = QLineEdit(api_cfg.get("url", ""))
        self.api_url.setPlaceholderText(tr("api.url_hint"))
        row.addWidget(self.api_url, 2)
        blay.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("api.key")))
        self.api_key = QLineEdit(echoMode=QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText(tr("api.key_hint"))
        row.addWidget(self.api_key, 2)
        self.api_remember = QCheckBox(tr("api.remember"))
        self.api_remember.setToolTip(tr("api.remember_tip"))
        row.addWidget(self.api_remember)
        self.api_run_btn = QPushButton(tr("api.run"))
        self.api_run_btn.clicked.connect(self.run_api)
        row.addWidget(self.api_run_btn)
        self.api_stop_btn = QPushButton(tr("common.stop"))
        self.api_stop_btn.setEnabled(False)
        self.api_stop_btn.clicked.connect(lambda: self.api_worker and self.api_worker.cancel())
        row.addWidget(self.api_stop_btn)
        blay.addLayout(row)
        self.api_progress = QProgressBar()
        blay.addWidget(self.api_progress)
        self.api_worker = None
        self.api_provider.currentIndexChanged.connect(self.on_api_provider)
        self.on_api_provider()
        lay.addWidget(box)

        box = QGroupBox(tr("ai.group_import"))
        blay = QVBoxLayout(box)
        blay.addWidget(QLabel(tr("ai.import_help"), wordWrap=True))
        row = QHBoxLayout()
        imp = QPushButton(tr("ai.import"))
        imp.clicked.connect(self.import_ai)
        row.addWidget(imp)
        acc = QPushButton(tr("ai.accept_all"))
        acc.clicked.connect(self.accept_all_ai)
        row.addWidget(acc)
        row.addStretch(1)
        blay.addLayout(row)
        self.ai_log = QPlainTextEdit(readOnly=True)
        blay.addWidget(self.ai_log)
        lay.addWidget(box, 1)
        self.last_task = None
        return w

    def update_ai_count(self, *_):
        if not self.project:
            return
        mode = self.ai_mode.currentData()
        n = 0
        for name in self.checked(self.ai_files):
            for e in self.project.load_file(name):
                if not e["source"].strip() and not e["target"].strip():
                    continue
                if (mode == "all"
                        or (mode == "errors" and e["_errors"])
                        or (mode == "problems" and (e["_errors"] or e["_warnings"]))
                        or (mode == "flagged" and e.get("flag"))
                        or (mode == "edited" and e["target"] != e["original"])):
                    n += 1
        parts = -(-n // self.ai_chunk.value())
        self.ai_count.setText(tr("ai.count", n=n, parts=parts))

    def generate_ai(self):
        files = self.checked(self.ai_files)
        if not files:
            QMessageBox.warning(self, tr("export.no_files_title"), tr("ai.no_files"))
            return
        self.save()
        out, n = core.generate_ai_task(self.project, files, self.ai_mode.currentData(), self.ai_chunk.value())
        if n == 0:
            QMessageBox.information(self, tr("ai.no_entries_title"), tr("ai.no_entries"))
        self.last_task = out
        self.open_task_btn.setEnabled(True)
        self.ai_prompt.setPlainText(core.task_prompt(out))
        self.ai_log.appendPlainText(tr("ai.created", path=out, n=n))

    def continue_ai(self):
        tasks = core.list_ai_tasks(self.project)
        if not tasks:
            QMessageBox.information(self, tr("ai.no_tasks_title"), tr("ai.no_tasks"))
            return

        def describe(t):
            report = tr("ai.task_has_report") if t["has_report"] else ""
            return tr("ai.task_desc", name=t["path"].name, done=t["done_parts"], parts=t["parts"],
                      fixes=t["fixes"]) + report

        task = tasks[0]
        if len(tasks) > 1:
            labels = [describe(t) for t in tasks]
            choice, ok = QInputDialog.getItem(
                self, tr("ai.continue"), tr("ai.choose_task"),
                labels, 0, False)
            if not ok:
                return
            task = tasks[labels.index(choice)]

        self.last_task = task["path"]
        self.open_task_btn.setEnabled(True)
        self.ai_prompt.setPlainText(core.continue_prompt(task["path"]))
        self.ai_log.appendPlainText(tr("ai.continuing", task=describe(task)))

    def import_ai(self):
        start = str(self.last_task or core.AI_TASKS_DIR)
        path = QFileDialog.getExistingDirectory(self, tr("ai.folder_title"), start)
        if not path:
            return
        if not core.task_layout(path):
            QMessageBox.warning(self, tr("ai.not_task_title"), tr("ai.not_task"))
            return
        try:
            applied, problems = core.import_ai_results(self.project, path)
        except Exception as e:
            QMessageBox.critical(self, tr("ai.import_error_title"), str(e))
            return
        self.ai_log.appendPlainText(tr("ai.loaded", n=applied, path=path))
        for p in problems[:50]:
            self.ai_log.appendPlainText("  ! " + p)
        self.update_all_file_items()
        if applied:
            self.filter_combo.setCurrentIndex(FILTERS.index("ai"))
            with_ai = [i for i, name in enumerate(self.project.files) if self.project.file_stats(name)["ai"]]
            if with_ai and with_ai[0] != self.file_list.currentRow():
                self.file_list.setCurrentRow(with_ai[0])
            else:
                self.select_file(self.file_list.currentItem())
            self.tabs.setCurrentIndex(1)
        else:
            self.select_file(self.file_list.currentItem())

    def accept_all_ai(self):
        charset = self.project.charset()
        accepted = skipped = 0
        for name in self.project.files:
            changed = False
            for e in self.project.load_file(name):
                if e.get("ai") is None:
                    continue
                if e["_ai_errors"] or core.odd_chars(e["ai"], charset):
                    skipped += 1
                    continue
                core.accept_ai(e)
                accepted += 1
                changed = True
            if changed:
                self.project.dirty.add(name)
        self.save()
        self.select_file(self.file_list.currentItem())
        self.ai_log.appendPlainText(tr("ai.accepted", accepted=accepted, skipped=skipped))

    @staticmethod
    def open_folder(path):
        if path:
            subprocess.Popen(["xdg-open", str(path)])

    # ------------------------------------------------------------ tab 4

    def _build_install_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel(tr("install.help", folder=core.BACKUPS_DIR.name), wordWrap=True))
        row = QHBoxLayout()
        for text, slot in ((tr("install.check"), self.check_pending),
                           (tr("install.install"), self.install),
                           (tr("install.restore"), self.restore),
                           (tr("install.open_backups"), lambda: self.open_folder(core.BACKUPS_DIR))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        box = QGroupBox(tr("patch.group"))
        blay = QVBoxLayout(box)
        blay.addWidget(QLabel(tr("patch.help"), wordWrap=True))
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("patch.author")))
        self.patch_author = QLineEdit(self.cfg.get("patch_author", ""))
        row.addWidget(self.patch_author, 1)
        self.patch_reviewed = QCheckBox(tr("patch.only_reviewed"))
        row.addWidget(self.patch_reviewed)
        for text, slot in ((tr("patch.export"), self.export_patch),
                           (tr("patch.apply"), self.apply_patch),
                           (tr("patch.import"), self.import_patch)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        blay.addLayout(row)
        lay.addWidget(box)

        self.install_log = QPlainTextEdit(readOnly=True)
        lay.addWidget(self.install_log, 1)
        return w

    # ------------------------------------------------------------ patches

    def export_patch(self):
        self.save()
        author = self.patch_author.text().strip()
        self.cfg["patch_author"] = author
        core.save_config(self.cfg)
        p = self.project
        default = str(core.BASE_DIR / f"crimson-desert-{p.src_lang}-{p.tgt_lang}{patch.SUFFIX}")
        path, _ = QFileDialog.getSaveFileName(self, tr("patch.export"), default, f"*{patch.SUFFIX}")
        if path:
            n = patch.export_patch(p, path, author, self.patch_reviewed.isChecked())
            self.log(tr("patch.exported", n=n, path=path))

    def apply_patch(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("patch.apply"), str(core.BASE_DIR), f"*{patch.SUFFIX} *.jsonl")
        if not path:
            return
        game = self.project.game_dir if self.project else self.game_edit.text().strip()
        if QMessageBox.question(self, tr("install.confirm_title"), tr("patch.confirm", path=Path(path).name)) \
                != QMessageBox.StandardButton.Yes:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            n = patch.apply_patch(game, path, self.log)
            self.log(tr("install.done", n=n))
        except Exception as e:
            self.log(tr("install.error", error=e))
            QMessageBox.critical(self, tr("install.error_title"), str(e))
        finally:
            QApplication.restoreOverrideCursor()

    def import_patch(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("patch.import"), str(core.BASE_DIR), f"*{patch.SUFFIX} *.jsonl")
        if not path:
            return
        self.save()
        try:
            applied, skipped = patch.import_into_project(self.project, path)
        except Exception as e:
            QMessageBox.critical(self, tr("ai.import_error_title"), str(e))
            return
        self.log(tr("patch.imported", n=applied, skipped=skipped, path=path))
        self.update_all_file_items()
        if applied:
            self.filter_combo.setCurrentIndex(FILTERS.index("ai"))
            self.select_file(self.file_list.currentItem())
            self.tabs.setCurrentIndex(1)

    # ------------------------------------------------------------ AI through an API

    def on_api_provider(self):
        provider = self.api_provider.currentData()
        self.api_model.setPlaceholderText(ai_api.model_default(provider) or tr("api.model_hint"))
        saved = self.cfg.get("api_keys", {}).get(provider, "")
        self.api_key.setText(saved)
        self.api_remember.setChecked(bool(saved))

    def run_api(self):
        task = self.last_task
        if not task:
            tasks = core.list_ai_tasks(self.project)
            task = tasks[0]["path"] if tasks else None
        if not task:
            QMessageBox.information(self, tr("ai.no_tasks_title"), tr("ai.no_tasks"))
            return
        provider = self.api_provider.currentData()
        model = self.api_model.text().strip() or ai_api.model_default(provider)
        url = self.api_url.text().strip() or None
        key = self.api_key.text().strip() or ai_api.api_key_for(provider, self.cfg)
        if not model or (not key and not ai_api.is_local(url)):
            QMessageBox.warning(self, tr("api.group"), tr("api.missing"))
            return
        self.cfg["api"] = {"provider": provider, "model": self.api_model.text().strip(),
                           "url": self.api_url.text().strip()}
        keys = self.cfg.setdefault("api_keys", {})
        if self.api_remember.isChecked() and self.api_key.text().strip():
            keys[provider] = self.api_key.text().strip()
        else:
            keys.pop(provider, None)
        core.save_config(self.cfg)
        if QMessageBox.question(self, tr("api.group"), tr("api.confirm", task=Path(task).name, model=model)) \
                != QMessageBox.StandardButton.Yes:
            return
        self.ai_log.appendPlainText(tr("api.started", task=task, model=model))

        def progress(i, n, label):
            self.api_progress.setMaximum(max(n, 1))
            self.api_progress.setValue(i)
            self.api_progress.setFormat(f"{label}  %v / %m")

        def job(progress_cb, stop):
            return ai_api.run_task(task, provider, model, key, url, progress_cb, stop,
                                   log=lambda text: progress_cb(-1, -1, text))

        def on_progress(i, n, label):
            if i < 0:
                self.ai_log.appendPlainText(label)
            else:
                progress(i, n, label)

        def done(result):
            self.api_worker = None
            self.api_run_btn.setEnabled(True)
            self.api_stop_btn.setEnabled(False)
            parts, fixes, rejected = result
            self.ai_log.appendPlainText(tr("api.finished", parts=parts, n=fixes, bad=rejected))
            self.last_task = Path(task)

        def failed(msg):
            self.api_worker = None
            self.api_run_btn.setEnabled(True)
            self.api_stop_btn.setEnabled(False)
            self.ai_log.appendPlainText(tr("install.error", error=msg))

        self.api_run_btn.setEnabled(False)
        self.api_stop_btn.setEnabled(True)
        self.api_worker = start_worker(self, job, done, failed, on_progress)

    def log(self, text):
        self.install_log.appendPlainText(text)
        QApplication.processEvents()

    def check_pending(self):
        self.save()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            changes = core.pending_changes(self.project)
        finally:
            QApplication.restoreOverrideCursor()
        if not changes:
            self.log(tr("install.no_changes"))
            return []
        names = [c[0] for c in changes]
        self.log(tr("install.pending", n=len(names), names=", ".join(names)))
        return names

    def install(self):
        names = self.check_pending()
        if not names:
            return
        folder = self.project.tgt_folder
        if QMessageBox.question(
                self, tr("install.confirm_title"),
                tr("install.confirm", folder=folder, n=len(names))) != QMessageBox.StandardButton.Yes:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            n = core.install(self.project, self.log)
            self.log(tr("install.done", n=n))
        except Exception as e:
            self.log(tr("install.error", error=e))
            QMessageBox.critical(self, tr("install.error_title"),
                                 tr("install.error_backups", error=e, folder=core.BACKUPS_DIR.name))
        finally:
            QApplication.restoreOverrideCursor()

    def restore(self):
        if QMessageBox.question(
                self, tr("restore.title"),
                tr("restore.confirm", folder=self.project.tgt_folder)) != QMessageBox.StandardButton.Yes:
            return
        try:
            core.restore_original(self.project, self.log)
            self.log(tr("restore.done"))
        except Exception as e:
            self.log(tr("install.error", error=e))


def main():
    app = QApplication(sys.argv)
    lang = core.load_config().get("ui_language") or QLocale.system().name().split("_")[0]
    i18n.set_language(lang)
    app.setApplicationName(tr("app.title"))
    win = MainWindow()
    win.show()
    WINDOWS.append(win)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
