"""
Implementation of a tree model for use with `QTreeView` based on (file) paths.

"""

import enum
from functools import reduce
from pathlib import PurePath
from typing import Generic, List, Optional, Tuple, TypeVar, Union, overload

from PyQt5.QtCore import (QAbstractItemModel, QModelIndex, QObject,
                          QPersistentModelIndex, Qt)

#: Type of FileSystemItem's data
T = TypeVar('T')
FileSystemItemLike = Union[Tuple[PurePath, Optional[T]], 'FileSystemItem']


class FileSystemItem(Generic[T]):
    """
    An item in the virtual file system.

    ..warning::

        Do not edit `children` manually. Always use `add` or `remove` or
        `sort`.

    Attributes
    ----------
    path : PurePath
        The path of this item.
    data : Any
        The data belonging to this item.
    children : List[FileSystemItem]
        The children of this item.
    _subpath : str
        The subpath of this item relative to its parent.
    _parent : FileSystemItem or None
        The parent of the item.
    """
    def __init__(self, path: PurePath, data: T, subpath: str = ''):
        """Init."""
        self.path = path
        self.data = data
        self.children: List[FileSystemItem[T]] = []
        self.subpath = subpath
        self._parent: Optional[FileSystemItem[T]] = None

    def add(self,
            child: 'FileSystemItem[T]',
            _subpath: str = None,
            _check: bool = True):
        """
        Add a child.

        The parameters starting with an underscore exist for performance
        reasons only. They should only be used if the operations that these
        parameters toggle were performed already.

        Parameters
        ----------
        child : FileSystemItem
            The child to add.
        _subpath : str, optional
            Precalculated subpath, default is None.
        _check : bool, optional
            Whether to check for children with the same subpath (using `get`).
        """
        child._parent = self

        if _subpath is not None:
            child.subpath = _subpath
        else:
            _subpath = str(child.path.relative_to(self.path).parts[0])
            child.subpath = _subpath

        # check for a child with the same subpath
        if _check and self.get(_subpath):
            raise RuntimeError(
                "The subpath must be unique to a parent's children.")

        # add to exposed list
        self.children.append(child)

    def addChildren(self, children: List['FileSystemItem[T]']):
        """
        Add a list of children.

        Parameters
        ----------
        children : List[FileSystemItem]
            The children to add.
        """
        for child in children:
            self.add(child)

    @overload
    def remove(self, index: int) -> None:
        pass

    @overload
    def remove(self, child: 'FileSystemItem[T]') -> None:
        pass

    def remove(self, child_or_index):
        """
        Remove the given children.

        The index or child to remove must be in the list
        else an error will be raised.

        Parameters
        ----------
        child_or_index : FileSystemItem or int
            The instance to remove or its index in `children`.

        Raises
        ------
        ValueError
            The given item is not a child of this one.
        IndexError
            The given index is not a valid one.
        """
        if isinstance(child_or_index, FileSystemItem):
            child = child_or_index
            self.children.remove(child)

        elif isinstance(child_or_index, int):
            index = child_or_index
            del self.children[index]

        else:
            raise TypeError(
                "First argument passed to `{}.remove` has invalid type {}".
                format(type(self).__name__,
                       type(child_or_index).__name__))

    def __getitem__(self, index: int):
        """
        Get a an item.

        This allows accessing the attributes in the same manner for instances
        of this type and instances of `FileSystemItemLike`.
        """
        if index == 0:
            return self.path
        elif index == 1:
            return self.data
        elif index == 2:
            return self.children
        else:
            raise IndexError("Index {} out of range(0, 3)".format(index))

    def get(self,
            subpath: str,
            default=None) -> Optional[Tuple[int, 'FileSystemItem[T]']]:
        """
        Find direct child with given subpath.

        Parameters
        ----------
        subpath : str
            The items subpath relative to this.
        default : Any, optional
            The item to return if the child wasn't found, default None.

        Returns
        -------
        Tuple[int, FileSystemItem] or None
            The index and item if found else `default`.
        """
        for i, child in enumerate(self.children):
            if child.subpath == subpath:
                return i, child

        return default

    def get_subpath(self, path: PurePath) -> Optional['FileSystemItem[T]']:
        """
        Get the item with the given subpath relative to this item.

        Parameters
        ----------
        path : PurePath
            The subpath.
        """
        def walk(fsi, pp):
            if fsi is None:
                return None
            res = fsi.get(pp)
            if not res:
                return None
            return res[1]

        fsi = reduce(walk, path.parts, self)
        return fsi

    def __repr__(self):
        """Get a string representation."""
        return "FileSystemItem<'{}', '{}', {}, {}>".format(
            self.path,
            self.subpath,
            self.data,
            [c.subpath for c in self.children],
        )


