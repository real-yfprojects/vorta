import enum
import json
import logging
import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import List, Optional, Tuple, cast

from PyQt5 import uic
from PyQt5.QtCore import (QMimeData, QModelIndex, QPoint,
                          QSortFilterProxyModel, Qt, QUrl, pyqtSignal)
from PyQt5.QtGui import QColor, QKeySequence
from PyQt5.QtWidgets import (QApplication, QHeaderView, QMenu, QShortcut,
                             QTreeView)

from vorta.i18n import translate
from vorta.utils import get_asset, pretty_bytes, uses_dark_mode
from vorta.views.partials.treemodel import FileSystemItem, FileTreeModel
from vorta.views.utils import get_colored_icon

uifile = get_asset('UI/diffresult.ui')
DiffResultUI, DiffResultBase = uic.loadUiType(uifile)

logger = logging.getLogger(__name__)


class DiffResultDialog(DiffResultBase, DiffResultUI):
    """Display the results of `borg diff`."""
    def __init__(self, fs_data, archive_newer, archive_older, json_lines):
        """Init."""
        super().__init__()
        self.setupUi(self)

        self.model = DiffTree(self)

        # Older version do not support json output
        if json_lines:
            # If fs_data is already a dict, then there was just a single json-line
            # and the default handler already parsed into json dict, otherwise
            # fs_data is a str, and needs to be split and parsed into json dicts
            if isinstance(fs_data, dict):
                lines = [fs_data]
            else:
                lines = [
                    json.loads(line) for line in fs_data.split('\n') if line
                ]

            parse_diff_json(lines, self.model)
        else:
            lines = [line for line in fs_data.split('\n') if line]
            parse_diff_lines(lines, self.model)

        self.treeView: QTreeView
        self.treeView.setUniformRowHeights(
            True)  # Allows for scrolling optimizations.
        self.treeView.setAlternatingRowColors(True)
        self.treeView.setTextElideMode(
            Qt.TextElideMode.ElideMiddle)  # to better see name of paths

        # custom context menu
        self.treeView.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.treeView.customContextMenuRequested.connect(
            self.treeview_context_menu)

        # shortcuts
        shortcut_copy = QShortcut(QKeySequence.StandardKey.Copy, self.treeView)
        shortcut_copy.activated.connect(self.diff_item_copy)

        # add sort proxy model
        self.sortproxy = DiffSortProxyModel(self)
        self.sortproxy.setSourceModel(self.model)
        self.treeView.setModel(self.sortproxy)
        self.sortproxy.sorted.connect(self.slot_sorted)

        self.treeView.setSortingEnabled(True)

        # header
        header = self.treeView.header()
        header.setStretchLastSection(False)  # stretch only first section
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)

        # signals

        self.archiveNameLabel_1.setText(f'{archive_newer.name}')
        self.archiveNameLabel_2.setText(f'{archive_older.name}')

        self.comboBoxDisplayMode.currentIndexChanged.connect(
            self.change_display_mode)
        self.bFoldersOnTop.toggled.connect(self.sortproxy.keepFoldersOnTop)
        self.bCollapseAll.clicked.connect(self.treeView.collapseAll)

        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)

        self.set_icons()

        # Connect to palette change
        QApplication.instance().paletteChanged.connect(
            lambda p: self.set_icons())

    def set_icons(self):
        """Set or update the icons in the right color scheme."""
        self.bCollapseAll.setIcon(get_colored_icon('angle-up-solid'))

    def treeview_context_menu(self, pos: QPoint):
        """Display a context menu for `treeView`."""
        index = self.treeView.indexAt(pos)
        if not index.isValid():
            # popup only for items
            return

        menu = QMenu(self.treeView)

        menu.addAction(get_colored_icon('copy'), self.tr("Copy"),
                       lambda: self.diff_item_copy(index))

        if self.model.getMode() != self.model.DisplayMode.FLAT:
            menu.addSeparator()
            menu.addAction(get_colored_icon('angle-down-solid'),
                           self.tr("Expand recursively"),
                           lambda: self.treeView.expandRecursively(index))

        menu.popup(self.treeView.viewport().mapToGlobal(pos))

    def diff_item_copy(self, index: QModelIndex = None):
        """
        Copy a diff item path to the clipboard.

        Copies the first selected item if no index is specified.
        """
        if index is None or (not index.isValid()):
            indexes = self.treeView.selectionModel().selectedRows()

            if not indexes:
                return

            index = indexes[0]

        index = self.sortproxy.mapToSource(index)
        item = index.internalPointer()
        path = PurePath('/') / item.path

        data = QMimeData()
        data.setUrls([QUrl(path.as_uri())])
        data.setText(str(path))

        QApplication.clipboard().setMimeData(data)

    def change_display_mode(self, selection: int):
        """
        Change the display mode of the tree view

        The `selection` parameter specifies the index of the selected mode in
        `comboBoxDisplayMode`.

        """
        if selection == 0:
            mode = FileTreeModel.DisplayMode.TREE
        elif selection == 1:
            mode = FileTreeModel.DisplayMode.SIMPLIFIED_TREE
        elif selection == 2:
            mode = FileTreeModel.DisplayMode.FLAT
        else:
            raise Exception(
                "Unknown item in comboBoxDisplayMode with index {}".format(
                    selection))

        self.model.setMode(mode)

    def slot_sorted(self, column, order):
        """React the tree view being sorted."""
        # reveal selection
        selectedRows = self.treeView.selectionModel().selectedRows()
        if selectedRows:
            self.treeView.scrollTo(selectedRows[0])


