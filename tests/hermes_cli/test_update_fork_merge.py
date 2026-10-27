"""Real fork conflicts abort only this updater's merge and preserve the principal pull."""
from __future__ import annotations

import subprocess

import pytest

from hermes_cli import update_cmd_commit, update_custody
from hermes_cli._early_recovery import interrupted_pull_marker
from hermes_cli.update_cmd_git import _sync_with_upstream_if_needed
from hermes_cli.update_host_obligation import read_host_obligation
from tests.hermes_cli.test_update_fork_publication import _git, _pull

pytest_plugins = ['tests.hermes_cli.test_update_fork_publication']


def _conflicting_fork(repos, filename, monkeypatch):
    upstream, origin, clone, source = repos
    critical = filename == 'hermes_constants.py'
    local = source.replace('def example():', 'def fork_example():') if critical else 'fork\n'
    remote = source.replace('def example():', 'def upstream_example():') if critical else 'upstream\n'
    (clone / filename).write_text(local)
    _git(clone, 'add', filename)
    _git(clone, 'commit', '-qm', 'fork')
    _git(clone, 'push', '-q', 'origin', 'main')
    published = _git(origin, 'rev-parse', 'refs/heads/main')
    _git(clone, 'reset', '-q', '--hard', 'HEAD~1')
    start = _git(clone, 'rev-parse', 'HEAD')
    update_cmd_commit.record_run_start(['git'], clone)
    (upstream / filename).write_text(remote)
    _git(upstream, 'add', filename)
    _git(upstream, 'commit', '-qm', 'upstream conflict')
    monkeypatch.setattr('hermes_cli.config.load_config', lambda: {'updates': {'merge_upstream': True}})
    return origin, clone, start, published


@pytest.mark.parametrize('filename', ['notes.txt', 'hermes_constants.py'], ids=['add-add', 'critical-content'])
def test_owned_conflict_is_a_clean_skip_after_principal_pull(fork, monkeypatch, filename):
    origin, clone, start, published = _conflicting_fork(fork, filename, monkeypatch)
    real_run, pushes = update_custody.run_git, []

    def record_git(git_cmd, args, **kwargs):
        if args[0] == 'push':
            pushes.append(args)
        return real_run(git_cmd, args, **kwargs)

    monkeypatch.setattr(update_custody, 'run_git', record_git)
    try:
        result = _pull()
    except SystemExit as exc:
        result = exc
    assert result == start, 'a normal optional conflict must retain the successful origin pull'
    assert _git(clone, 'diff', '--name-only', '--diff-filter=U') == ''
    assert _git(clone, 'status', '--porcelain') == ''
    assert not (clone / '.git/MERGE_HEAD').exists()
    assert not interrupted_pull_marker(clone).exists()
    assert _git(clone, 'rev-parse', 'HEAD') == published
    assert _git(origin, 'rev-parse', 'refs/heads/main') == published
    assert (read_host_obligation() or {}).get('expected_sha') == published
    assert pushes == []


def test_failed_owned_abort_stops_with_recovery_and_principal_debt_intact(fork, monkeypatch):
    origin, clone, _start, published = _conflicting_fork(fork, 'notes.txt', monkeypatch)
    real_run = update_custody.run_git
    aborts = []

    def fail_abort(git_cmd, args, **kwargs):
        if args == ['merge', '--abort']:
            aborts.append(args)
            return subprocess.CompletedProcess(args, 1, '', 'synthetic abort failure')
        return real_run(git_cmd, args, **kwargs)

    monkeypatch.setattr(update_custody, 'run_git', fail_abort)
    with pytest.raises(subprocess.CalledProcessError):
        _pull()
    assert aborts == [['merge', '--abort']]
    assert _git(clone, 'diff', '--name-only', '--diff-filter=U') == 'notes.txt'
    assert (clone / '.git/MERGE_HEAD').is_file()
    assert interrupted_pull_marker(clone).is_file()
    assert _git(clone, 'rev-parse', 'HEAD') == published
    assert _git(origin, 'rev-parse', 'refs/heads/main') == published
    assert (read_host_obligation() or {}).get('expected_sha') == published


@pytest.mark.parametrize('conflicted', [True, False], ids=['conflicted-index', 'clean-index'])
def test_an_existing_external_merge_is_not_aborted_or_recovered(fork, monkeypatch, conflicted):
    origin, clone, _start, published = _conflicting_fork(fork, 'notes.txt', monkeypatch)
    _git(clone, 'merge', '--ff-only', 'refs/remotes/origin/main')
    _git(clone, 'fetch', '-q', 'upstream')
    if conflicted:
        with pytest.raises(subprocess.CalledProcessError):
            _git(clone, 'merge', '--no-edit', 'refs/remotes/upstream/main')
    else:
        _git(clone, 'merge', '--no-commit', '--no-ff', '-s', 'ours', 'refs/remotes/upstream/main')
    before = (clone / 'notes.txt').read_bytes(), (clone / '.git/MERGE_HEAD').read_bytes()
    assert _sync_with_upstream_if_needed(['git'], clone, assume_yes=True) is False
    assert ((clone / 'notes.txt').read_bytes(), (clone / '.git/MERGE_HEAD').read_bytes()) == before
    assert _git(clone, 'diff', '--name-only', '--diff-filter=U') == ('notes.txt' if conflicted else '')
    assert not interrupted_pull_marker(clone).exists()
    assert _git(origin, 'rev-parse', 'refs/heads/main') == published


@pytest.mark.parametrize('enabled,dirty,branch', [(False, False, 'main'), (True, True, 'main'),
                                               (True, False, 'feature')])
def test_merge_opt_in_and_clean_main_are_required(fork, monkeypatch, enabled, dirty, branch):
    origin, clone, _start, published = _conflicting_fork(fork, 'notes.txt', monkeypatch)
    _git(clone, 'merge', '--ff-only', 'refs/remotes/origin/main')
    if dirty:
        (clone / 'notes.txt').write_text('uncommitted local edit\n')
    if branch != 'main':
        _git(clone, 'checkout', '-qb', branch)
    before = _git(clone, 'status', '--porcelain')
    monkeypatch.setattr('hermes_cli.config.load_config', lambda: {'updates': {'merge_upstream': enabled}})
    assert _sync_with_upstream_if_needed(['git'], clone, assume_yes=True) is False
    assert _git(clone, 'status', '--porcelain') == before
    assert _git(clone, 'rev-parse', 'HEAD') == published
    assert _git(origin, 'rev-parse', 'refs/heads/main') == published
    assert not (clone / '.git/MERGE_HEAD').exists()
    assert not interrupted_pull_marker(clone).exists()