class FileTreeModel(QAbstractItemModel, Generic[T]):
    """
    FileTreeModel managing a virtual file system with variable file data.

    Attributes
    ----------
    mode: DisplayMode
        The tree display mode of the model.

    Methods
    -------
    _make_filesystemitem(path, data, children)
        Construct a `FileSystemItem`.
    _merge_data(item, data)
        Add the given data to the item.
    _flat_filter
        Return whether an item is part of the flat model representation.
    flags
    columnCount
    headerData

    """
    class DisplayMode(enum.Enum):
        """
        The tree display modes available for the model.

        The default `TREE` mode uses the fewest resources when adding items.
        """
        #: normal file tree
        TREE = enum.auto()

        #: combine items in the tree having a single child with that child
        SIMPLIFIED_TREE = enum.auto()

        #: simple list of items
        FLAT = enum.auto()

    def __init__(self, parent=None):
        """Init."""
        super().__init__(parent)
        self.root: FileSystemItem[T] = FileSystemItem(PurePath(''), None, [])

        #: mode
        self.mode: 'FileTreeModel.DisplayMode' = self.DisplayMode.TREE

        #: flat representation of the tree
        self._flattened: List[FileSystemItem] = []

    def addItems(self, items: List[FileSystemItemLike[T]]):
        """
        Add file system items to the model.

        This method can be used for populating the model.
        Calls `addItem` for each item.

        Parameters
        ----------
        items : List[FileSystemItemLike]
            The items.
        """
        for item in items:
            self.addItem(item)

    def addItem(self, item: FileSystemItemLike[T]):
        """
        Add a file system item to the model.

        Parameters
        ----------
        item : FileSystemItemLike
            The item.
        """
        path = item[0]
        data = item[1]

        pparts = path.parts

        fsi = reduce(
            lambda fsi, pp: self._addChild(fsi, PurePath(*pparts[:pp[0] + 1]
                                                         ), pp[1], None),
            enumerate(pparts[:-1]), self.root)

        self._addChild(fsi, path, pparts[-1], data)

    def _addChild(self, item: FileSystemItem[T], path: PurePath,
                  path_part: str, data: Optional[T]) -> FileSystemItem[T]:
        """
        Add a child to an item.

        This is called by `addItem` in a reduce statement. It should
        add a new child with the given attributes to the given item.
        This implementation provides a reasonable default, most subclasses
        wont need to override this method. The implementation should make use
        of `_make_filesystemitem`, `_merge_data`, `_add_children`.

        Parameters
        ----------
        item : FileSystemItem
            The item to add a new child to.
        path : PurePath
            The path of the new child.
        path_part : str
            The subpath of the new child relative to `item`.
        data : Any or None
            The data of the new child.
        children : Any or None
            The initial children of the item.

        Returns
        -------
        FileSystemItem
            [description]
        """
        res = item.get(path_part)
        if res:
            i, child = res
            self._merge_data(child, data)
        else:
            child = self._make_filesystemitem(path, data)

            # different behavior in flat and tree mode
            if self.mode == self.DisplayMode.FLAT:
                if self._flat_filter(child):
                    i = len(self._flattened)
                    self.beginInsertRows(QModelIndex(), i, i)
                    self._flattened.append(child)
                    self.endInsertRows()

                item.add(child, _subpath=path_part, _check=False)

            else:
                i = len(item.children)
                index = self.indexPath(item.path)

                if self.mode == self.DisplayMode.SIMPLIFIED_TREE and len(
                        item.children) == 1:
                    # invisible because combined with child
                    from_list = [
                        self.index(0, c, index)
                        for c in range(self.columnCount(index))
                    ]

                    persistent_index = QPersistentModelIndex(index)
                    self.layoutAboutToBeChanged.emit([persistent_index])

                    item.add(child, _subpath=path_part, _check=False)

                    new_parent_index = self.index(0, 0, index)
                    to_list = [
                        self.index(0, c, new_parent_index)
                        for c in range(self.columnCount(new_parent_index))
                    ]
                    self.changePersistentIndexList(from_list, to_list)

                    self.layoutChanged.emit([persistent_index])
                else:
                    self.beginInsertRows(index, i, i)
                    item.add(child, _subpath=path_part, _check=False)
                    self.endInsertRows()

                if self._flat_filter(child):
                    self._flattened.append(child)

            # update parent data
            self._process_child(child)

        return child

    def _make_filesystemitem(self, path: PurePath,
                             data: Optional[T]) -> FileSystemItem[T]:
        """
        Construct a `FileSystemItem`.

        The attributes are the attributes of a `FileSystemItemLike`.
        This implementation already provides reasonable default that
        subclasses can be used.

        ..warning:: Do always call `_addChild` to add a child to an item.

        Parameters
        ----------
        path : PurePath
            The path of the item.
        data : Any or None
            The data.
        children : Any or None
            The initial children.

        Returns
        -------
        FileSystemItem
            The FileSystemItem for the internal tree structure.
        """
        return FileSystemItem(path, data)

    def _process_child(self, child: FileSystemItem[T]):
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
        pass  # Does nothing

    def _merge_data(self, item: FileSystemItem[T], data: Optional[T]):
        """
        Add the given data to the item.

        This method is called by `_addChild` which in turn is called by
        `addItem`. It gets an item in the virtual file system that was
        added again with the given data. The data may be None.

        Must emit `dataChanged` if data is changed.

        Parameters
        ----------
        item : FileSystemItem
            The item to merge the data in.
        data : Any or None
            The data to add.
        """
        if not item.data:
            item.data = data
            index = self.indexPath(item.path)
            self.dataChanged.emit(index, index)

    def removeItem(self, path: PurePath) -> None:
        """
        Remove an item from the model.

        Parameters
        ----------
        path : PurePath
            The path of the item to remove.
        """
        parent = self.getItem(path.parent)

        if not parent:
            return

        res = parent.get(path.name)

        if not res:
            return

        i, item = res

        # if item in self._flattened:
        #     fi = self._flattened.index(item)
        fi = self._flattened.index(item)

        # different behavior in flat and tree mode
        if self.mode == self.DisplayMode.FLAT:

            for child in item.children:
                fci = self._flattened.index(child)
                self.beginRemoveRows(QModelIndex(), fci, fci)
                del self._flattened[fci]
                self.endRemoveRows()

            # remove item
            self.beginRemoveRows(QModelIndex(), fi, fi)
            parent.remove(i)
            del self._flattened[fi]
            self.endRemoveRows()

        else:  # same for tree and simplified tree
            parent_index = self.indexPath(path.parent)

            self.beginRemoveRows(parent_index, i, i)

            # remove children in flat representation
            for child in item.children:
                self._flattened.remove(child)

            # remove item
            parent.remove(i)
            del self._flattened[fi]

            self.endRemoveRows()

    def setMode(self, value: 'DisplayMode'):
        """
        Set the display mode of the tree model.

        In TREE mode (default) the tree will be displayed as is.
        In SIMPLIFIED_TREE items will simplify the tree by combining
        items with their single child if they posess only one.
        In FLAT mode items will be displayed as a simple list. The items
        shown can be filtered by `_flat_filter`.

        Parameters
        ----------
        value : bool
            The new value for the attribute.

        See also
        --------
        getMode: Get the current mode.
        _flat_filter
        """
        if value == self.mode:
            return  # nothing to do

        self.beginResetModel()
        self.mode = value
        self.endResetModel()

    def getMode(self) -> bool:
        """
        Get the display mode set.

        Returns
        -------
        DisplayMode
            The current value.

        See also
        --------
        setMode : Set the mode.
        """
        return self.mode

    def _flat_filter(self, item: FileSystemItem[T]) -> bool:
        """
        Return whether an item is part of the flat model representation.
        """
        return True

    def _simplify_filter(self, item: FileSystemItem[T]) -> bool:
        """
        Return whether an item may be merged in simplified mode.
        """
        return True

    def getItem(self, path: PurePath) -> Optional[FileSystemItem[T]]:
        """
        Get the item with the given path.

        Parameters
        ----------
        path : PurePath
            The path of the item.

        Returns
        -------
        Optional[FileSystemItem]
            [description]
        """
        return self.root.get_subpath(path)

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
        return super().data(index, role)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """
        Returns the number of rows under the given parent.

        When the parent is valid it means that rowCount is returning
        the number of children of parent.

        Parameters
        ----------
        parent : QModelIndex, optional
            The index of the parent item, by default QModelIndex()

        Returns
        -------
        int
            The number of children.
        """
        if parent.column() > 0:
            return 0  # Only the first column has children

        # flat mode
        if self.mode == self.DisplayMode.FLAT:
            if not parent.isValid():
                return len(self._flattened)
            return 0

        # tree mode
        if not parent.isValid():
            parent_item: FileSystemItem = self.root
        else:
            parent_item = parent.internalPointer()

        return len(parent_item.children)

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
        raise NotImplementedError("Method `columnCount` of FileTreeModel" +
                                  " must be implemented by subclasses.")

    def indexPath(self, path: PurePath) -> QModelIndex:
        """
        Construct a `QModelIndex` for the given path.

        If `combine` is enabled, the closest indexed parent path is returned.

        Parameters
        ----------
        path : PurePath
            The path to the item the index should point to.

        Returns
        -------
        QModelIndex
            The requested index.
        """
        # flat mode
        if self.mode == self.DisplayMode.FLAT:
            for i, item in enumerate(self._flattened):
                if item.path == path:
                    return self.index(i, 0)
            return QModelIndex()

        # tree mode
        simplified = self.mode == self.DisplayMode.SIMPLIFIED_TREE

        def step(index, i, item, subpath):
            if not item:
                return index, None

            res = item.get(subpath)

            if not res:
                return QModelIndex(), None

            r, item = res

            if i <= -1:
                i = r

            if (simplified and len(item.children) == 1
                    and self._simplify_filter(item)):
                return index, i, item

            index = self.index(i if simplified else r, 0, index)

            return index, -1, item

        index, i, item = reduce(lambda t, p: step(*t, p), path.parts,
                                (QModelIndex(), -1, self.root))

        return index

    def index(self, row: int, column: int,
              parent: QModelIndex = QModelIndex()) -> QModelIndex:
        """
        Construct a `QModelIndex`.

        Returns the index of the item in the model specified by
        the given row, column and parent index.

        Parameters
        ----------
        row : int
        column : int
        parent : QModelIndex, optional

        Returns
        -------
        QModelIndex
            The requested index.
        """
        # different behavior in flat and treemode
        if self.mode == self.DisplayMode.FLAT:
            if (0 <= row < len(self._flattened)
                    and 0 <= column < self.columnCount(parent)):
                return self.createIndex(row, column, self._flattened[row])

            return QModelIndex()

        # valid index?
        if not parent.isValid():
            parent_item: FileSystemItem[T] = self.root
        else:
            parent_item = parent.internalPointer()

        item = parent_item.children[row]

        if self.mode == self.DisplayMode.SIMPLIFIED_TREE:
            # combine items with a single child with that child
            while len(item.children) == 1 and self._simplify_filter(item):
                item = item.children[0]

        if (0 <= row < len(parent_item.children)
                and 0 <= column < self.columnCount(parent)):
            return self.createIndex(row, column, item)

        return QModelIndex()

    @overload
    def parent(self, child: QModelIndex) -> QModelIndex:
        pass

    @overload
    def parent(self) -> QObject:
        pass

    def parent(self, child=None):
        """
        Returns the parent of the model item with the given index.

        If the item has no parent, an invalid QModelIndex is returned.
        A common convention used in models that expose tree data structures
        is that only items in the first column have children.
        For that case, when reimplementing this function in a subclass
        the column of the returned QModelIndex would be 0.

        Parameters
        ----------
        child : QModelIndex
            The index of the child item.

        Returns
        -------
        QModelIndex
            The index of the parent item.
        """
        # overloaded variant to retrieve parent of model
        if child is None:
            return super().parent()

        # variant to retrieve parent for data item
        if not child.isValid():
            return QModelIndex()

        # different behavior in tree and flat mode
        if self.mode == self.DisplayMode.FLAT:
            return QModelIndex()  # in flat mode their are no parents

        child_item: FileSystemItem[T] = child.internalPointer()
        parent_item = child_item._parent

        if self.mode == self.DisplayMode.SIMPLIFIED_TREE:
            # combine items with a single child with the child
            while (len(parent_item.children) == 1
                   and self._simplify_filter(parent_item)):
                parent_item = parent_item._parent
                if parent_item is self.root:
                    break

        if parent_item is self.root:
            # Never return root item since it shouldn't be displayed
            return QModelIndex()

        row = parent_item._parent.children.index(parent_item)
        return self.createIndex(row, 0, parent_item)

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
        -------
        Any
            The data for the specified header section.
        """
        return super().headerData(section, orientation, role)