# ---- Output parsing --------------------------------------------------------


def parse_diff_json(diffs: List[dict], model: 'DiffTree'):
    """Parse the json output from `borg diff`."""
    for item in diffs:
        path = PurePath(item['path'])
        file_type = FileType.FILE
        size = 0
        change_type: ChangeType = None
        mode_change: Optional[Tuple[str, str]] = None
        owner_change: Optional[Tuple[str, str, str, str]] = None
        modified: Optional[Tuple[int, int]] = None

        # added link, removed link, changed link
        # modified (added, removed), added (size), removed (size)
        # added directory, removed directory
        # owner (old_user, new_user, old_group, new_group))
        # mode (old_mode, new_mode)
        for change in item['changes']:
            # if more than one type of change has happened for this file/dir/link, then report the most important
            # (higher priority)
            if {'type': 'modified'} == change:
                # modified, but can't compare ids -> no added, removed
                change_type = ChangeType.MODIFIED
            elif change['type'] == 'modified':
                # modified with added, removed
                change_type = ChangeType.MODIFIED
                size = change['added'] - change['removed']
                modified = (change['added'], change['removed'])

            elif change['type'] == 'changed link':
                change_type = ChangeType.CHANGED_LINK
                file_type = FileType.LINK

            elif change['type'] in [
                    'added', 'removed', 'added link', 'removed link',
                    'added directory', 'removed directory'
            ]:
                if 'directory' in change['type']:
                    file_type = FileType.DIRECTORY
                elif 'link' in change['type']:
                    file_type = FileType.LINK

                size = change.get('size', 0)

                a_r = change['type'].split()[0]  # 'added' or 'removed'
                if a_r == 'added':
                    change_type = ChangeType.ADDED
                else:
                    change_type = ChangeType.REMOVED
                    size = -size

            elif change['type'] == 'mode':
                # mode change can occur along with previous changes
                change_type = ChangeType.MODIFIED
                mode_change = (change['old_mode'], change['new_mode'])

            elif change['type'] == 'owner':
                # owner change can occur along with previous changes
                change_type = ChangeType.MODIFIED

                owner_change = (change['old_user'], change['old_group'],
                                change['new_user'], change['new_group'])
            else:
                raise Exception('Unknown change type: {}'.format(
                    change['type']))

        model.addItem((path,
                       DiffData(file_type=file_type,
                                change_type=change_type,
                                size=size,
                                mode_change=mode_change,
                                owner_change=owner_change,
                                modified=modified)))


