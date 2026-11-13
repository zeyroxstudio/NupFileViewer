from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import threading
import time

from PySide6.QtCore import Qt, QAbstractTableModel, QLibraryInfo, QLocale, QModelIndex, QSettings, QSignalBlocker, QSize, QThread, QTranslator, QUrl, Signal, QTimer
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QKeySequence, QPainter, QPalette, QPixmap, QImageReader
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QSizePolicy,
    QPushButton, QToolButton, QDialog, QInputDialog, QLineEdit, QListWidget, QTreeWidget, QTreeWidgetItem,
    QTableView, QTableWidget, QTableWidgetItem, QTabWidget, QGroupBox, QSplitter, QStackedWidget, QPlainTextEdit, QFileDialog, QMessageBox,
    QProgressBar, QHeaderView, QAbstractItemView, QCheckBox, QComboBox, QMenu,
)

from . import __version__, updates
from .i18n import LANGUAGES, set_language, tr
from .archive import Archive, Cancelled, human_size


STYLE = """
QWidget { color: #232323; font-family: 'Segoe UI'; font-size: 12px; }
QMainWindow, QDialog, QMenuBar, QStatusBar { background: #f0f0f0; }
QGroupBox { border: 1px solid #939393; margin-top: 9px; padding: 6px 3px 3px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 6px; padding: 0 3px; background: #f0f0f0; }
QLabel#field { color: #8d1222; font-weight: 600; }
QLabel#value { border: 1px solid #b8b8b8; background: white; padding: 1px 3px; }
QLabel#muted { color: #616161; }
QWidget#empty { background: white; }
QLabel#drop { color: #666; font-size: 14px; }
QToolButton#cover { background: white; border: 1px solid #a5a5a5; color: #ad1022; }
QToolButton#cover:hover:enabled { border-color: #c31125; }
QTreeWidget, QListWidget, QTableView, QPlainTextEdit, QLineEdit { background: white; }
QTabBar::tab { padding: 4px 8px; }
QTabBar::tab:selected { color: #9e1223; }
QSplitter::handle { background: #d8d8d8; }
"""


def label(text="", name=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    return widget


def package_caption(package):
    version = package.display_version or f"NCM {package.version}"
    return f"{package.kind_name} · {version}"


class PackageDetails(QDialog):
    def __init__(self, package, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('Package details'))
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.package = package
        self.languages = sorted(package.titles.keys() | package.icons.keys())
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        fields = (
            (tr('Title'), package.title),
            (tr('Type'), package.kind_name),
            (tr('Title ID'), f"{package.title_id:016X}"),
            (tr('Version'), package.display_version or tr('Not declared')),
            (tr('NCM version'), str(package.version)),
            (tr('Publisher'), package.text(6)),
            (tr('Dump name'), package.text(8)),
            ('Build ID', package.metadata.get(7, b'').hex().upper()),
            ('NACP', human_size(len(package.metadata[2])) if 2 in package.metadata else tr('Absent')),
        )
        info = QPlainTextEdit()
        info.setReadOnly(True)
        info.setPlainText("\n\n".join(f"{name}\n{value or '—'}" for name, value in fields))
        tabs.addTab(info, tr('Metadata'))

        page = QWidget()
        languages_layout = QVBoxLayout(page)
        if self.languages:
            self.table = QTableWidget(len(self.languages), 3)
            self.table.setHorizontalHeaderLabels((tr('Language'), tr('Title'), tr('Icon')))
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.verticalHeader().hide()
            header = self.table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            for row, code in enumerate(self.languages):
                title = package.titles.get(code) or '—'
                icon = tr('Present') if code in package.icons else '—'
                for column, value in enumerate((code, title, icon)):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    self.table.setItem(row, column, item)
            languages_layout.addWidget(self.table)
            self.icon_preview = QPushButton(tr('Preview icon'))
            self.icon_preview.clicked.connect(self.show_language_icon)
            self.table.currentCellChanged.connect(self.update_icon_button)
            languages_layout.addWidget(self.icon_preview)
            self.table.setCurrentCell(0, 0)
            self.update_icon_button()
        else:
            languages_layout.addWidget(label(tr('No localized metadata in this package.')))
            languages_layout.addStretch()
        tabs.addTab(page, tr('Languages'))
        close = QPushButton(tr('Close'))
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        self.setFixedSize(420, 395)

    def update_icon_button(self, *_):
        row = self.table.currentRow()
        self.icon_preview.setEnabled(row >= 0 and self.languages[row] in self.package.icons)

    def show_language_icon(self):
        row = self.table.currentRow()
        if row < 0:
            return
        code = self.languages[row]
        icon = QPixmap()
        if not icon.loadFromData(self.package.icons.get(code, b'')):
            QMessageBox.information(self, tr('Preview icon'), tr('This icon could not be decoded.'))
            return
        show_image(self, f"{tr('Icon')} · {code}", icon)


def show_image(parent, title, image):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
    layout = QVBoxLayout(dialog)
    preview = QLabel()
    preview.setPixmap(image.scaled(
        360, 360, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    ))
    layout.addWidget(preview, alignment=Qt.AlignmentFlag.AlignCenter)
    close = QPushButton(tr('Close'))
    close.clicked.connect(dialog.accept)
    layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignCenter)
    dialog.setFixedSize(dialog.sizeHint())
    dialog.exec()


