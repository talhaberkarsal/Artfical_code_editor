#!/usr/bin/env python3
"""
Artfical Code Editor v3.1 — Full Edition
Features:
 - Explorer (defaults to Desktop)
 - Large code editor with autosave
 - Output and interactive Terminal tabs
 - Run saved Python files in an embedded process
 - Auto-installer: scans imports and installs missing packages every 10 seconds
 - Outline panel showing imports / prints / defs / classes
 - Theme (Dark / Light)
 - Updater: checks remote version on startup and via "Check Update" button;
   if remote version > CURRENT_VERSION, offers to open GitHub page
Requirements:
    python -m pip install PySide6 requests
Run:
    python Artfical_v3_1_full.py
"""
import sys
import os
import ast
import subprocess
import traceback
import webbrowser
from pathlib import Path
from typing import List, Set, Optional

import importlib.util
import requests

from PySide6.QtCore import Qt, QTimer, QThread, Signal, QProcess, QDir
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QMessageBox, QPlainTextEdit,
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSplitter,
    QTabWidget, QLineEdit, QStatusBar, QComboBox, QToolBar, QTreeView,
    QFileSystemModel, QListWidget
)

# ------------------------
APP_NAME = "Artfical v3.1 Full"
CURRENT_VERSION = "3.1"
UPDATE_URL = "https://test123no.great-site.net/1.txt?i=1"
GITHUB_URL = "https://github.com/ArtficalTeam/Artfical-Code-Editor"

# ------------------------
# Helpers
# ------------------------
def get_desktop_path() -> str:
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, "Desktop"),
        os.path.join(home, "Masaüstü"),  # Turkish Windows localization
        os.path.join(home, "Escritorio"),
        os.path.join(home, "Bureau"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return home

def version_tuple(v: str):
    try:
        parts = [int(p) for p in v.strip().split(".") if p != ""]
        return tuple(parts)
    except Exception:
        return (v.strip(),)

def is_remote_version_newer(remote_v: str, current_v: str) -> bool:
    rv = version_tuple(remote_v)
    cv = version_tuple(current_v)
    try:
        return rv > cv
    except Exception:
        return str(remote_v).strip() > str(current_v).strip()

# ------------------------
# Updater thread (non-blocking)
# ------------------------
class UpdaterThread(QThread):
    found = Signal(str)   # latest version
    error = Signal(str)
    no_update = Signal()

    def __init__(self, url: str, current_version: str, parent=None):
        super().__init__(parent)
        self.url = url
        self.current_version = current_version

    def run(self):
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache"
            }
            resp = requests.get(self.url, headers=headers, timeout=8)
            
            if resp.status_code == 200:
                latest = resp.text.strip()
                if latest and is_remote_version_newer(latest, self.current_version):
                    self.found.emit(latest)
                else:
                    self.no_update.emit()
            else:
                self.error.emit(f"Server responded with HTTP {resp.status_code}")
        
        except requests.exceptions.ConnectionError:
            self.error.emit("Connection error — check your internet or site availability.")
        except requests.exceptions.Timeout:
            self.error.emit("Connection timeout — server took too long to respond.")
        except Exception as e:
            self.error.emit(f"Unexpected error: {e}")

