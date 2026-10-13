"""Tab 5 – voice-over and quality tools: voice index, speech recognition, name consistency, game updates."""

import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

import cd_audio as audio
import cd_core as core
import cd_stt as stt
from cd_ui_common import start_worker
from i18n import tr

STT_SCOPES = ["missing_file", "missing_edited", "missing_all", "file"]


class VoiceTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.worker = None
        self.report = []
        lay = QVBoxLayout(self)

        # --- voice-over
        box = QGroupBox(tr("voice.group"))
        blay = QVBoxLayout(box)
        blay.addWidget(QLabel(tr("voice.help"), wordWrap=True))
        row = QHBoxLayout()
        self.index_label = QLabel()
        row.addWidget(self.index_label, 1)
        self.vgm_label = QLabel()
        row.addWidget(self.vgm_label)
        self.download_btn = QPushButton(tr("voice.download_vgmstream"))
        self.download_btn.clicked.connect(self.download_vgmstream)
        row.addWidget(self.download_btn)
        b = QPushButton(tr("voice.choose_vgmstream"))
        b.clicked.connect(self.choose_vgmstream)
        row.addWidget(b)
        self.index_btn = QPushButton(tr("voice.build_index"))
        self.index_btn.clicked.connect(self.build_index)
        row.addWidget(self.index_btn)
        blay.addLayout(row)
        lay.addWidget(box)

        # --- speech recognition
        box = QGroupBox(tr("stt.group"))
        blay = QVBoxLayout(box)
        blay.addWidget(QLabel(tr("stt.help"), wordWrap=True))
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("stt.model")))
        self.model_combo = QComboBox()
        self.model_combo.addItems(stt.MODELS)
        self.model_combo.setCurrentText(self.win.cfg.get("stt_model", stt.DEFAULT_MODEL))
        row.addWidget(self.model_combo)
        row.addWidget(QLabel(tr("stt.scope")))
        self.scope_combo = QComboBox()
        for key in STT_SCOPES:
            self.scope_combo.addItem(tr(f"stt.scope.{key}"), key)
        row.addWidget(self.scope_combo, 1)
        self.stt_btn = QPushButton(tr("stt.start"))
        self.stt_btn.clicked.connect(self.start_stt)
        row.addWidget(self.stt_btn)
        self.stop_btn = QPushButton(tr("common.stop"))
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop)
        row.addWidget(self.stop_btn)
        blay.addLayout(row)
        self.stt_label = QLabel(wordWrap=True)
        blay.addWidget(self.stt_label)
        lay.addWidget(box)

        # --- names
        box = QGroupBox(tr("terms.group"))
        blay = QVBoxLayout(box)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("terms.help"), wordWrap=True), 1)
        self.terms_btn = QPushButton(tr("terms.run"))
        self.terms_btn.clicked.connect(self.run_terms)
        row.addWidget(self.terms_btn)
        blay.addLayout(row)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.terms_table = QTableWidget(0, 4)
        self.terms_table.setHorizontalHeaderLabels(
            [tr("terms.col_name"), tr("terms.col_expected"), tr("terms.col_variants"), tr("terms.col_entries")])
        self.terms_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.terms_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.terms_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.terms_table.verticalHeader().setVisible(False)
        self.terms_table.currentCellChanged.connect(self.show_term_entries)
        split.addWidget(self.terms_table)
        self.terms_entries = QListWidget()
        self.terms_entries.itemDoubleClicked.connect(self.goto_term_entry)
        split.addWidget(self.terms_entries)
        split.setSizes([700, 400])
        blay.addWidget(split, 1)
        lay.addWidget(box, 1)

        # --- game update
        box = QGroupBox(tr("update.group"))
        row = QHBoxLayout(box)
        self.update_label = QLabel(wordWrap=True)
        row.addWidget(self.update_label, 1)
        self.refresh_btn = QPushButton(tr("update.refresh"))
        self.refresh_btn.clicked.connect(self.refresh_from_game)
        row.addWidget(self.refresh_btn)
        lay.addWidget(box)

        self.progress = QProgressBar()
        lay.addWidget(self.progress)

    # ------------------------------------------------------------ state

    def refresh(self):
        p = self.win.project
        lib = p.voice if p else None
        if lib:
            self.index_label.setText(tr("voice.index_ready", n=lib.count(), lang=core.lang_name(lib.index["lang"]),
                                        folder=lib.index["folder"]))
        else:
            self.index_label.setText(tr("voice.index_missing"))
        tool = audio.find_vgmstream()
        self.vgm_label.setText(tr("voice.vgmstream_ok") if tool else tr("voice.vgmstream_missing"))
        self.vgm_label.setToolTip(tool or "")
        self.download_btn.setVisible(not tool)
        if not stt.available():
            self.stt_label.setText(tr("stt.missing"))
        elif p:
            self.stt_label.setText(tr("stt.status", n=len(p.transcripts),
                                      bad=sum(1 for r in p.transcripts.values()
                                              if r.get("ratio", 1) < core.VOICE_MISMATCH_RATIO)))
        if p:
            self.update_label.setText(tr("update.changed") if core.game_updated(p) else tr("update.same"))
        busy = self.worker is not None
        for w in (self.index_btn, self.stt_btn, self.terms_btn, self.refresh_btn):
            w.setEnabled(not busy and p is not None)
        self.stt_btn.setEnabled(not busy and p is not None and lib is not None and stt.available())
        self.stop_btn.setEnabled(busy)

    def on_progress(self, i, n, label):
        self.progress.setMaximum(max(n, 1))
        self.progress.setValue(i)
        self.progress.setFormat(f"{label}  %v / %m" if label else "%p%")

    def finish(self):
        self.worker = None
        self.progress.setValue(self.progress.maximum())
        self.refresh()

    def on_error(self, msg):
        self.finish()
        QMessageBox.critical(self, tr("install.error_title"), msg)

    def stop(self):
        if self.worker:
            self.worker.cancel()
            self.stop_btn.setEnabled(False)

    # ------------------------------------------------------------ voice index

    def choose_vgmstream(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("voice.choose_vgmstream"))
        if path:
            self.win.cfg["vgmstream"] = path
            core.save_config(self.win.cfg)
            self.refresh()

    def download_vgmstream(self):
        if QMessageBox.question(self, tr("voice.group"), tr("voice.download_confirm")) != QMessageBox.StandardButton.Yes:
            return

        def done(path):
            self.finish()
            QMessageBox.information(self, tr("voice.group"), tr("voice.downloaded", path=path))

        self.worker = start_worker(self, lambda _p, _s: audio.download_vgmstream(), done, self.on_error)
        self.refresh()

    def build_index(self):
        p = self.win.project
        lang = p.src_lang if audio.voice_folder(p.game_dir, p.src_lang) else "en"

        def job(progress, _stop):
            return audio.build_index(p, lang, progress)

        def done(index):
            self.finish()
            self.win.attach_voice(audio.VoiceLibrary(p, index))
            self.refresh()

        self.worker = start_worker(self, job, done, self.on_error, self.on_progress)
        self.refresh()

    # ------------------------------------------------------------ speech recognition

    def start_stt(self):
        p, lib = self.win.project, self.win.project.voice
        scope = self.scope_combo.currentData()
        files = [self.win.current_file] if scope in ("file", "missing_file") else list(lib.index["lines"])
        items = []
        for name in files:
            if name not in p.files:
                continue
            for e in p.load_file(name):
                if not lib.lines(name, e["key"]):
                    continue
                if scope.startswith("missing") and f"{name}|{e['key']}" in p.transcripts:
                    continue
                if scope == "missing_edited" and e["target"] == e["original"]:
                    continue
                items.append((name, e))
        if not items:
            QMessageBox.information(self, tr("ai.no_entries_title"), tr("ai.no_entries"))
            return
        model = self.model_combo.currentText()
        self.win.cfg["stt_model"] = model
        core.save_config(self.win.cfg)
        started = time.time()

        def progress_with_eta(i, n, label):
            if i:
                left = (time.time() - started) / i * (n - i)
                label = tr("stt.eta", label=label, minutes=round(left / 60))
            self.on_progress(i, n, label)

        def job(progress, stop):
            progress(0, len(items), tr("stt.loading_model"))
            return stt.transcribe_entries(p, lib, items, model, progress, stop)

        def done(n):
            self.finish()
            self.win.attach_voice(lib)
            self.stt_label.setText(self.stt_label.text() + "  " + tr("stt.done", n=n))

        self.worker = start_worker(self, job, done, self.on_error, progress_with_eta)
        self.refresh()

    # ------------------------------------------------------------ names

    def run_terms(self):
        p = self.win.project

        def job(progress, _stop):
            return core.terminology_report(p, progress=progress)

        def done(report):
            self.finish()
            self.report = report
            self.terms_table.setRowCount(len(report))
            for i, r in enumerate(report):
                for col, val in enumerate((r["name"], r["expected"], " | ".join(r["variants"]), str(len(r["entries"])))):
                    self.terms_table.setItem(i, col, QTableWidgetItem(val))
            self.terms_table.resizeColumnToContents(0)
            self.terms_table.resizeColumnToContents(1)
            self.progress.setFormat(tr("terms.found", n=len(report)))

        self.worker = start_worker(self, job, done, self.on_error, self.on_progress)
        self.refresh()

    def show_term_entries(self, row, *_):
        self.terms_entries.clear()
        if 0 <= row < len(self.report):
            p = self.win.project
            for name, key in self.report[row]["entries"]:
                e = next((x for x in p.load_file(name) if x["key"] == key), None)
                text = core.visible_text(e["target"])[:120] if e else ""
                item = QListWidgetItem(f"{name}|{key}\n   {text}")
                item.setData(Qt.ItemDataRole.UserRole, (name, key))
                self.terms_entries.addItem(item)

    def goto_term_entry(self, item):
        name, key = item.data(Qt.ItemDataRole.UserRole)
        self.win.goto_entry(name, key)

    # ------------------------------------------------------------ game update

    def refresh_from_game(self):
        self.win.refresh_from_game()
