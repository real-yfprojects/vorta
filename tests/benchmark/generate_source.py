"""
Generate a source to be backupped.
"""
import os
import random
import shutil
import stat
import sys
from pathlib import Path

from elevate import elevate

ADD_PROBABILITY = 0.2
REMOVE_PROBABILITY = 0.2
DIR_PROBABILITY = 0.1

MODES = [
    stat.S_ISVTX, stat.S_IWUSR, stat.S_IXUSR, stat.S_IRGRP, stat.S_IWGRP,
    stat.S_IXGRP, stat.S_IROTH, stat.S_IWOTH, stat.S_IXOTH
]


def generate_source(directory: Path, number: int, user):
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)

    i = 0

    while i < number:
        filename = "file{}".format(i)
        filepath = directory / filename

        i = generate_file(filepath, i, number, user)


def generate_file(filepath, i, number, user):
    i += 1

    # decide on type
    r = random.random()
    if r < ADD_PROBABILITY:
        # add when changing
        pass
    elif len(filepath.parts) < 5 and r - ADD_PROBABILITY < DIR_PROBABILITY:
        # create dir
        create_file(filepath, user, dir=True)

        while i < i + (number - i) // 2:
            filename = "file{}".format(i)
            i = generate_file(filepath / filename, i, number, user)

    else:
        # create random file
        create_file(filepath, user)

    return i


def create_file(filepath, user, dir=False):
    if not filepath.parent.exists():
        print(filepath, 'doesnt exist')
        return

    groups = [user, 'testgroup']

    if dir:
        filepath.mkdir()
    else:
        # start size
        size = random.randint(0, 30_000)
        with open(filepath, 'wb') as f:
            f.write(bytes([random.getrandbits(8) for i in range(size // 2)]))

    # mode
    mode_flags = stat.S_IRUSR
    for mode in MODES:
        if random.randint(0, 1):
            mode_flags |= mode

    filepath.chmod(mode_flags)

    # user / group
    group = random.choice(groups)
    shutil.chown(filepath, user, group)


if __name__ == "__main__":
    if os.name != "posix":
        sys.exit(1)

    print(sys.argv)
    path = Path(sys.argv[1])
    number = int(sys.argv[2])

    if len(sys.argv) < 4:
        user = os.getlogin()
    else:
        user = sys.argv[3]

    # get root
    elevate(args=sys.argv + [user], graphical=False)

    # generate first source
    generate_source(path, number, user)
