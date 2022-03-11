from pathlib import PurePath

import pytest

from vorta.views.diff_result import (ChangeType, DiffData, DiffTree, FileType,
                                     parse_diff_json, parse_diff_lines)


@pytest.mark.parametrize(
    'line, expected',
    [
        ('changed link        some/changed/link',
         ('some/changed/link', FileType.LINK, ChangeType.CHANGED_LINK, 0, None,
          None, None)),
        (' +77.8 kB  -77.8 kB some/changed/file',
         ('some/changed/file', FileType.FILE, ChangeType.MODIFIED, 0, None, None,
          (77800, 77800))),
        (' +77.8 kB  -77.8 kB [-rw-rw-rw- -> -rw-r--r--] some/changed/file',
         ('some/changed/file', FileType.FILE, ChangeType.MODIFIED, 0,
          ('-rw-rw-rw-', '-rw-r--r--'), None, (77800, 77800))),
        ('[-rw-rw-rw- -> -rw-r--r--] some/changed/file',
         ('some/changed/file', FileType.FILE, ChangeType.MODE, 0,
          ('-rw-rw-rw-', '-rw-r--r--'), None, None)),
        ('added directory    some/changed/dir',
         ('some/changed/dir', FileType.DIRECTORY, ChangeType.ADDED, 0, None,
          None, None)),
        ('removed directory  some/changed/dir',
         ('some/changed/dir', FileType.DIRECTORY, ChangeType.REMOVED_DIR, 0,
          None, None, None)),

        # Example from https://github.com/borgbase/vorta/issues/521
        ('[user:user -> nfsnobody:nfsnobody] home/user/arrays/test.txt',
         ('home/user/arrays/test.txt', FileType.FILE, ChangeType.OWNER, 0,
          None, ('user', 'user', 'nfsnobody', 'nfsnobody'), None)),

        # Very short owner change, to check stripping whitespace from file path
        ('[a:a -> b:b]       home/user/arrays/test.txt',
         ('home/user/arrays/test.txt', FileType.FILE, ChangeType.OWNER, 0,
          None, ('a', 'a', 'b', 'b'), None)),

        # All file-related changes in one test
        (' +77.8 kB  -77.8 kB [user:user -> nfsnobody:nfsnobody] [-rw-rw-rw- -> -rw-r--r--] home/user/arrays/test.txt',
         ('home/user/arrays/test.txt', FileType.FILE, ChangeType.OWNER, 0,
          ('-rw-rw-rw-', '-rw-r--r--'),
          ('user', 'user', 'nfsnobody', 'nfsnobody'), (77800, 77800))),
    ])
def test_archive_diff_parser(line, expected):
    model = DiffTree()
    model.setMode(model.DisplayMode.FLAT)
    parse_diff_lines([line], model)

    assert model.rowCount() == 1
    item = model.index(0, 0).internalPointer()

    assert item.path == PurePath(expected[0])
    assert item.data == DiffData(*expected[1:])


@pytest.mark.parametrize(
    'line, expected',
    [
        ({
            'path': 'some/changed/link',
            'changes': [{
                'type': 'changed link'
            }]
        }, ('some/changed/link', FileType.LINK, ChangeType.CHANGED_LINK, 0,
            None, None, None)),
        ({
            'path': 'some/changed/file',
            'changes': [{
                'type': 'modified',
                'added': 77800,
                'removed': 77800
            }]
        }, ('some/changed/file', FileType.FILE, ChangeType.MODIFIED, 0, None, None,
            (77800, 77800))),
        ({
            'path':
            'some/changed/file',
            'changes': [{
                'type': 'modified',
                'added': 77800,
                'removed': 77800
            }, {
                'type': 'mode',
                'old_mode': '-rw-rw-rw-',
                'new_mode': '-rw-r--r--'
            }]
        }, ('some/changed/file', FileType.FILE, ChangeType.MODIFIED, 0,
            ('-rw-rw-rw-', '-rw-r--r--'), None, (77800, 77800))),
        ({
            'path':
            'some/changed/file',
            'changes': [{
                'type': 'mode',
                'old_mode': '-rw-rw-rw-',
                'new_mode': '-rw-r--r--'
            }]
        }, ('some/changed/file', FileType.FILE, ChangeType.MODE, 0,
            ('-rw-rw-rw-', '-rw-r--r--'), None, None)),
        ({
            'path': 'some/changed/dir',
            'changes': [{
                'type': 'added directory'
            }]
        }, ('some/changed/dir', FileType.DIRECTORY, ChangeType.ADDED, 0, None,
            None, None)),
        ({
            'path': 'some/changed/dir',
            'changes': [{
                'type': 'removed directory'
            }]
        }, ('some/changed/dir', FileType.DIRECTORY, ChangeType.REMOVED_DIR, 0,
            None, None, None)),

        # Example from https://github.com/borgbase/vorta/issues/521
        ({
            'path':
            'home/user/arrays/test.txt',
            'changes': [{
                'type': 'owner',
                'old_user': 'user',
                'new_user': 'nfsnobody',
                'old_group': 'user',
                'new_group': 'nfsnobody'
            }]
        }, ('home/user/arrays/test.txt', FileType.FILE, ChangeType.OWNER, 0,
            None, ('user', 'user', 'nfsnobody', 'nfsnobody'), None)),

        # Very short owner change, to check stripping whitespace from file path
        ({
            'path':
            'home/user/arrays/test.txt',
            'changes': [{
                'type': 'owner',
                'old_user': 'a',
                'new_user': 'b',
                'old_group': 'a',
                'new_group': 'b'
            }]
        }, ('home/user/arrays/test.txt', FileType.FILE, ChangeType.OWNER, 0,
            None, ('a', 'a', 'b', 'b'), None)),

        # All file-related changes in one test
        ({
            'path':
            'home/user/arrays/test.txt',
            'changes': [{
                'type': 'modified',
                'added': 77800,
                'removed': 77800
            }, {
                'type': 'mode',
                'old_mode': '-rw-rw-rw-',
                'new_mode': '-rw-r--r--'
            }, {
                'type': 'owner',
                'old_user': 'user',
                'new_user': 'nfsnobody',
                'old_group': 'user',
                'new_group': 'nfsnobody'
            }]
        }, ('home/user/arrays/test.txt', FileType.FILE, ChangeType.OWNER, 0,
            ('-rw-rw-rw-', '-rw-r--r--'),
            ('user', 'user', 'nfsnobody', 'nfsnobody'), (77800, 77800))),
    ])
def test_archive_diff_json_parser(line, expected):
    model = DiffTree()
    model.setMode(model.DisplayMode.FLAT)
    parse_diff_json([line], model)

    assert model.rowCount() == 1
    item = model.index(0, 0).internalPointer()

    assert item.path == PurePath(expected[0])
    assert item.data == DiffData(*expected[1:])