# ------------------------
# AST-based import scanner & outline
# ------------------------
def scan_imports_ast(code: str) -> Set[str]:
    pkgs = set()
    try:
        tree = ast.parse(code)
    except Exception:
        return pkgs
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                pkgs.add(n.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                pkgs.add(node.module.split('.')[0])
    return pkgs

def outline_from_python(code: str) -> dict:
    out = {"imports": [], "prints": [], "defs": [], "classes": []}
    try:
        tree = ast.parse(code)
    except Exception:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                out["imports"].append((n.name, getattr(node, "lineno", 0)))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            out["imports"].append((f"from {mod} import ...", getattr(node, "lineno", 0)))
        elif isinstance(node, ast.FunctionDef):
            out["defs"].append((node.name, getattr(node, "lineno", 0)))
        elif isinstance(node, ast.ClassDef):
            out["classes"].append((node.name, getattr(node, "lineno", 0)))
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "print":
                snippet = "print(...)"
                try:
                    snippet = ast.get_source_segment(code, node) or snippet
                except Exception:
                    pass
                out["prints"].append((snippet, getattr(node, "lineno", 0)))
    for k in out:
        out[k].sort(key=lambda x: x[1])
    return out

def is_package_installed(pkg: str) -> bool:
    try:
        return importlib.util.find_spec(pkg) is not None
    except Exception:
        return False

# ------------------------
# Installer thread (background pip installer)
# ------------------------
class InstallerThread(QThread):
    log = Signal(str)
    finished = Signal(list, list)  # succeeded, failed

    def __init__(self, packages: List[str], python_exe: str = sys.executable, parent=None):
        super().__init__(parent)
        self.packages = packages
        self.python_exe = python_exe

    def run(self):
        succeeded = []
        failed = []
        for pkg in self.packages:
            self.log.emit(f"\n--- Installing {pkg} ---")
            try:
                proc = subprocess.run(
                    [self.python_exe, "-m", "pip", "install", pkg],
                    capture_output=True, text=True
                )
                out = proc.stdout.strip()
                err = proc.stderr.strip()
                if out:
                    self.log.emit(out)
                if proc.returncode == 0:
                    succeeded.append(pkg)
                else:
                    if err:
                        self.log.emit(err)
                    failed.append(pkg)
            except Exception as e:
                tb = traceback.format_exc()
                self.log.emit(f"Exception while installing {pkg}: {e}\n{tb}")
                failed.append(pkg)
        self.finished.emit(succeeded, failed)

# ------------------------
# Editor widget
# ------------------------
class CodeEditor(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.file_path: Optional[Path] = None
        self._saved_text = ""
        self.setFont(QFont("Consolas", 12))
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(3000)  # default 3 seconds
        self.autosave_timer.timeout.connect(self.autosave)
        self.autosave_timer.start()

    def is_modified(self) -> bool:
        return self.toPlainText() != self._saved_text

    def autosave(self):
        if self.file_path and self.is_modified():
            try:
                with open(self.file_path, "w", encoding="utf-8") as f:
                    f.write(self.toPlainText())
                self._saved_text = self.toPlainText()
            except Exception as e:
                print("Autosave failed:", e)

    def load_from_path(self, path: Path):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            txt = f.read()
        self.setPlainText(txt)
        self.file_path = path
        self._saved_text = txt

    def save(self, path: Optional[Path] = None) -> bool:
        if path:
            self.file_path = path
        if not self.file_path:
            return False
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                f.write(self.toPlainText())
            self._saved_text = self.toPlainText()
            return True
        except Exception as e:
            QMessageBox.critical(self, "Save error", f"Failed to save file: {e}")
            return False

# ------------------------
# Main Window
# ------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1200, 820)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # Toolbar
        toolbar = QToolBar()
        self.addToolBar(toolbar)
        self.open_folder_act = QPushButton("Open Folder")
        self.open_file_act = QPushButton("Open File")
        self.save_act = QPushButton("Save")
        self.run_act = QPushButton("Run")
        self.install_act = QPushButton("Install Missing")
        self.check_update_act = QPushButton("Check Update")
        toolbar.addWidget(self.open_folder_act)
        toolbar.addWidget(self.open_file_act)
        toolbar.addWidget(self.save_act)
        toolbar.addWidget(self.run_act)
        toolbar.addWidget(self.install_act)
        toolbar.addWidget(self.check_update_act)

        # Top controls
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Auto-save (s):"))
        self.autosave_input = QLineEdit("3")
        self.autosave_input.setFixedWidth(60)
        top_row.addWidget(self.autosave_input)
        top_row.addStretch()
        top_row.addWidget(QLabel("Theme:"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Dark", "Light"])
        top_row.addWidget(self.theme_combo)
        main_layout.addLayout(top_row)

        # Splitter: explorer | editor+bottom | outline
        hsplit = QSplitter(Qt.Horizontal)

        # Explorer
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.addWidget(QLabel("Explorer"))
        self.fs_model = QFileSystemModel()
        self.fs_model.setFilter(QDir.AllDirs | QDir.Files | QDir.NoDotAndDotDot)
        self.tree = QTreeView()
        self.tree.setModel(self.fs_model)
        self.tree.setHeaderHidden(True)
        left_layout.addWidget(self.tree)
        hsplit.addWidget(left_widget)

        # Center: editor + bottom
        center_split = QSplitter(Qt.Vertical)
        editor_container = QWidget()
        editor_layout = QVBoxLayout(editor_container)
        self.editor = CodeEditor()
        editor_layout.addWidget(self.editor)
        center_split.addWidget(editor_container)

        # Bottom tabs
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)
        self.bottom_tabs = QTabWidget()
        self.output_area = QPlainTextEdit()
        self.output_area.setReadOnly(True)
        self.output_area.setFont(QFont("Consolas", 11))
        # Terminal
        term_widget = QWidget()
        term_layout = QVBoxLayout(term_widget)
        self.term_output = QPlainTextEdit()
        self.term_output.setReadOnly(True)
        self.term_output.setFont(QFont("Consolas", 11))
        self.term_input = QLineEdit()
        self.term_input.setPlaceholderText("Type command and press Enter (e.g. pip install requests)")
        term_layout.addWidget(self.term_output)
        term_layout.addWidget(self.term_input)

        self.bottom_tabs.addTab(self.output_area, "Output")
        self.bottom_tabs.addTab(term_widget, "Terminal")
        bottom_layout.addWidget(self.bottom_tabs)
        center_split.addWidget(bottom_widget)

        hsplit.addWidget(center_split)

        # Outline (right)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.addWidget(QLabel("Outline (imports / prints / def / class)"))
        self.outline = QListWidget()
        right_layout.addWidget(self.outline)
        hsplit.addWidget(right_widget)

        hsplit.setStretchFactor(0, 1)
        hsplit.setStretchFactor(1, 3)
        hsplit.setStretchFactor(2, 1)

        main_layout.addWidget(hsplit)

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)

        # Connect signals
        self.open_folder_act.clicked.connect(self.open_folder)
        self.open_file_act.clicked.connect(self.open_file_dialog)
        self.save_act.clicked.connect(self.save_file)
        self.run_act.clicked.connect(self.run_current_file)
        self.install_act.clicked.connect(self.install_missing_current)
        self.check_update_act.clicked.connect(self.on_check_update_clicked)
        self.theme_combo.currentIndexChanged.connect(self.apply_theme)
        self.term_input.returnPressed.connect(self.on_term_command)
        self.editor.textChanged.connect(self.on_editor_changed)
        self.outline.itemClicked.connect(self.on_outline_clicked)
        self.autosave_input.editingFinished.connect(self.on_autosave_changed)
        self.tree.doubleClicked.connect(self.on_tree_double_clicked)

        # State
        self.current_folder = None
        self.installer: Optional[InstallerThread] = None
        self.proc_run: Optional[QProcess] = None
        self.proc_term: Optional[QProcess] = None

        # Open Desktop
        desktop = get_desktop_path()
        try:
            self.fs_model.setRootPath(desktop)
            self.tree.setRootIndex(self.fs_model.index(desktop))
            self.current_folder = desktop
            self.status.showMessage(f"Explorer: {desktop}", 4000)
        except Exception:
            pass

        self.editor.setMinimumHeight(420)

        # Theme & autosave config
        self.current_theme = "Dark"
        self.theme_combo.setCurrentText(self.current_theme)
        self.apply_theme()
        self.on_autosave_changed()

        # Auto-installer timer every 10s
        self.auto_installer_timer = QTimer(self)
        self.auto_installer_timer.setInterval(10_000)
        self.auto_installer_timer.timeout.connect(self._auto_installer_tick)
        self.auto_installer_timer.start()

        # Updater thread started at startup (non-blocking)
        QTimer.singleShot(500, self.start_initial_update_check)

    # ------------------------
    # Explorer & file handling
    # ------------------------
    def open_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Open Folder', os.path.expanduser('~'))
        if not folder:
            return
        self.current_folder = folder
        self.fs_model.setRootPath(folder)
        self.tree.setRootIndex(self.fs_model.index(folder))
        self.status.showMessage(f"Opened folder: {folder}", 4000)

    def on_tree_double_clicked(self, index):
        path = self.fs_model.filePath(index)
        if os.path.isdir(path):
            return
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read()
        except Exception:
            QMessageBox.information(self, 'Info', f"Can't open file as text: {path}")
            return
        self.open_path_in_editor(Path(path), text)

    def open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Open File', os.path.expanduser('~'), 'All Files (*)')
        if path:
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    text = f.read()
            except Exception:
                QMessageBox.information(self, 'Info', f"Can't open file as text: {path}")
                return
            self.open_path_in_editor(Path(path), text)

    def open_path_in_editor(self, path: Path, text: str):
        self.editor.setPlainText(text)
        self.editor.file_path = path
        self.editor._saved_text = text
        self.status.showMessage(f"Opened: {path}", 3000)
        self.update_outline_for_current()

    # ------------------------
    # Outline
    # ------------------------
    def on_editor_changed(self):
        self.update_outline_for_current()

    def update_outline_for_current(self):
        code = self.editor.toPlainText()
        outline = outline_from_python(code)
        self.outline.clear()
        if outline["imports"]:
            self.outline.addItem("== Imports ==")
            for name, ln in outline["imports"]:
                self.outline.addItem(f"{ln}: {name}")
        if outline["prints"]:
            self.outline.addItem("== Prints ==")
            for src, ln in outline["prints"]:
                preview = (src.strip().replace("\n", " "))[:120]
                self.outline.addItem(f"{ln}: {preview}")
        if outline["defs"]:
            self.outline.addItem("== Functions ==")
            for name, ln in outline["defs"]:
                self.outline.addItem(f"{ln}: def {name}()")
        if outline["classes"]:
            self.outline.addItem("== Classes ==")
            for name, ln in outline["classes"]:
                self.outline.addItem(f"{ln}: class {name}")

    def on_outline_clicked(self, item):
        text = item.text()
        if ":" not in text:
            return
        try:
            ln = int(text.split(":")[0])
        except Exception:
            return
        block = self.editor.document().findBlockByLineNumber(max(0, ln - 1))
        cursor = self.editor.textCursor()
        cursor.setPosition(block.position())
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()

    # ------------------------
    # Save / Run / Install
    # ------------------------
    def save_file(self):
        tab = self.editor
        if not tab.file_path:
            path, _ = QFileDialog.getSaveFileName(self, 'Save file', os.path.expanduser('~'), 'Python Files (*.py);;All Files (*)')
            if not path:
                return False
            ok = tab.save(Path(path))
            if ok:
                self.status.showMessage(f"Saved {path}", 3000)
                return True
            else:
                return False
        else:
            ok = tab.save()
            if ok:
                self.status.showMessage(f"Saved {tab.file_path}", 3000)
                return True
            return False

    def run_current_file(self):
        tab = self.editor
        if not tab.file_path:
            reply = QMessageBox.question(self, 'Save required', 'File must be saved before running. Save now?')
            if reply == QMessageBox.StandardButton.Yes:
                if not self.save_file():
                    return
            else:
                return
        if not tab.file_path:
            return

        tab.save()
        path = str(tab.file_path)
        self.output_area.appendPlainText(f"\nRunning {path}...\n")

        # kill prior
        if self.proc_run and self.proc_run.state() != QProcess.NotRunning:
            try:
                self.proc_run.kill()
            except Exception:
                pass

        self.proc_run = QProcess(self)
        self.proc_run.setProgram(sys.executable)
        self.proc_run.setArguments([path])
        self.proc_run.setProcessChannelMode(QProcess.MergedChannels)
        self.proc_run.readyReadStandardOutput.connect(self._on_run_ready)
        self.proc_run.finished.connect(lambda code, status: self.output_area.appendPlainText(f"\nProcess exited with {code}\n"))
        self.proc_run.start()
        self.bottom_tabs.setCurrentWidget(self.output_area)

    def _on_run_ready(self):
        if not self.proc_run:
            return
        data = self.proc_run.readAllStandardOutput().data().decode('utf-8', errors='replace')
        if data:
            self.output_area.appendPlainText(data)

    def install_missing_current(self):
        code = self.editor.toPlainText()
        pkgs = sorted(scan_imports_ast(code))
        missing = [p for p in pkgs if not is_package_installed(p)]
        if not missing:
            QMessageBox.information(self, 'Info', 'No missing packages detected.')
            return
        self.output_area.appendPlainText(f"\nInstalling missing: {', '.join(missing)}\n")
        self.installer = InstallerThread(missing)
        self.installer.log.connect(self.output_area.appendPlainText)
        self.installer.finished.connect(self.on_install_finished)
        self.installer.start()

    def on_install_finished(self, ok, failed):
        if ok:
            self.output_area.appendPlainText("\nInstalled: " + ", ".join(ok) + "\n")
        if failed:
            self.output_area.appendPlainText("\nFailed: " + ", ".join(failed) + "\n")

    # ------------------------
    # Terminal
    # ------------------------
    def on_term_command(self):
        cmd = self.term_input.text().strip()
        if not cmd:
            return
        self.term_output.appendPlainText(f"> {cmd}")
        self.term_input.clear()
        if self.proc_term and self.proc_term.state() != QProcess.NotRunning:
            try:
                self.proc_term.kill()
            except Exception:
                pass
        self.proc_term = QProcess(self)
        if os.name == "nt":
            self.proc_term.setProgram("cmd.exe")
            self.proc_term.setArguments(["/c", cmd])
        else:
            self.proc_term.setProgram("/bin/sh")
            self.proc_term.setArguments(["-c", cmd])
        self.proc_term.setProcessChannelMode(QProcess.MergedChannels)
        self.proc_term.readyReadStandardOutput.connect(self._on_term_ready)
        self.proc_term.finished.connect(lambda code, status: self.term_output.appendPlainText(f"\nProcess exited with {code}\n"))
        self.proc_term.start()
        self.bottom_tabs.setCurrentIndex(1)

    def _on_term_ready(self):
        if not self.proc_term:
            return
        data = self.proc_term.readAllStandardOutput().data().decode('utf-8', errors='replace')
        if data:
            self.term_output.appendPlainText(data)

    # ------------------------
    # Auto-installer tick
    # ------------------------
    def _auto_installer_tick(self):
        try:
            code = self.editor.toPlainText()
            pkgs = sorted(scan_imports_ast(code))
            missing = [p for p in pkgs if not is_package_installed(p)]
            if missing:
                if self.installer and self.installer.isRunning():
                    self.output_area.appendPlainText("\nAuto-installer: installer busy, skipping this tick.\n")
                    return
                self.output_area.appendPlainText(f"\nAuto-installer: found missing packages: {', '.join(missing)}\n")
                self.installer = InstallerThread(missing)
                self.installer.log.connect(self.output_area.appendPlainText)
                self.installer.finished.connect(self.on_install_finished)
                self.installer.start()
        except Exception as e:
            self.output_area.appendPlainText(f"\nAuto-installer error: {e}\n")

    # ------------------------
    # Theme & autosave change
    # ------------------------
    def on_autosave_changed(self):
        try:
            secs = max(1, int(float(self.autosave_input.text())))
        except Exception:
            secs = 3
            self.autosave_input.setText("3")
        self.editor.autosave_timer.setInterval(secs * 1000)
        self.status.showMessage(f"Auto-save set to {secs} seconds", 3000)

    def apply_theme(self):
        theme = self.theme_combo.currentText()
        if theme == "Dark":
            qss = """
            QWidget { background-color: #1e1e1e; color: #d4d4d4; }
            QPlainTextEdit, QLineEdit, QTreeView, QListWidget { background-color: #252526; color: #d4d4d4; }
            QPushButton { background-color: #2d2d2d; color: #d4d4d4; border: 1px solid #3c3c3c; padding: 4px; }
            """
            self.setStyleSheet(qss)
        else:
            self.setStyleSheet("")

    # ------------------------
    # Update check handlers
    # ------------------------
    def start_initial_update_check(self):
        # run updater thread and show dialog if update found
        self.updater = UpdaterThread(UPDATE_URL, CURRENT_VERSION)
        self.updater.found.connect(self._on_update_found)
        self.updater.error.connect(lambda e: print(f"[Updater] error: {e}"))
        self.updater.no_update.connect(lambda: None)
        self.updater.start()

    def on_check_update_clicked(self):
        self.status.showMessage("Checking for updates...", 3000)
        self.check_update_manual()

    def check_update_manual(self):
        self.updater_manual = UpdaterThread(UPDATE_URL, CURRENT_VERSION)
        self.updater_manual.found.connect(self._on_update_found)
        self.updater_manual.error.connect(lambda e: QMessageBox.warning(self, "Update check failed", f"Error: {e}"))
        self.updater_manual.no_update.connect(lambda: QMessageBox.information(self, "Up to date", f"You are running the latest version ({CURRENT_VERSION})."))
        self.updater_manual.start()

    def _on_update_found(self, latest_version: str):
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Update available")
        dlg.setText(f"New version available: {latest_version} (current: {CURRENT_VERSION})")
        dlg.setInformativeText("Click 'Open GitHub' to visit the releases page.")
        open_btn = dlg.addButton("Open GitHub", QMessageBox.AcceptRole)
        dlg.addButton("Later", QMessageBox.RejectRole)
        dlg.exec()
        if dlg.clickedButton() == open_btn:
            webbrowser.open(GITHUB_URL)

# ------------------------
# Run app
# ------------------------
def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
