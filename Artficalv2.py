import sys
import ast
import os
import subprocess
import traceback
from pathlib import Path
from typing import List, Set
import importlib.util

from PySide6.QtCore import Qt, QTimer, QThread, Signal, QProcess
from PySide6.QtGui import QFont, QColor, QTextCharFormat, QSyntaxHighlighter, QAction
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QMessageBox, QPlainTextEdit,
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSplitter,
    QTabWidget, QLineEdit, QStatusBar, QComboBox, QToolBar
)

APP_NAME = "Artfical v2.0"

# --------- Import scanner ---------
def scan_imports_ast(code: str) -> Set[str]:
    packages = set()
    try:
        tree = ast.parse(code)
    except Exception:
        return packages
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                packages.add(n.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                packages.add(node.module.split('.')[0])
    return packages

def is_package_installed(pkg: str) -> bool:
    try:
        return importlib.util.find_spec(pkg) is not None
    except Exception:
        return False

# --------- Installer Thread ---------
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
                if proc.returncode == 0:
                    self.log.emit(proc.stdout.strip())
                    succeeded.append(pkg)
                else:
                    self.log.emit(proc.stdout.strip())
                    self.log.emit(proc.stderr.strip())
                    failed.append(pkg)
            except Exception as e:
                self.log.emit(f"Exception: {e}\n{traceback.format_exc()}")
                failed.append(pkg)
        self.finished.emit(succeeded, failed)

# --------- Syntax Highlighter ---------
class PythonHighlighter(QSyntaxHighlighter):
    KEYWORDS = (
        'False None True and as assert async await break class continue def del elif else '
        'except finally for from global if import in is lambda nonlocal not or pass raise '
        'return try while with yield'
    ).split()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.keyword_fmt = QTextCharFormat()
        self.keyword_fmt.setForeground(QColor(86, 156, 214))
        self.keyword_fmt.setFontWeight(QFont.Weight.Bold)

        self.string_fmt = QTextCharFormat()
        self.string_fmt.setForeground(QColor(206, 145, 120))

        self.comment_fmt = QTextCharFormat()
        self.comment_fmt.setForeground(QColor(87, 166, 74))
        self.comment_fmt.setFontItalic(True)

        self.number_fmt = QTextCharFormat()
        self.number_fmt.setForeground(QColor(181, 206, 168))

        import re
        self.keyword_re = [re.compile(rf"\b{k}\b") for k in self.KEYWORDS]
        self.string_re = re.compile(r'(\".*?\"|\'.*?\')')
        self.comment_re = re.compile(r"#.*")
        self.number_re = re.compile(r"\b\d+(\.\d+)?\b")

    def highlightBlock(self, text: str):
        import re
        for m in self.string_re.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self.string_fmt)
        for m in self.comment_re.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self.comment_fmt)
        for k in self.keyword_re:
            for m in k.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), self.keyword_fmt)
        for m in self.number_re.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self.number_fmt)

# --------- Editor Tab ---------
class EditorTab(QWidget):
    def __init__(self, path: Path = None, text: str = "", autosave_interval: int = 5000):
        super().__init__()
        self.path = path
        self._saved_text = text

        layout = QVBoxLayout(self)
        self.editor = QPlainTextEdit()
        self.editor.setPlainText(text)
        self.editor.setFont(QFont('Consolas', 11))
        PythonHighlighter(self.editor.document())
        layout.addWidget(self.editor)

        self.timer = QTimer(self)
        self.timer.setInterval(autosave_interval)
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

# --------- Main Window ---------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 750)

        central = QWidget()
        layout = QVBoxLayout(central)
        self.setCentralWidget(central)

        toolbar = QToolBar()
        self.addToolBar(toolbar)

        open_act = QAction('Open', self)
        save_act = QAction('Save', self)
        run_act = QAction('Run', self)
        install_act = QAction('Install Imports', self)
        theme_act = QAction('Toggle Theme', self)

        for act in [open_act, save_act, run_act, install_act, theme_act]:
            toolbar.addAction(act)

        top_bar = QHBoxLayout()
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(['Light', 'Dark'])
        top_bar.addWidget(QLabel('Theme:'))
        top_bar.addWidget(self.theme_combo)
        layout.addLayout(top_bar)

        splitter = QSplitter(Qt.Vertical)
        self.tabs = QTabWidget()
        splitter.addWidget(self.tabs)

        self.terminal = QPlainTextEdit()
        self.terminal.setReadOnly(True)
        splitter.addWidget(self.terminal)
        layout.addWidget(splitter)

        self.status = QStatusBar()
        self.setStatusBar(self.status)

        open_act.triggered.connect(self.open_file)
        save_act.triggered.connect(self.save_file)
        run_act.triggered.connect(self.run_file)
        install_act.triggered.connect(self.install_imports)
        theme_act.triggered.connect(self.toggle_theme)
        self.theme_combo.currentIndexChanged.connect(self.apply_theme)

        self.new_tab()
        self.current_theme = 'Light'
        self.apply_theme()

    def new_tab(self, path=None, text=''):
        tab = EditorTab(path, text)
        title = path.name if path else 'untitled'
        self.tabs.addTab(tab, title)

    def current_tab(self):
        return self.tabs.currentWidget()

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open File", "", "Python Files (*.py)")
        if path:
            with open(path, 'r', encoding='utf-8') as f:
                text = f.read()
            self.new_tab(Path(path), text)

    def save_file(self):
        tab = self.current_tab()
        if not tab:
            return
        if not tab.path:
            path, _ = QFileDialog.getSaveFileName(self, "Save File", "", "Python Files (*.py)")
            if not path:
                return
            tab.path = Path(path)
        tab.save()
        self.status.showMessage("File saved", 3000)

    def run_file(self):
        tab = self.current_tab()
        if not tab or not tab.path:
            QMessageBox.warning(self, "Warning", "Save the file before running.")
            return
        tab.save()
        self.terminal.appendPlainText(f"\nRunning {tab.path}...\n")
        proc = subprocess.Popen(
            [sys.executable, str(tab.path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        out, err = proc.communicate()
        self.terminal.appendPlainText(out)
        if err:
            self.terminal.appendPlainText(err)

    def install_imports(self):
        tab = self.current_tab()
        if not tab:
            return
        code = tab.editor.toPlainText()
        pkgs = sorted(scan_imports_ast(code))
        missing = [p for p in pkgs if not is_package_installed(p)]
        if not missing:
            QMessageBox.information(self, "Info", "All imports are installed.")
            return
        self.terminal.appendPlainText(f"Installing missing: {', '.join(missing)}")
        self.installer = InstallerThread(missing)
        self.installer.log.connect(self.terminal.appendPlainText)
        self.installer.start()

    def toggle_theme(self):
        self.current_theme = 'Dark' if self.current_theme == 'Light' else 'Light'
        self.theme_combo.setCurrentText(self.current_theme)
        self.apply_theme()

    def apply_theme(self):
        if self.theme_combo.currentText() == 'Dark':
            self.setStyleSheet(DARK_QSS)
        else:
            self.setStyleSheet("")

# --------- Dark Theme ---------
DARK_QSS = """
QWidget { background-color: #1e1e1e; color: #d4d4d4; }
QPlainTextEdit, QLineEdit { background-color: #252526; color: #d4d4d4; }
QPushButton { background-color: #2d2d2d; border: 1px solid #3c3c3c; padding: 4px; }
QPushButton:hover { background-color: #3c3c3c; }
QTabBar::tab:selected { background: #3c3c3c; }
"""

# --------- Main ---------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