# re pattern
pattern_ar = r'(?P<a_r>added|removed) (?P<ar_type>directory|link|\s+(?P<size>\d+) (?P<size_unit>\w+))\s*'
pattern_cl = r'changed link\s*'
pattern_modified = r'\s*\+?(?P<added>[\d.]+) (?P<added_unit>\w+)\s*-?(?P<removed>[\d.]+) (?P<removed_unit>\w+)'
pattern_mode = r'\[(?P<old_mode>[\w-]{10}) -> (?P<new_mode>[\w-]{10})\]'
pattern_owner = r'\[(?P<old_user>[\w ]+):(?P<old_group>[\w ]+) -> (?P<new_user>[\w ]+):(?P<new_group>[\w ]+)\]'
pattern_path = r'(?P<path>.*)'
pattern_changed_file = (
    r'(({ar} )|((?P<cl>{cl} )|' +
    r'((?P<modified>{modified}\s+)?)(?P<owner>{owner}\s+)?(?P<mode>{mode}\s+)?))'
    + r'{path}').format(ar=pattern_ar,
                        cl=pattern_cl,
                        modified=pattern_modified,
                        mode=pattern_mode,
                        owner=pattern_owner,
                        path=pattern_path)
re_changed_file = re.compile(pattern_changed_file)


def parse_diff_lines(lines: List[str], model: 'DiffTree'):
    """
    Parse non-json diff output from borg.

    ::

        [-rw-rw-r-- -> lrwxrwxrwx] home/theuser/Documents/testdir/file2
        [-rw-rw-r-- -> drwxr-xr-x] home/theuser/Documents/testdir/file3
            +32 B     -36 B [-r--rw---- -> -rwxrwx--x] home/theuser/Documents/testfile.txt
        [drwxrwxr-x -> lrwxrwxrwx] home/theuser/Documents/testlink
        added directory     home/theuser/Documents/newfolder
        removed         0 B home/theuser/Documents/testdir/file1
        added          20 B home/theuser/Documents/testdir/file4
        changed link        home/theuser/Documents/testlink
        changed link [theuser:dip -> theuser:theuser] home/theuser/Documents/testlink

    Notes
    -----
    This method can't handle changes of type `modified` that do not provide
    the amount of `added` and `removed` bytes.

    """
    for line in lines:
        if not line:
            continue

        parsed_line = re_changed_file.fullmatch(line)

        if not parsed_line:
            raise Exception("Couldn't parse diff output `{}`".format(line))
            continue

        path = PurePath(parsed_line['path'])
        file_type = FileType.FILE
        size = 0
        change_type: ChangeType = None
        mode_change: Optional[Tuple[str, str]] = None
        owner_change: Optional[Tuple[str, str, str, str]] = None
        modified: Optional[Tuple[int, int]] = None

        if parsed_line['a_r']:
            # added or removed
            if parsed_line['ar_type']:
                if parsed_line['ar_type'] == 'directory':
                    file_type = FileType.DIRECTORY
                elif parsed_line['ar_type'] == 'link':
                    file_type = FileType.LINK
            else:
                # normal file
                size = size_to_byte(parsed_line['size'],
                                    parsed_line['size_unit'])

            if parsed_line['a_r'] == 'added':
                change_type = ChangeType.ADDED
            elif parsed_line['a_r'] == 'removed':
                change_type = ChangeType.REMOVED
                size = -size

        else:
            change_type = ChangeType.MODIFIED

            if parsed_line['owner']:
                # owner changed
                owner_change = (parsed_line['old_user'],
                                parsed_line['old_group'],
                                parsed_line['new_user'],
                                parsed_line['new_group'])

            if parsed_line['cl']:
                # link changed
                # links can't have changed permissions
                change_type = ChangeType.CHANGED_LINK
                file_type = FileType.LINK
            else:
                # modified contents or mode
                if parsed_line['modified']:
                    modified = (size_to_byte(parsed_line['added'],
                                             parsed_line['added_unit']),
                                size_to_byte(parsed_line['removed'],
                                             parsed_line['removed_unit']))

                    size = modified[0] - modified[1]

                if parsed_line['mode']:
                    mode_change = (parsed_line['old_mode'],
                                   parsed_line['new_mode'])

        # add change to model
        model.addItem((path,
                       DiffData(file_type=file_type,
                                change_type=change_type,
                                size=size,
                                mode_change=mode_change,
                                owner_change=owner_change,
                                modified=modified)))


def size_to_byte(significand: str, unit: str) -> int:
    """Convert a size with a unit identifier from borg into a number of bytes."""
    if unit == 'B':
        return int(significand)
    elif unit == 'kB' or unit == 'KB':
        return int(float(significand) * 10**3)
    elif unit == 'MB':
        return int(float(significand) * 10**6)
    elif unit == 'GB':
        return int(float(significand) * 10**9)
    elif unit == 'TB':
        return int(float(significand) * 10**12)
    else:
        # unknown identifier
        raise Exception("Unknown unit `{}`".format(unit))


