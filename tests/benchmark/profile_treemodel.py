import cProfile
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from peewee import SqliteDatabase
from pkg_resources import parse_version

from vorta.borg.diff import BorgDiffJob
from vorta.borg.version import BorgVersionJob
from vorta.store import connection
from vorta.store.models import BackupProfileModel, RepoModel
from vorta.utils import borg_compat
from vorta.views import diff_result, old_diff_result


def diff_data(profile, a1, a2):
    # non json
    params = BorgDiffJob.prepare(profile, a1, a2)
    params['json_lines'] = False
    if '--json-lines' in params['cmd']:
        params['cmd'].remove('--json-lines')

    job = BorgDiffJob(params['cmd'], params, profile.repo.id)

    def finished(result):
        job.result = result

    job.finished_event = finished

    job.run()
    non_json_lines = [line for line in job.result['data'].split('\n') if line]

    # json
    params = BorgDiffJob.prepare(profile, a1, a2)

    job = BorgDiffJob(params['cmd'], params, profile.repo.id)

    def finished(result):
        job.result = result

    job.finished_event = finished
    job.run()

    fs_data = job.result['data']
    json_lines = [fs_data] if isinstance(fs_data, dict) else \
        [json.loads(line) for line in fs_data.split('\n') if line]

    return non_json_lines, json_lines


def run_old_json(lines):
    attributes, nested_files = old_diff_result.parse_diff_json_lines(lines)
    model = old_diff_result.DiffTree(attributes, nested_files)


def run_old_nojson(lines):
    attributes, nested_files = old_diff_result.parse_diff_lines(lines)
    model = old_diff_result.DiffTree(attributes, nested_files)


def run_json(lines):
    model = diff_result.DiffTree()
    diff_result.parse_diff_json(lines, model)


def run_nojson(lines):
    model = diff_result.DiffTree()
    diff_result.parse_diff_lines(lines, model)


def setup_vorta(repo):
    # setup db
    folder = tempfile.TemporaryDirectory()
    tmp_db = Path(folder.name) / 'settings.sqlite'
    mock_db = SqliteDatabase(str(tmp_db), pragmas={
        'journal_mode': 'wal',
    })
    connection.init_db(mock_db)

    default_profile = BackupProfileModel(name='Default')
    default_profile.save()

    new_repo = RepoModel(url=repo)
    new_repo.encryption = 'none'
    new_repo.save()

    default_profile.repo = new_repo.id
    default_profile.dont_run_on_metered_networks = False
    default_profile.validation_on = False
    default_profile.save()

    from vorta.application import VortaApp
    VortaApp.scheduler = MagicMock()

    def borg_version(self):
        params = BorgVersionJob.prepare()
        if not params['ok']:
            self._alert_missing_borg()
            return
        job = BorgVersionJob(params['cmd'], params)
        job.result.connect(self.set_borg_details_result)
        job.run()

    VortaApp.set_borg_details_action = borg_version

    qapp = VortaApp([])

    return mock_db, qapp, folder


def main(repo, a1, a2):
    db, qapp, folder = setup_vorta(repo)
    try:
        profile = BackupProfileModel.get(name='Default')
        nojson_lines, json_lines = diff_data(profile, a1, a2)

        cProfile.runctx('run_json(json_lines)', globals(), locals(),
                        'profile_stats-newjson')
        cProfile.runctx('run_old_json(json_lines)', globals(), locals(),
                        'profile_stats-oldjson')
        cProfile.runctx('run_nojson(nojson_lines)', globals(), locals(),
                        'profile_stats-newlines')
        cProfile.runctx('run_old_nojson(nojson_lines)', globals(), locals(),
                        'profile_stats-oldlines')

    finally:
        db.close()
        qapp.quit()
        folder.cleanup()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