class Job(QThread):
    result = Signal(object)
    failed = Signal(str, bool)
    update = Signal(int, int, str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function
        self.cancel = threading.Event()
        self.last_update = 0

    def progress(self, value, total, message):
        now = time.monotonic()
        if now - self.last_update > .1 or value == total:
            self.update.emit(value, total, message)
            self.last_update = now

    def run(self):
        try:
            self.result.emit(self.function(self.cancel, self.progress))
        except Cancelled:
            self.failed.emit(tr('Canceled'), True)
        except Exception as exc:
            self.failed.emit(str(exc), False)


class FileModel(QAbstractTableModel):
    headers = ['File', 'Size', 'Stored', 'Codec']

    def __init__(self):
        super().__init__()
        self.rows = []
        self.full_paths = False
        self.sort_column = 0
        self.sort_order = Qt.SortOrder.AscendingOrder

    def replace(self, rows, full_paths=False):
        ordered = self.ordered_rows(rows, full_paths)
        self.beginResetModel()
        self.rows = ordered
        self.full_paths = full_paths
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return tr(self.headers[section])

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        entry = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return (entry.path if self.full_paths else entry.name,
                    human_size(entry.size), human_size(entry.stored), entry.compression)[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return entry.path
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() in (1, 2):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 3:
            return QColor("#8f1625" if entry.codec else "#625955")

    def sort(self, column, order):
        if column < 0:
            return
        self.sort_column = column
        self.sort_order = order
        self.replace(self.rows, self.full_paths)

    def ordered_rows(self, rows, full_paths):
        funcs = [lambda f: (f.path if full_paths else f.name).casefold(),
                 lambda f: f.size, lambda f: f.stored, lambda f: f.codec]
        return sorted(rows, key=funcs[self.sort_column],
                      reverse=self.sort_order == Qt.SortOrder.DescendingOrder)


class Window(QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        self.archive = None
        self.job = None
        self.active_package = 0
        self.directory = None
        self.verified_all = False
        self.last_path = None
        self.settings = QSettings("zeyroxstudio", "NupFileViewer")
        self.setWindowTitle(f"NupFileViewer v{__version__}")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, False)
        self.setWindowFlag(Qt.WindowType.MSWindowsFixedSizeDialogHint)
        self.setFixedSize(455, 665)
        self.statusBar().setSizeGripEnabled(False)
        self.setAcceptDrops(True)
        body = QWidget()
        self.setCentralWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(4, 0, 4, 3)
        layout.setSpacing(4)
        self.workspace = QStackedWidget()
        empty = QWidget()
        empty.setObjectName("empty")
        empty_layout = QVBoxLayout(empty)
        empty_layout.addStretch()
        logo = QLabel()
        logo.setPixmap(QApplication.windowIcon().pixmap(64, 64))
        empty_layout.addWidget(logo, alignment=Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addWidget(label(tr('Drop a .nup file here'), "drop"), alignment=Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch()
        self.workspace.addWidget(empty)
        layout.addWidget(self.workspace, 1)
        self.tabs = QTabWidget()
        self.tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.summary_page = QWidget()
        summary_layout = QVBoxLayout(self.summary_page)
        summary_layout.setContentsMargins(0, 8, 0, 0)
        self.tabs.addTab(self.summary_page, tr('Overview'))

        file_menu = self.menuBar().addMenu(tr('&File'))
        tools_menu = self.menuBar().addMenu(tr('&Tools'))
        self.open_button = file_menu.addAction(tr('Open archive…'), self.open_dialog)
        self.open_button.setShortcut(QKeySequence.StandardKey.Open)
        self.reopen_action = file_menu.addAction(tr('Reopen last archive'), self.reopen_archive)
        self.close_archive_action = file_menu.addAction(tr('Close archive'), self.close_archive)
        self.close_archive_action.setShortcut("Ctrl+Shift+C")
        self.report_button = file_menu.addAction(tr('Save report…'), self.save_report)
        file_menu.addSeparator()
        file_menu.addAction(tr('Exit'), self.close)

        summary_layout.setSpacing(5)
        archive_box = QGroupBox(tr('File'))
        archive_grid = QGridLayout(archive_box)
        archive_grid.setContentsMargins(5, 4, 5, 4)
        archive_grid.setSpacing(3)
        self.metrics = []
        for index, caption in enumerate((tr('Size'), tr('Uncompressed size'), tr('Files / folders'), tr('Integrity'))):
            self.metrics.append(self.add_field(archive_grid, caption, index // 2 * 2, index % 2))
        self.verify_summary_button = QPushButton(tr('Verify integrity…'))
        self.verify_summary_button.clicked.connect(lambda: self.verify(True))
        archive_grid.addWidget(self.verify_summary_button, 4, 0, 1, 2)
        summary_layout.addWidget(archive_box)

        package_box = QGroupBox(tr('Package information'))
        package_grid = QGridLayout(package_box)
        package_grid.setContentsMargins(5, 4, 5, 4)
        package_grid.setSpacing(3)
        self.package_summary = label("", "muted")
        self.package_summary.setWordWrap(True)
        package_grid.addWidget(self.package_summary, 0, 0, 1, 2)
        self.package_picker = QComboBox()
        self.package_picker.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.package_picker.setMinimumContentsLength(12)
        self.package_picker.setAccessibleName(tr('Packages'))
        self.package_picker.currentIndexChanged.connect(self.change_package)
        package_grid.addWidget(self.package_picker, 1, 0)
        self.package_details_button = QPushButton(tr('Details…'))
        self.package_details_button.clicked.connect(self.show_package_details)
        package_grid.addWidget(self.package_details_button, 1, 1)
        self.title_id = self.add_field(package_grid, tr('Title ID'), 2, 0)
        self.ncm_version = self.add_field(package_grid, tr('NCM version'), 2, 1)
        package_grid.setColumnStretch(0, 2)
        summary_layout.addWidget(package_box)

        self.hero = QGroupBox(tr('Title information'))
        presentation = QGridLayout(self.hero)
        presentation.setContentsMargins(5, 4, 5, 4)
        presentation.setSpacing(3)
        self.display_version = self.add_field(presentation, tr('Version'), 0, 0, 2)
        self.title = self.add_field(presentation, tr('Title'), 2, 0, 2)
        self.title.setWordWrap(True)
        self.publisher = self.add_field(presentation, tr('Publisher'), 4, 0, 2)
        self.cover_image = QPixmap()
        self.cover = QToolButton()
        self.cover.setObjectName("cover")
        self.cover.setText("NUP")
        self.cover.setAccessibleName(tr('Enlarge cover'))
        self.cover.setFixedSize(184, 184)
        self.cover.setIconSize(QSize(180, 180))
        self.cover.setEnabled(False)
        self.cover.clicked.connect(self.show_cover)
        self.cover.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.cover.customContextMenuRequested.connect(self.cover_menu)
        presentation.addWidget(self.cover, 6, 0, 1, 2, Qt.AlignmentFlag.AlignCenter)
        self.icon_button = file_menu.addAction(tr('Save icon…'), self.save_icon)
        summary_layout.addWidget(self.hero)
        summary_layout.addStretch()

        folders = QGroupBox(tr('Structure'))
        folders_layout = QVBoxLayout(folders)
        folders_layout.setContentsMargins(0, 8, 0, 0)
        self.package_label = label(tr('Packages'), "muted")
        folders_layout.addWidget(self.package_label)
        self.package_list = QListWidget()
        self.package_list.setMaximumHeight(104)
        self.package_list.setAccessibleName(tr('Packages'))
        self.package_list.currentRowChanged.connect(self.change_package)
        folders_layout.addWidget(self.package_list)
        self.package_label.hide()
        self.package_list.hide()
        self.content = QSplitter(Qt.Orientation.Vertical)
        self.content.setChildrenCollapsible(False)
        self.content.addWidget(folders)
        self.tabs.addTab(self.content, tr('Contents'))

        self.files_page = QWidget()
        explorer_layout = QVBoxLayout(self.files_page)
        explorer_layout.setContentsMargins(0, 8, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr('Search package…'))
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName(tr('Search for files in the package'))
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(200)
        self.search.textChanged.connect(lambda: self.search_timer.start())
        self.search_timer.timeout.connect(self.refresh_files)
        explorer_layout.addWidget(self.search)
        self.recursive = QCheckBox(tr('Subfolders'))
        self.recursive.setToolTip(tr('Include files in subfolders of the selected folder.'))
        self.recursive.toggled.connect(self.refresh_files)
        folders_layout.addWidget(self.recursive)
        self.breadcrumb = label(tr('No archive open'), "muted")
        self.breadcrumb.setWordWrap(True)
        self.breadcrumb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        explorer_layout.addWidget(self.breadcrumb)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.currentItemChanged.connect(self.change_directory)
        self.tree.itemDoubleClicked.connect(lambda: self.show_files())
        folders_layout.addWidget(self.tree, 1)
        self.table = QTableView()
        self.model = FileModel()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col, width in ((1, 82), (2, 82), (3, 82)):
            self.table.setColumnWidth(col, width)
        self.table.selectionModel().selectionChanged.connect(self.show_file_details)
        self.table.doubleClicked.connect(self.preview_selected)
        explorer_layout.addWidget(self.table, 1)
        self.count_label = label(tr('No archive'), "muted")
        self.count_label.setWordWrap(True)
        explorer_layout.addWidget(self.count_label)
        file_actions = QHBoxLayout()
        details_button = QPushButton(tr('Details'))
        details_button.clicked.connect(self.show_details_tab)
        file_actions.addWidget(details_button)
        self.preview_button = QPushButton(tr('Preview'))
        self.preview_button.clicked.connect(self.preview_selected)
        file_actions.addWidget(self.preview_button)
        self.export_button = QPushButton(tr('Extract selection…'))
        self.export_button.setObjectName("primary")
        self.export_button.clicked.connect(lambda: self.export(True))
        file_actions.addWidget(self.export_button)
        explorer_layout.addLayout(file_actions)
        self.detail_tabs = QTabWidget()
        self.detail_tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.detail_tabs.addTab(self.files_page, tr('File'))
        self.details = QTableWidget(0, 2)
        self.details.setHorizontalHeaderLabels([tr('Properties'), tr('Value')])
        self.details.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.details.verticalHeader().hide()
        self.details.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.details.setColumnWidth(0, 124)
        self.detail_tabs.addTab(self.details, tr('Properties'))
        self.preview_text = QPlainTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setFont(QFont("Consolas", 11))
        self.preview_text.setStyleSheet("font-family: Consolas; font-size: 13px;")
        self.preview_text.setPlainText(tr('Double-click a file to preview up to 64 KiB.'))
        self.detail_tabs.addTab(self.preview_text, tr('Preview'))
        self.metadata = QPlainTextEdit()
        self.metadata.setReadOnly(True)
        self.detail_tabs.addTab(self.metadata, tr('Metadata'))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1000)
        properties = QGroupBox(tr('Files and properties'))
        properties_layout = QVBoxLayout(properties)
        properties_layout.setContentsMargins(4, 3, 4, 4)
        properties_layout.addWidget(self.detail_tabs)
        self.content.addWidget(properties)
        self.content.setSizes([235, 350])
        self.work_split = QSplitter(Qt.Orientation.Vertical)
        self.work_split.addWidget(self.tabs)
        self.work_split.addWidget(self.log)
        self.log.hide()
        self.workspace.addWidget(self.work_split)
        self.workspace.setCurrentIndex(0)
        options = self.menuBar().addMenu(tr('&Options'))
        options.addAction(tr('Language…'), self.change_language)
        log_action = options.addAction(tr('Show log'))
        log_action.setCheckable(True)
        log_action.toggled.connect(self.toggle_log)
        self.update_action = options.addAction(tr('Check for updates…'), self.check_updates)
        self.auto_update_action = options.addAction(tr('Check for updates at startup'))
        self.auto_update_action.setCheckable(True)
        self.auto_update_action.setChecked(self.settings.value("checkUpdates", True, bool))
        self.auto_update_action.toggled.connect(lambda checked: self.settings.setValue("checkUpdates", checked))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.file_menu_at)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.folder_menu_at)

        self.verify_selected_button = tools_menu.addAction(tr('Verify selected files'), lambda: self.verify(False))
        self.verify_button = tools_menu.addAction(tr('Verify all'), lambda: self.verify(True))
        self.export_view_button = tools_menu.addAction(tr('Extract listed files…'), lambda: self.export(False))
        progress_layout = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        progress_layout.addWidget(self.progress, 1)
        self.cancel_button = QPushButton(tr('Cancel'))
        self.cancel_button.clicked.connect(self.cancel_job)
        self.cancel_button.setVisible(False)
        progress_layout.addWidget(self.cancel_button)
        layout.addLayout(progress_layout)
        self.statusBar().showMessage(tr('Ready'))
        self.integrity_badge = label()
        self.statusBar().addPermanentWidget(self.integrity_badge)
        self.integrity_badge.hide()
        self.github_button = QToolButton()
        self.github_button.setIcon(svg_icon("github.svg"))
        self.github_button.setIconSize(QSize(16, 16))
        self.github_button.setFixedSize(24, 22)
        self.github_button.setAutoRaise(True)
        self.github_button.setToolTip("GitHub · zeyroxstudio")
        self.github_button.setAccessibleName(tr("Open GitHub profile"))
        self.github_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.github_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://github.com/zeyroxstudio"))
        )
        self.statusBar().addPermanentWidget(self.github_button)
        self.controls = [self.report_button, self.icon_button, self.package_list, self.package_picker,
                         self.package_details_button, self.search,
                         self.recursive, self.tree, self.table, self.preview_button, self.verify_selected_button,
                         self.verify_button, self.export_view_button, self.export_button,
                         details_button, self.verify_summary_button, self.close_archive_action]
        self.set_controls()
        search_action = QAction(tr('Find'), self)
        search_action.setShortcut(QKeySequence.StandardKey.Find)
        search_action.triggered.connect(self.focus_search)
        self.addAction(search_action)
        if path:
            QTimer.singleShot(0, lambda: self.open_archive(path))
        if updates.portable_root():
            QTimer.singleShot(2200, self.startup_updates)

    def startup_updates(self):
        root = updates.portable_root()
        if root is None:
            return
        error_path = root / "update-error.txt"
        if error_path.is_file():
            message = error_path.read_text(encoding="utf-8-sig", errors="replace")
            self.log.appendPlainText(message)
            self.statusBar().showMessage(tr('Update failed. See update-error.txt.'))
            return
        if not self.auto_update_action.isChecked():
            return
        if self.job:
            QTimer.singleShot(1000, self.startup_updates)
        else:
            self.check_updates(automatic=True)

    def change_language(self):
        previous = self.settings.value("language")
        if select_language(self.settings, self) and self.settings.value("language") != previous:
            QMessageBox.information(self, tr('Language…'), tr('Restart the app to apply the language.'))

    def report(self):
        report = self.archive.report()
        report["content_verified"] = self.verified_all
        return report

    def check_updates(self, *, automatic=False):
        if self.job:
            return
        self.run_job(
            tr('Checking for updates…'), lambda cancel, progress: updates.check_release(cancel),
            lambda release: self.offer_update(release, automatic), quiet=automatic,
        )

    def offer_update(self, release, automatic):
        if release is None:
            if not automatic:
                QMessageBox.information(self, tr('Updates'), tr('No updates available.'))
            return
        root = updates.portable_root()
        if root is None:
            QMessageBox.information(self, tr('Updates'), tr('{0} is available on GitHub Releases. Use the portable build to install updates from the app.', release.tag))
            return
        answer = QMessageBox.question(
            self, tr('Update available'),
            tr('NupFileViewer {0} ({1}).\nDownload the update and restart the app?', release.tag, human_size(release.size)),
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.run_job(
                tr('Downloading update…'),
                lambda cancel, progress: updates.stage_release(release, root, cancel, progress),
                lambda stage: self.apply_update(stage, root),
            )

    def apply_update(self, stage, root):
        try:
            updates.install_staged(stage, root, self.archive.path if self.archive else None)
        except OSError as error:
            shutil.rmtree(stage, ignore_errors=True)
            self.job_failed(str(error))
            return
        self.close()

    @staticmethod
    def add_field(grid, caption, row, column, span=1):
        grid.addWidget(label(caption, "field"), row, column, 1, span)
        value = label("", "value")
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        grid.addWidget(value, row + 1, column, 1, span)
        grid.setColumnStretch(column, 1)
        return value

    def toggle_log(self, visible):
        self.log.setVisible(visible)
        if visible:
            self.work_split.setSizes([465, 125])

    def reopen_archive(self):
        if self.last_path:
            self.open_archive(self.last_path)

    def close_archive(self):
        if self.job:
            return
        self.search_timer.stop()
        self.archive = None
        self.directory = None
        self.tree.clear()
        self.model.replace([])
        self.cover_image = QPixmap()
        self.integrity_badge.hide()
        self.workspace.setCurrentIndex(0)
        self.statusBar().showMessage(tr('Ready'))
        self.setWindowTitle(f"NupFileViewer v{__version__}")
        self.set_controls()

    def cover_menu(self, point):
        if self.cover_image.isNull() or self.job:
            return
        menu = QMenu(self)
        menu.addAction(tr('Enlarge'), self.show_cover)
        menu.addAction(tr('Copy image'), lambda: QApplication.clipboard().setPixmap(self.cover_image))
        menu.addAction(tr('Save icon…'), self.save_icon)
        menu.exec(self.cover.mapToGlobal(point))

    def file_menu_at(self, point):
        if not self.selected() or self.job:
            return
        menu = QMenu(self)
        menu.addAction(tr('Properties'), self.show_details_tab)
        menu.addAction(tr('Preview'), self.preview_selected)
        menu.addAction(tr('Extract selection…'), lambda: self.export(True))
        menu.addAction(tr('Verify SHA-256'), lambda: self.verify(False))
        menu.exec(self.table.viewport().mapToGlobal(point))

    def folder_menu_at(self, point):
        item = self.tree.itemAt(point)
        if item is None or self.job:
            return
        self.tree.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction(tr('Show files'), self.show_files)
        menu.addAction(tr('Extract file list…'), lambda: self.export(False))
        menu.exec(self.tree.viewport().mapToGlobal(point))

    def show_files(self):
        if self.archive:
            self.tabs.setCurrentWidget(self.content)
            self.detail_tabs.setCurrentWidget(self.files_page)

    def focus_search(self):
        self.show_files()
        self.search.setFocus()

    def show_details_tab(self):
        self.tabs.setCurrentWidget(self.content)
        self.detail_tabs.setCurrentWidget(self.details)

    def set_controls(self):
        busy = self.job is not None
        for widget in self.controls:
            widget.setEnabled(self.archive is not None and not busy)
        self.open_button.setEnabled(not busy)
        self.reopen_action.setEnabled(self.last_path is not None and not busy)
        self.update_action.setEnabled(not busy)
        if self.archive:
            self.icon_button.setEnabled(not busy and bool(self.archive.packages[self.active_package].metadata.get(3)))

    def run_job(self, name, function, done, *, quiet=False, on_failure=None):
        if self.job is not None:
            return
        self.job_done = done
        self.job_results = []
        self.job_quiet = quiet
        self.job_failure = on_failure
        if not quiet:
            self.log.appendPlainText(f"{time.strftime('%H:%M:%S')}  {name}")
            self.statusBar().showMessage(name)
            self.progress.setRange(0, 0)
            self.progress.show()
            self.cancel_button.show()
            self.cancel_button.setEnabled(True)
        self.job = Job(function, self)
        self.job.result.connect(self.record_job_result)
        self.job.failed.connect(self.job_failed)
        self.job.update.connect(self.update_progress)
        self.job.finished.connect(self.job_finished)
        self.set_controls()
        self.job.start()

    def record_job_result(self, result):
        self.job_results.append(result)

    def job_failed(self, message, canceled=False):
        self.log.appendPlainText(message)
        if self.job and self.job_failure:
            self.job_failure(message, canceled)
        if self.job and self.job_quiet:
            return
        self.statusBar().showMessage(message)
        if not canceled:
            QMessageBox.warning(self, tr('Operation failed'), message)

    def job_finished(self):
        finished = self.job
        done, results = self.job_done, self.job_results
        self.job = None
        finished.deleteLater()
        self.progress.hide()
        self.cancel_button.hide()
        self.set_controls()
        if results and not finished.cancel.is_set():
            done(results[0])
        elif results:
            if self.job_failure:
                self.job_failure(tr('Canceled'), True)
            if isinstance(results[0], Path):
                shutil.rmtree(results[0], ignore_errors=True)

    def update_progress(self, value, total, message):
        self.progress.setRange(0, total)
        self.progress.setValue(value)
        self.statusBar().showMessage(f"{value:,} / {total:,} · {message}")

    def cancel_job(self):
        if self.job:
            self.job.cancel.set()
            self.cancel_button.setEnabled(False)
            self.statusBar().showMessage(tr('Canceling…'))

    def open_dialog(self):
        if self.job:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, tr('Open NUP archive'), "", tr('NUP archives (*.nup);;All files (*)'),
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if path:
            self.open_archive(path)

    def open_archive(self, path):
        self.run_job(tr('Reading and verifying the index…'), lambda cancel, progress: Archive(path, cancel), self.loaded)

    def loaded(self, archive):
        self.archive = archive
        self.last_path = archive.path
        self.workspace.setCurrentWidget(self.work_split)
        self.setWindowTitle(f"NupFileViewer v{__version__} - {archive.path.name}")
        self.verified_all = False
        self.active_package = 0
        self.search.clear()
        self.search_timer.stop()
        self.preview_text.setPlainText(tr('Double-click a file to preview up to 64 KiB.'))
        with QSignalBlocker(self.package_list), QSignalBlocker(self.package_picker):
            self.package_list.clear()
            self.package_picker.clear()
            for p in archive.packages:
                caption = package_caption(p)
                self.package_list.addItem(f"{caption}  ({p.title_id:016X})")
                self.package_picker.addItem(caption)
        groups = {}
        for package in archive.packages:
            groups.setdefault(package.kind, []).append(package)
        summary = [tr('Base missing')] if 0 not in groups else []
        for kind, packages in sorted(groups.items()):
            caption = f"{len(packages)} × {packages[0].kind_name}"
            if kind == 1:
                newest = max(packages, key=lambda p: p.version)
                version = newest.display_version or f"NCM {newest.version}"
                caption += f" ({version})"
            summary.append(caption)
        self.package_summary.setText(" · ".join(summary))
        has_multiple_packages = len(archive.packages) > 1
        self.package_label.setVisible(has_multiple_packages)
        self.package_list.setVisible(has_multiple_packages)
        self.metrics[0].setText(human_size(archive.size))
        self.metrics[1].setText(human_size(archive.logical_size))
        self.metrics[2].setText(f"{len(archive.files):,} / {len(archive.dirs):,}")
        self.set_integrity(tr('Index verified'), "#15508a", tr('The index SHA-256 matches. File contents are checked separately.'))
        self.hero.setToolTip(str(archive.path))
        self.change_package(0)
        self.tabs.setCurrentWidget(self.summary_page)
        self.log.appendPlainText(tr('Index SHA-256 matched. Files: {0:,}. Unique extents: {1:,}.', len(archive.files), archive.unique_extents))
        self.statusBar().showMessage(tr('Archive opened'))

    def change_package(self, index):
        if not self.archive or not 0 <= index < len(self.archive.packages):
            return
        with QSignalBlocker(self.package_list), QSignalBlocker(self.package_picker):
            self.package_list.setCurrentRow(index)
            self.package_picker.setCurrentIndex(index)
        self.active_package = index
        p = self.archive.packages[index]
        self.title.setText(p.title)
        self.title_id.setText(f"{p.title_id:016X}")
        self.ncm_version.setText(str(p.version))
        self.display_version.setText(p.display_version or tr('Not declared'))
        self.publisher.setText(p.text(6) or "—")
        icon = QPixmap()
        if p.metadata.get(3):
            icon.loadFromData(p.metadata[3])
        self.cover_image = icon
        has_cover = not icon.isNull()
        self.cover.setIcon(QIcon(icon) if has_cover else QIcon())
        self.cover.setText("" if has_cover else "NUP")
        self.cover.setEnabled(has_cover)
        self.cover.setToolTip(tr('Enlarge cover') if has_cover else "")
        self.cover.setCursor(Qt.CursorShape.PointingHandCursor if has_cover else Qt.CursorShape.ArrowCursor)
        self.metadata.setPlainText(json.dumps(self.report(), ensure_ascii=False, indent=2))
        self.tree.blockSignals(True)
        self.tree.clear()
        root = QTreeWidgetItem([p.kind_name])
        root.setData(0, Qt.ItemDataRole.UserRole, p.root)
        self.tree.addTopLevelItem(root)
        items = {p.root: root}
        stack = [p.root]
        while stack:
            di = stack.pop()
            for child in self.archive.dirs[di].children:
                if child < len(self.archive.dirs):
                    item = QTreeWidgetItem([self.archive.dirs[child].name])
                    item.setData(0, Qt.ItemDataRole.UserRole, child)
                    items[di].addChild(item)
                    items[child] = item
                    stack.append(child)
        root.setExpanded(True)
        self.tree.setCurrentItem(root)
        self.tree.blockSignals(False)
        self.directory = p.root
        self.refresh_files()
        self.set_controls()

    def change_directory(self, item, previous):
        if item:
            self.directory = item.data(0, Qt.ItemDataRole.UserRole)
            if not self.search.text().strip():
                self.refresh_files()

    def refresh_files(self):
        if not self.archive or self.directory is None:
            return
        query = self.search.text().casefold().strip()
        directory = self.archive.dirs[self.directory]
        if query:
            rows = [f for f in self.archive.files if f.package == self.active_package and query in f.path.casefold()]
        else:
            dc = len(self.archive.dirs)
            rows = []
            pending = [self.directory]
            while pending:
                for node in self.archive.dirs[pending.pop()].children:
                    if node >= dc:
                        rows.append(self.archive.files[node - dc])
                    elif self.recursive.isChecked():
                        pending.append(node)
        self.model.replace(rows, bool(query) or self.recursive.isChecked())
        self.breadcrumb.setText(tr('Searching the entire package') if query else tr('Package / ') + (directory.path or tr('All contents')))
        self.count_label.setText(
            tr('No files in this folder.\nOpen a subfolder.')
            if not rows and not query and not self.recursive.isChecked() and directory.children
            else tr('Files listed: {0:,}', len(rows))
        )
        self.details.setRowCount(0)

    def selected(self):
        return [self.model.rows[i.row()] for i in self.table.selectionModel().selectedRows()]

    def show_file_details(self, *args):
        rows = self.selected()
        self.details.setRowCount(0)
        if not rows:
            return
        f = rows[0]
        properties = (
            (tr('Name'), f.name), (tr('Path'), f.path), (tr('Size'), human_size(f.size)),
            (tr('Stored size'), human_size(f.stored)), (tr('Codec'), f.compression),
            ("Offset", f"0x{f.offset:X}"), ("SHA-256", f.sha256.hex()),
            (tr('Selected'), str(len(rows))),
        )
        self.details.setRowCount(len(properties))
        for row, (name, value) in enumerate(properties):
            self.details.setItem(row, 0, QTableWidgetItem(name))
            item = QTableWidgetItem(value)
            item.setToolTip(value)
            self.details.setItem(row, 1, item)

    def preview_selected(self, *args):
        rows = self.selected()
        if not rows or self.job:
            return
        f = rows[0]
        def show(data):
            heading = tr('{0}\nFirst {1:,} of {2:,} bytes · Preview, hash not checked\n\n', f.path, len(data), f.size)
            try:
                text = data.decode("utf-8")
                if any(ord(c) < 32 and c not in "\n\r\t" for c in text):
                    raise ValueError()
            except (UnicodeDecodeError, ValueError):
                lines = []
                for offset in range(0, len(data), 16):
                    chunk = data[offset:offset+16]
                    ascii_text = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
                    lines.append(f"{offset:08X}  {chunk.hex(' '):47}  {ascii_text}")
                text = "\n".join(lines)
            self.preview_text.setPlainText(heading + text)
            self.tabs.setCurrentWidget(self.content)
            self.detail_tabs.setCurrentWidget(self.preview_text)
            self.statusBar().showMessage(tr('Preview loaded'))
        self.run_job(tr('Loading preview…'), lambda cancel, progress: self.archive.preview(f), show)

    def set_integrity(self, message, color, tooltip=""):
        for field in (self.metrics[3], self.integrity_badge):
            field.setText(message)
            field.setStyleSheet(f"color: {color}; font-weight: 600;")
            field.setToolTip(tooltip or message)
        self.integrity_badge.show()

    def verify(self, all_files):
        if not self.archive or self.job:
            return
        entries = self.archive.files if all_files else self.selected()
        if not entries:
            QMessageBox.information(self, tr('Verify'), tr('Select at least one file from the list.'))
            return
        self.set_integrity(tr('Verifying…'), "#15508a")

        def done(message):
            if all_files:
                self.verified_all = True
                self.metadata.setPlainText(json.dumps(self.report(), ensure_ascii=False, indent=2))
            result = tr('Files verified') if all_files or self.verified_all else tr('Selection verified')
            self.set_integrity(result, "#176335", message)
            self.log.appendPlainText(message)
            self.statusBar().showMessage(message)

        def failed(message, canceled):
            if canceled:
                self.set_integrity(tr('Verification canceled'), "#805511")
            else:
                self.verified_all = False
                self.metadata.setPlainText(json.dumps(self.report(), ensure_ascii=False, indent=2))
                self.set_integrity(tr('Verification failed'), "#a91e30", message)

        self.run_job(tr('Checking SHA-256 for {0:,} files…', len(entries)),
                     lambda cancel, progress: self.archive.verify(entries, cancel, progress), done, on_failure=failed)

    def export(self, selection):
        if not self.archive or self.job:
            return
        entries = self.selected() if selection else list(self.model.rows)
        if not entries:
            QMessageBox.information(self, tr('Extract'), tr('No files to extract. Select one or more files.'))
            return
        destination = QFileDialog.getExistingDirectory(
            self, tr('Extract {0:,} files ({1})', len(entries), human_size(sum(f.size for f in entries))),
            options=QFileDialog.Option.DontUseNativeDialog | QFileDialog.Option.ShowDirsOnly,
        )
        if not destination:
            return
        def done(message):
            self.log.appendPlainText(message)
            self.statusBar().showMessage(message)
        self.run_job(tr('Extracting {0:,} files and checking SHA-256…', len(entries)),
                     lambda cancel, progress: self.archive.export(entries, destination, cancel, progress), done)

    def save_report(self):
        if not self.archive:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, tr('Save report'), "NUP-report.json", "JSON (*.json)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if path:
            try:
                if Path(path).resolve() == self.archive.path:
                    raise OSError(tr('The report cannot replace the open archive.'))
                Path(path).write_text(json.dumps(self.report(), ensure_ascii=False, indent=2), encoding="utf-8")
                self.statusBar().showMessage(tr('Report saved'))
            except OSError as exc:
                self.job_failed(str(exc))

    def show_cover(self):
        if not self.cover_image.isNull():
            show_image(self, tr('Cover'), self.cover_image)

    def show_package_details(self):
        if self.archive and not self.job:
            PackageDetails(self.archive.packages[self.active_package], self).exec()

    def save_icon(self):
        if not self.archive:
            return
        data = self.archive.packages[self.active_package].metadata.get(3)
        if not data:
            return
        suffix = ".png" if data.startswith(b"\x89PNG") else ".jpg" if data.startswith(b"\xff\xd8") else ".bin"
        path, _ = QFileDialog.getSaveFileName(
            self, tr('Save original icon'), "icon" + suffix, tr('Image (*') + suffix + ")",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if path:
            try:
                if Path(path).resolve() == self.archive.path:
                    raise OSError(tr('The icon cannot replace the open archive.'))
                Path(path).write_bytes(data)
                self.statusBar().showMessage(tr('Icon saved'))
            except OSError as exc:
                self.job_failed(str(exc))

    def dragEnterEvent(self, event):
        if not self.job and event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not self.job:
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    self.open_archive(url.toLocalFile())
                    break

    def closeEvent(self, event):
        if self.job:
            self.cancel_job()
            self.statusBar().showMessage(tr('Canceling. Close the window when the operation ends.'))
            event.ignore()
        else:
            event.accept()


def configure_appearance(app):
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in {
        QPalette.ColorRole.Window: "#ffffff",
        QPalette.ColorRole.Base: "#ffffff",
        QPalette.ColorRole.AlternateBase: "#f5f5f5",
        QPalette.ColorRole.Button: "#eeeeee",
        QPalette.ColorRole.WindowText: "#232323",
        QPalette.ColorRole.Text: "#232323",
        QPalette.ColorRole.ButtonText: "#232323",
        QPalette.ColorRole.Highlight: "#1767ad",
        QPalette.ColorRole.HighlightedText: "#ffffff",
    }.items():
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)


def select_language(settings, parent=None):
    names = list(LANGUAGES.values())
    current = names.index(LANGUAGES.get(settings.value("language"), "English"))
    first_run = parent is None
    title = "NupFileViewer" if first_run else tr('Language…')
    prompt = "Language / Lingua / Idioma / Langue" if first_run else tr('Application language')
    name, accepted = QInputDialog.getItem(parent, title, prompt, names, current, False)
    if accepted:
        settings.setValue("language", next(code for code, value in LANGUAGES.items() if value == name))
        settings.sync()
    return accepted


def configure_language(app, code):
    set_language(code)
    if code not in {"it", "es", "fr"}:
        QLocale.setDefault(QLocale("en"))
        return
    QLocale.setDefault(QLocale(code))
    translator = QTranslator(app)
    path = (Path(sys._MEIPASS) / "translations" if getattr(sys, "frozen", False)
            else Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)))
    if translator.load(str(path / f"qtbase_{code}.qm")):
        app.installTranslator(translator)
        app.qt_translator = translator


def svg_icon(filename):
    renderer = QSvgRenderer(str(Path(__file__).with_name(filename)))
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def main():
    args = sys.argv[1:]
    ready = None
    if len(args) >= 2 and args[0] == "--update-ready":
        ready = Path(args[1])
        args = args[2:]
    app = QApplication([sys.argv[0]])
    app.setApplicationName("NupFileViewer")
    app.setWindowIcon(svg_icon("logo.svg"))
    configure_appearance(app)
    QImageReader.setAllocationLimit(32)
    settings = QSettings("zeyroxstudio", "NupFileViewer")
    if settings.value("language") not in LANGUAGES:
        # The updater must be able to finish while the language dialog waits for input.
        if ready:
            QTimer.singleShot(0, lambda marker=ready: marker.touch(exist_ok=False))
            ready = None
        if not select_language(settings):
            return
    configure_language(app, settings.value("language"))
    window = Window(args[0] if args else None)
    window.show()
    if ready:
        QTimer.singleShot(0, lambda: ready.touch(exist_ok=False))
    sys.exit(app.exec())