# ---- Sorting ---------------------------------------------------------------


class DiffSortProxyModel(QSortFilterProxyModel):
    """
    Sort a FileTreeModel.
    """
    sorted = pyqtSignal(int, Qt.SortOrder)

    def __init__(self, parent) -> None:
        """Init."""
        super().__init__(parent)
        self.folders_on_top = False
        self._sort_order: Qt.SortOrder = None

    def keepFoldersOnTop(self, value: bool = None) -> bool:
        """
        Set or get whether folders are kept on top when sorting.

        Parameters
        ----------
        value : bool, optional
            The new value, by default None

        Returns
        -------
        bool
            The value of the attribute.
        """
        if value:
            self.folders_on_top = value
            # resort
            self.sort(self.sortColumn(), self.sortOrder())

        return self.folders_on_top

    def choose_data(self, index: QModelIndex):
        """Choose the data of index used for comparison."""
        item = index.internalPointer()
        model = cast(DiffTree, self.sourceModel())

        if index.column() == 0:
            # name
            if model.mode == FileTreeModel.DisplayMode.FLAT:
                return item.path

            if model.mode == FileTreeModel.DisplayMode.SIMPLIFIED_TREE:
                parent = index.parent()
                if parent == QModelIndex():
                    path: PurePath = item.path.relative_to(model.root.path)
                path = item.path.relative_to(parent.internalPointer().path)
                return path.parts[0]

            # standard tree mode
            return item.subpath
        elif index.column() == 1:
            # change type
            ct = item.data.change_type
            if ct == ChangeType.NONE:
                return ChangeType.MODIFIED
            return ct
        else:
            # size
            return item.data.size

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        """
        Return whether the item of `left` is lower than the one of `right`.
        Parameters
        ----------
        left : QModelIndex
            The index left of the `<`.
        right : QModelIndex
            The index right of the `<`.
        Returns
        -------
        bool
            Whether left is lower than right.
        """

        if self.folders_on_top:
            item1 = left.internalPointer()
            item2 = right.internalPointer()
            ch1 = bool(len(item1.children))
            ch2 = bool(len(item2.children))

            if ch1 ^ ch2:
                if self._sort_order == Qt.SortOrder.AscendingOrder:
                    return ch1
                return ch2

        data1 = self.choose_data(left)
        data2 = self.choose_data(right)
        return data1 < data2

    def sort(self,
             column: int,
             order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        """
        Sorts the model by column in the given order.
        """
        self._sort_order = order
        super().sort(column, order)
        self.sorted.emit(column, order)


# ---- DiffTree --------------------------------------------------------------


class ChangeType(enum.Enum):
    """
    The possible types of changes from `borg diff`.

    modified - file contents changed.
    added - the file was added.
    removed - the file was removed.
    added directory - the directory was added.
    removed directory - the directory was removed.
    added link - the symlink was added.
    removed link - the symlink was removed.
    changed link - the symlink target was changed.
    mode - the file/directory/link mode was changed.
            Note: this could indicate a change from a file/directory/link
                    type to a different type (file/directory/link),
                    such as - a file is deleted and replaced with
                    a directory of the same name.
    owner - user and/or group ownership changed.

    size:
        If type == `added` or `removed`,
        then size provides the size of the added or removed file.
    added:
        If type == `modified` and chunk ids can be compared,
        then added and removed indicate the amount of
        data `added` and `removed`. If chunk ids can not be compared,
        then added and removed properties are not provided and
        the only information available is that the file contents were modified.
    removed:
        See added property.
    old_mode:
        If type == `mode`, then old_mode and new_mode provide the mode
        and permissions changes.
    new_mode:
        See old_mode property.
    old_user:
        If type == `owner`, then old_user, new_user, old_group
        and new_group provide the user and group ownership changes.
    old_group:
        See old_user property.
    new_user:
        See old_user property.
    new_group:
        See old_user property.
    """
    NONE = 0  # no change
    MODIFIED = 2  # int for sorting
    ADDED = 1
    REMOVED = 3
    ADDED_DIR = ADDED
    REMOVED_DIR = REMOVED
    ADDED_LINK = ADDED
    REMOVED_LINK = REMOVED
    CHANGED_LINK = MODIFIED
    MODE = MODIFIED  # changed permissions
    OWNER = MODIFIED

    def short(self):
        """Get a short identifier for the change type."""
        if self == self.ADDED:
            return 'A'
        if self == self.REMOVED:
            return 'D'
        if self == self.MODIFIED:
            return 'M'
        return ''

    def __ge__(self, other):
        """Greater than or equal for enums."""
        if self.__class__ is other.__class__:
            return other >= self
        return NotImplemented

    def __gt__(self, other):
        """Greater than for enums."""
        if self.__class__ is other.__class__:
            return other < self
        return NotImplemented

    def __le__(self, other):
        """Lower than or equal for enums."""
        if self.__class__ is other.__class__:
            return self.value == other.value or self < other
        return NotImplemented

    def __lt__(self, other):
        """Lower than for enums."""
        if self.__class__ is other.__class__:
            return self.value < other.value
        return NotImplemented


class FileType(enum.Enum):
    """The possible file types of changed file."""
    FILE = enum.auto()
    DIRECTORY = enum.auto()
    LINK = enum.auto()


@dataclass
class DiffData:
    """The data linked to a diff item."""
    file_type: FileType
    change_type: ChangeType
    size: int
    mode_change: Optional[Tuple[str, str]] = None
    owner_change: Optional[Tuple[str, str, str, str]] = None
    modified: Optional[Tuple[int, int]] = None


class DiffTree(FileTreeModel[DiffData]):
    """The file tree model for diff results."""
    def tr(self, *args, **kwargs):
        """Translate a GUI string."""
        scope = self.__class__.__name__
        return translate(scope, *args, **kwargs)

    def _make_filesystemitem(self, path, data):
        return super()._make_filesystemitem(path, data)

    def _merge_data(self, item, data):
        if data:
            logger.debug('Overriding data for {}'.format(item.path))
        return super()._merge_data(item, data)

    def _flat_filter(self, item):
        """
        Return whether an item is part of the flat model representation.

        The item's data might have not been set yet.
        """
        return item.data and item.data.change_type != ChangeType.NONE

    def _simplify_filter(self, item: FileSystemItem[DiffData]) -> bool:
        """
        Return whether an item may be merged in simplified mode.

        Allows simplification only for unchanged items.
        """
        if not item.data:
            return True

        if item.data.change_type == ChangeType.NONE:
            return True

        return False  # otherwise the change is not displayed

    def _process_child(self, child):
        """
        Process a new child.

        This can make some changes to the child's data like
        setting a default value if the child's data is None.
        This can also update the data of the parent.
        This must emit `dataChanged` if data is changed.

        Parameters
        ----------
        child : FileSystemItem
            The child that was added.
        """
        parent = child._parent

        if not child.data:
            child.data = DiffData(FileType.DIRECTORY, ChangeType.NONE, 0)

        if child.data.size != 0:
            # update size
            size = child.data.size

            def add_size(parent):
                if parent is self.root:
                    return

                if parent.data is None:
                    raise Exception("Item {} without data".format(parent.path))
                else:
                    parent.data.size += size

                # emit data change signal
                index = self.indexPath(parent.path)
                self.dataChanged.emit(index, index)

                # update parent
                parent = parent._parent
                if parent:
                    add_size(parent)

            add_size(parent)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """
        Returns the number of columns for the children of the given parent.

        This corresponds to the number of data (column) entries shown
        for each item in the tree view.

        Parameters
        ----------
        parent : QModelIndex, optional
            The index of the parent, by default QModelIndex()

        Returns
        -------
        int
            The number of rows.
        """
        # name, change_type, size
        return 3

    def headerData(self,
                   section: int,
                   orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole):
        """
        Get the data for the given role and section in the given header.

        The header is identified by its orientation.
        For horizontal headers, the section number corresponds to
        the column number. Similarly, for vertical headers,
        the section number corresponds to the row number.

        Parameters
        ----------
        section : int
            The row or column number.
        orientation : Qt.Orientation
            The orientation of the header.
        role : int, optional
            The data role, by default Qt.ItemDataRole.DisplayRole

        Returns
        -------Improve
        Any
            The data for the specified header section.
        """
        if (orientation == Qt.Orientation.Horizontal
                and role == Qt.ItemDataRole.DisplayRole):
            if section == 0:
                return self.tr("Name")
            elif section == 1:
                return self.tr("Change")
            elif section == 2:
                return self.tr("Size")

        return None

    def data(self,
             index: QModelIndex,
             role: int = Qt.ItemDataRole.DisplayRole):
        """
        Get the data for the given role and index.

        The indexes internal pointer references the corresponding
        `FileSystemItem`.

        Parameters
        ----------
        index : QModelIndex
            The index of the item.
        role : int, optional
            The data role, by default Qt.ItemDataRole.DisplayRole

        Returns
        -------
        Any
            The data, return None if no data is available for the role.
        """
        if not index.isValid():
            return None

        item: FileSystemItem[DiffData] = index.internalPointer()
        column = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if column == 0:
                # name
                if self.mode == self.DisplayMode.FLAT:
                    return str(item.path)

                if self.mode == self.DisplayMode.SIMPLIFIED_TREE:
                    parent = index.parent()
                    if parent == QModelIndex():
                        return str(item.path.relative_to(self.root.path))
                    return str(
                        item.path.relative_to(parent.internalPointer().path))

                # standard tree mode
                return item.subpath
            elif column == 1:
                # change type
                return item.data.change_type.short()
            else:
                # size
                return pretty_bytes(item.data.size)

        if role == Qt.ItemDataRole.ForegroundRole:
            # colour
            if item.data.change_type == ChangeType.ADDED:
                return (QColor(Qt.green)
                        if uses_dark_mode() else QColor(Qt.darkGreen))
            if item.data.change_type == ChangeType.MODIFIED:
                return (QColor(Qt.yellow)
                        if uses_dark_mode() else QColor(Qt.darkYellow))
            if item.data.change_type == ChangeType.REMOVED:
                return (QColor(Qt.red)
                        if uses_dark_mode() else QColor(Qt.darkRed))
            return None  # no change

        if role == Qt.ItemDataRole.ToolTipRole:
            if column == 0:
                # name column -> display fullpath
                return str(item.path)

            # info/data tooltip -> no real size limitation
            tooltip_template = \
                "{name}\n" + \
                "\n" + \
                "{filetype} {changetype}"

            modified_template = self.tr("Added {}, deleted {}")
            owner_template = "{: <10} -> {: >10}"
            permission_template = "{} -> {}"

            # format
            if item.data.file_type == FileType.FILE:
                filetype = self.tr("File")
            elif item.data.file_type == FileType.DIRECTORY:
                filetype = self.tr("Directory")
            elif item.data.file_type == FileType.LINK:
                filetype = self.tr("Link")
            else:
                raise Exception("Unknown filetype {}".format(
                    item.data.file_type))

            if item.data.change_type == ChangeType.NONE:
                changetype = self.tr("unchanged")
            elif item.data.change_type == ChangeType.MODIFIED:
                changetype = self.tr("modified")
            elif item.data.change_type == ChangeType.REMOVED:
                changetype = self.tr("removed")
            elif item.data.change_type == ChangeType.ADDED:
                changetype = self.tr("added")
            else:
                raise Exception("Unknown changetype {}".format(
                    item.data.change_type))

            tooltip = tooltip_template.format(name=item.path.name,
                                              filetype=filetype,
                                              changetype=changetype)
            if item.data.modified:
                tooltip += '\n'
                tooltip += modified_template.format(
                    pretty_bytes(item.data.modified[0]),
                    pretty_bytes(item.data.modified[1]))

            if item.data.mode_change:
                tooltip += '\n'
                tooltip += permission_template.format(*item.data.mode_change)

            if item.data.owner_change:
                tooltip += '\n'
                tooltip += owner_template.format(
                    '{}:{}'.format(item.data.owner_change[0],
                                   item.data.owner_change[1]),
                    "{}:{}".format(item.data.owner_change[2],
                                   item.data.owner_change[3]))

            return tooltip

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        """
        Returns the item flags for the given index.

        The base class implementation returns a combination of flags
        that enables the item (ItemIsEnabled) and
        allows it to be selected (ItemIsSelectable).

        Parameters
        ----------
        index : QModelIndex
            The index.

        Returns
        -------
        Qt.ItemFlags
            The flags.
        """
        return super().flags(index)
