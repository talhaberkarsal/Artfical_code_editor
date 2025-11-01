#!/usr/bin/env python3
"""
Artfical v2.5
- Explorer (folder view) using QFileSystemModel
- Opens any file type in editor (text); .py files get syntax highlighting
- Auto-save per tab (configurable)
- Integrated terminal (run saved python scripts)
- AST-based outline: imports, prints, defs, classes
- Installer thread: pip install missing imports
- Theme toggle Light/Dark

Save as: artfical_v2_5.py
Requires: PySide6
Run: python artfical_v2_5.py
"""

import sys
import ast
import os
import subprocess
import traceback
from pathlib import Path
from typing import List, Set

import importlib.util
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QProcess, QDir
from PySide6.QtGui import QFont, QColor, QTextCharFormat, QSyntaxHighlighter, QAction
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QMessageBox, QPlainTextEdit,
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSplitter,
    QTabWidget, QLineEdit, QStatusBar, QComboBox, QToolBar, QTreeView,
    QFileSystemModel, QListWidget, QSizePolicy
)

APP_NAME = "Artfical v2.5"

# --------------------
# AST analysis utils
# --------------------
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
    """
    Return dict with lists: imports, prints (line numbers), defs, classes
    """
    out = {"imports": [], "prints": [], "defs": [], "classes": []}
    try:
        tree = ast.parse(code)
    except Exception:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                out["imports"].append((n.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            out["imports"].append((f"from {mod} import ...", node.lineno))
        elif isinstance(node, ast.FunctionDef):
            out["defs"].append((node.name, node.lineno))
        elif isinstance(node, ast.ClassDef):
            out["classes"].append((node.name, node.lineno))
        elif isinstance(node, ast.Call):
            # detect print calls (simple)
            func = node.func
            if isinstance(func, ast.Name) and func.id == "print":
                out["prints"].append((ast.get_source_segment(code, node) or "print(...)", node.lineno))
    # sort by lineno
    for k in out:
        out[k].sort(key=lambda x: x[1])
    return out

def is_package_installed(pkg: str) -> bool:
    try:
        return importlib.util.find_spec(pkg) is not None
    except Exception:
        return False

# --------------------
# Installer thread
# --------------------
class InstallerThread(QThread):
    log = Signal(str)
    finished = Signal(list, list)

    def __init__(self, packages: List[str], python_exe: str = sys.executable, parent=None):
        super().__init__(parent)
        self.packages = packages
        self.python_exe = python_exe

    def run(self):
        succeeded, failed = [], []
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
                self.log.emit(f"Exception: {e}\n{traceback.format_exc()}")
                failed.append(pkg)
        self.finished.emit(succeeded, failed)

# --------------------
# Simple syntax highlighter for Python (light)
# --------------------
class PythonHighlighter(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)
        self._init_formats()
        import re
        self.string_re = re.compile(r'(\"\"\".*?\"\"\"|\'\'\'.*?\'\'\'|\".*?\"|\'.*?\')', re.DOTALL)
        self.comment_re = re.compile(r"#.*")
        self.keyword_re = [r"\b" + k + r"\b" for k in (
            'False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield'
        )]
        import re
        self.keyword_compiled = [re.compile(p) for p in self.keyword_re]
        self.number_re = re.compile(r"\b\d+(\.\d+)?\b")

    def _init_formats(self):
        from PySide6.QtGui import QTextCharFormat
        self.fmt_string = QTextCharFormat()
        self.fmt_string.setForeground(QColor(206, 145, 120))
        self.fmt_comment = QTextCharFormat()
        self.fmt_comment.setForeground(QColor(87, 166, 74))
        self.fmt_keyword = QTextCharFormat()
        self.fmt_keyword.setForeground(QColor(86, 156, 214))
        self.fmt_number = QTextCharFormat()
        self.fmt_number.setForeground(QColor(181, 206, 168))

    def highlightBlock(self, text: str):
        import re
        for m in self.string_re.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self.fmt_string)
        m = self.comment_re.search(text)
        if m:
            self.setFormat(m.start(), m.end() - m.start(), self.fmt_comment)
        for r in self.keyword_compiled:
            for m in r.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), self.fmt_keyword)
        for m in self.number_re.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self.fmt_number)

# --------------------
# Editor tab
# --------------------
class EditorTab(QWidget):
    def __init__(self, path: Path = None, text: str = "", autosave_interval: int = 3000):
        super().__init__()
        self.path = path
        self._saved_text = text
        self.autosave_interval = autosave_interval

        layout = QVBoxLayout(self)
        self.editor = QPlainTextEdit()
        self.editor.setPlainText(text)
        self.editor.setFont(QFont('Consolas', 11))
        self.highlighter = PythonHighlighter(self.editor.document())
        layout.addWidget(self.editor)

        self.timer = QTimer(self)
        self.timer.setInterval(self.autosave_interval)
        self.timer.timeout.connect(self.autosave)
        self.timer.start()

    def autosave(self):
        if self.path and self.is_modified():
            try:
                with open(self.path, 'w', encoding='utf-8') as f:
                    f.write(self.editor.toPlainText())
                self._saved_text = self.editor.toPlainText()
            except Exception as e:
                print("Autosave failed:", e)

    def is_modified(self):
        return self.editor.toPlainText() != self._saved_text

    def save(self, path: Path = None):
        if path:
            self.path = path
        if not self.path:
            return False
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write(self.editor.toPlainText())
        self._saved_text = self.editor.toPlainText()
        return True

# --------------------
# Main Window
# --------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1200, 800)

        # central layout: left explorer, center tabs, right outline, bottom terminal
        main = QWidget()
        main_layout = QVBoxLayout(main)
        self.setCentralWidget(main)

        # toolbar
        toolbar = QToolBar()
        self.addToolBar(toolbar)
        open_folder_act = QAction("Open Folder", self)
        open_file_act = QAction("Open File", self)
        save_act = QAction("Save", self)
        run_act = QAction("Run", self)
        install_act = QAction("Install Missing", self)
        theme_act = QAction("Toggle Theme", self)
        toolbar.addAction(open_folder_act)
        toolbar.addAction(open_file_act)
        toolbar.addAction(save_act)
        toolbar.addAction(run_act)
        toolbar.addAction(install_act)
        toolbar.addAction(theme_act)

        # top controls
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("Auto-save (s):"))
        self.autosave_input = QLineEdit("3")
        self.autosave_input.setFixedWidth(50)
        top_bar.addWidget(self.autosave_input)
        top_bar.addStretch()
        top_bar.addWidget(QLabel("Theme:"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Light", "Dark"])
        top_bar.addWidget(self.theme_combo)
        main_layout.addLayout(top_bar)

        # horizontal splitter: left explorer | center editor+terminal | right outline
        hsplit = QSplitter(Qt.Horizontal)

        # LEFT: Explorer
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        self.fs_model = QFileSystemModel()
        self.fs_model.setFilter(QDir.AllDirs | QDir.Files | QDir.NoDotAndDotDot)
        self.fs_model.setNameFilters(["*"])
        self.fs_model.setNameFilterDisables(False)
        self.tree = QTreeView()
        self.tree.setModel(self.fs_model)
        self.tree.setHeaderHidden(True)
        self.tree.clicked.connect(self.on_tree_clicked)
        left_layout.addWidget(QLabel("Explorer"))
        left_layout.addWidget(self.tree)
        hsplit.addWidget(left_widget)

        # CENTER: tabs + terminal (vertical)
        center_split = QSplitter(Qt.Vertical)
        center_top = QWidget()
        top_layout = QVBoxLayout(center_top)
        self.tabs = QTabWidget()
        top_layout.addWidget(self.tabs)
        center_split.addWidget(center_top)

        # terminal
        term_widget = QWidget()
        term_layout = QVBoxLayout(term_widget)
        self.terminal = QPlainTextEdit()
        self.terminal.setReadOnly(True)
        self.terminal.setFont(QFont("Consolas", 11))
        term_layout.addWidget(QLabel("Terminal / Output"))
        term_layout.addWidget(self.terminal)
        center_split.addWidget(term_widget)

        hsplit.addWidget(center_split)

        # RIGHT: outline / outline list
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        self.outline_list = QListWidget()
        right_layout.addWidget(QLabel("Outline"))
        right_layout.addWidget(self.outline_list)
        hsplit.addWidget(right_widget)

        hsplit.setStretchFactor(0, 1)
        hsplit.setStretchFactor(1, 3)
        hsplit.setStretchFactor(2, 1)

        main_layout.addWidget(hsplit)

        # status
        self.status = QStatusBar()
        self.setStatusBar(self.status)

        # connections
        open_folder_act.triggered.connect(self.open_folder)
        open_file_act.triggered.connect(self.open_file_dialog)
        save_act.triggered.connect(self.save_current)
        run_act.triggered.connect(self.run_current)
        install_act.triggered.connect(self.install_missing_current)
        theme_act.triggered.connect(self.toggle_theme)
        self.theme_combo.currentIndexChanged.connect(self.apply_theme)
        self.autosave_input.editingFinished.connect(self.on_autosave_changed)
        self.outline_list.itemClicked.connect(self.on_outline_clicked)

        # state
        self.current_folder = None
        self.installer = None
        self.proc = None

        # start with an empty tab
        self.new_tab()

        # apply theme
        self.current_theme = "Light"
        self.apply_theme()

    # --------------------
    # Explorer & file handling
    # --------------------
    def open_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Open Folder", os.path.expanduser("~"))
        if not folder:
            return
        self.current_folder = folder
        self.fs_model.setRootPath(folder)
        self.tree.setRootIndex(self.fs_model.index(folder))
        self.status.showMessage(f"Opened folder: {folder}", 4000)

    def on_tree_clicked(self, index):
        path = self.fs_model.filePath(index)
        if os.path.isdir(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            # binary or unreadable -- show message
            QMessageBox.information(self, "Info", f"Can't open file as text: {path}")
            return
        # open in new tab
        self.open_path_in_tab(Path(path), text)

    def open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open File", os.path.expanduser("~"), "All Files (*)")
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
            except Exception:
                QMessageBox.information(self, "Info", f"Can't open file as text: {path}")
                return
            self.open_path_in_tab(Path(path), text)

    def open_path_in_tab(self, path: Path, text: str):
        # check if already open
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, EditorTab) and tab.path and tab.path.samefile(path):
                self.tabs.setCurrentIndex(i)
                return
        # create new tab
        autosave_ms = max(1000, int(float(self.autosave_input.text() or "3")) * 1000)
        tab = EditorTab(path=path, text=text, autosave_interval=autosave_ms)
        idx = self.tabs.addTab(tab, path.name)
        self.tabs.setCurrentIndex(idx)
        tab.editor.textChanged.connect(self.on_editor_changed)
        # update outline for python files
        if path.suffix == ".py":
            self.update_outline_for_tab(tab)

    def new_tab(self):
        tab = EditorTab(None, "", autosave_interval=max(1000, int(float(self.autosave_input.text() or "3"))*1000))
        idx = self.tabs.addTab(tab, "untitled")
        self.tabs.setCurrentIndex(idx)
        tab.editor.textChanged.connect(self.on_editor_changed)

    # --------------------
    # Outline & editor events
    # --------------------
    def on_editor_changed(self):
        tab = self.current_tab()
        if not tab:
            return
        if tab.path and tab.path.suffix == ".py":
            self.update_outline_for_tab(tab)

    def update_outline_for_tab(self, tab: EditorTab):
        code = tab.editor.toPlainText()
        outline = outline_from_python(code)
        self.outline_list.clear()
        # imports
        if outline["imports"]:
            self.outline_list.addItem("== Imports ==")
            for name, ln in outline["imports"]:
                self.outline_list.addItem(f"{ln}: {name}")
        if outline["prints"]:
            self.outline_list.addItem("== Prints ==")
            for src, ln in outline["prints"]:
                preview = (src.strip().replace("\n", " "))[:80]
                self.outline_list.addItem(f"{ln}: {preview}")
        if outline["defs"]:
            self.outline_list.addItem("== Functions ==")
            for name, ln in outline["defs"]:
                self.outline_list.addItem(f"{ln}: def {name}()")
        if outline["classes"]:
            self.outline_list.addItem("== Classes ==")
            for name, ln in outline["classes"]:
                self.outline_list.addItem(f"{ln}: class {name}")

    def on_outline_clicked(self, item):
        text = item.text()
        if ":" not in text:
            return
        try:
            ln = int(text.split(":")[0])
        except Exception:
            return
        tab = self.current_tab()
        if not tab:
            return
        # move cursor to line
        cursor = tab.editor.textCursor()
        # calculate position (naive): move to start and then next lines
        tc = tab.editor.document().findBlockByLineNumber(max(0, ln-1))
        cursor.setPosition(tc.position())
        tab.editor.setTextCursor(cursor)
        tab.editor.setFocus()

    def current_tab(self) -> EditorTab:
        w = self.tabs.currentWidget()
        return w if isinstance(w, EditorTab) else None

    # --------------------
    # Save / Run / Install
    # --------------------
    def save_current(self):
        tab = self.current_tab()
        if not tab:
            return
        if not tab.path:
            path, _ = QFileDialog.getSaveFileName(self, "Save File", os.path.expanduser("~"), "All Files (*)")
            if not path:
                return
            tab.path = Path(path)
            self.tabs.setTabText(self.tabs.currentIndex(), Path(path).name)
        tab.save()
        self.status.showMessage("Saved", 2500)

    def run_current(self):
        tab = self.current_tab()
        if not tab or not tab.path:
            QMessageBox.warning(self, "Warning", "Please save the file before running.")
            return
        self.terminal.appendPlainText(f"\nRunning {tab.path}...\n")
        # Ensure any previous process is killed
        if self.proc and self.proc.state() != QProcess.NotRunning:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = QProcess(self)
        self.proc.setProgram(sys.executable)
        self.proc.setArguments([str(tab.path)])
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._proc_ready)
        self.proc.readyReadStandardError.connect(self._proc_ready)
        self.proc.finished.connect(lambda code, status: self.terminal.appendPlainText(f"\nProcess exited with {code}\n"))
        self.proc.start()

    def _proc_ready(self):
        if not self.proc:
            return
        data = self.proc.readAllStandardOutput().data().decode("utf-8", errors="replace")
        if data:
            self.terminal.appendPlainText(data)

    def install_missing_current(self):
        tab = self.current_tab()
        if not tab:
            return
        code = tab.editor.toPlainText()
        pkgs = sorted(scan_imports_ast(code))
        missing = [p for p in pkgs if not is_package_installed(p)]
        if not missing:
            QMessageBox.information(self, "Info", "No missing packages detected.")
            return
        self.terminal.appendPlainText(f"\nInstalling missing: {', '.join(missing)}\n")
        self.installer = InstallerThread(missing)
        self.installer.log.connect(self.terminal.appendPlainText)
        self.installer.finished.connect(self.on_install_finished)
        self.installer.start()

    def on_install_finished(self, ok, failed):
        if ok:
            self.terminal.appendPlainText("\nInstalled: " + ", ".join(ok) + "\n")
        if failed:
            self.terminal.appendPlainText("\nFailed: " + ", ".join(failed) + "\n")

    # --------------------
    # Theme & autosave change
    # --------------------
    def toggle_theme(self):
        self.current_theme = "Dark" if self.current_theme == "Light" else "Light"
        self.theme_combo.setCurrentText(self.current_theme)
        self.apply_theme()

    def apply_theme(self):
        if self.theme_combo.currentText() == "Dark":
            self.setStyleSheet(DARK_QSS)
        else:
            self.setStyleSheet("")

    def on_autosave_changed(self):
        try:
            secs = max(1, int(float(self.autosave_input.text())))
        except Exception:
            secs = 3
            self.autosave_input.setText("3")
        # update all tabs
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, EditorTab):
                tab.timer.setInterval(secs * 1000)

# --------------------
# Dark QSS
# --------------------
DARK_QSS = """
QWidget { background-color: #1e1e1e; color: #d4d4d4; }
QPlainTextEdit, QLineEdit, QListWidget { background-color: #252526; color: #d4d4d4; }
QTreeView { background-color: #252526; color: #d4d4d4; }
QPushButton { background-color: #2d2d2d; border: 1px solid #3c3c3c; padding: 4px; }
QTabBar::tab:selected { background: #3c3c3c; }
"""

# --------------------
# Run
# --------------------
def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
