"""Fork publication follows validation of the resulting tree, not either parent."""
import os
import subprocess

import pytest

from hermes_cli import main, update_cmd, update_cmd_commit
from hermes_cli.update_cmd_git import _push_synced_fork


def _git(cwd, *args):
    return subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True,
                          text=True, encoding='utf-8').stdout.strip()


@pytest.fixture
def fork(tmp_path, monkeypatch):
    for key, value in {'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
                       'GIT_AUTHOR_NAME': 'Test', 'GIT_AUTHOR_EMAIL': 'test@example.invalid',
                       'GIT_COMMITTER_NAME': 'Test', 'GIT_COMMITTER_EMAIL': 'test@example.invalid'}.items():
        monkeypatch.setenv(key, value)
    upstream = tmp_path / 'upstream'
    upstream.mkdir()
    _git(upstream, 'init', '-q', '-b', 'main')
    source = 'def example():\n' + '    # unchanged separator\n' * 12 + '    pass\n'
    (upstream / 'hermes_constants.py').write_text(source)
    _git(upstream, 'add', '.')
    _git(upstream, 'commit', '-qm', 'base')
    origin = tmp_path / 'origin.git'
    _git(tmp_path, 'clone', '-q', '--bare', str(upstream), str(origin))
    clone = tmp_path / 'clone'
    _git(tmp_path, 'clone', '-q', str(origin), str(clone))
    _git(clone, 'remote', 'add', 'upstream', str(upstream))
    monkeypatch.setattr(main, 'PROJECT_ROOT', clone)
    update_cmd_commit.begin_update_attempt()
    yield upstream, origin, clone, source
    update_cmd_commit.begin_update_attempt()


def _pull():
    return update_cmd._pull_updates(['git'], 'main', None, prompt_for_restore=False,
        gw_input_fn=None, discard_local_changes=False, keep_stash=False,
        sync_upstream=True, assume_yes=True)


@pytest.mark.parametrize('broken', [True, False])
def test_divergent_result_validated_before_publication(fork, monkeypatch, broken):
    upstream, origin, clone, source = fork
    (clone / 'hermes_constants.py').write_text(source.replace('def example():', 'class example:'))
    _git(clone, 'commit', '-qam', 'fork class')
    _git(clone, 'push', '-q', 'origin', 'main')
    before = _git(origin, 'rev-parse', 'refs/heads/main')
    changed = source.replace('    pass', '    return 1' if broken else '    value = 1')
    compile(changed, 'upstream', 'exec')  # valid upstream is not sufficient
    compile((clone / 'hermes_constants.py').read_text(), 'fork', 'exec')
    _git(clone, 'reset', '-q', '--hard', 'HEAD~1')  # origin pull must actually move
    start = _git(clone, 'rev-parse', 'HEAD')
    (upstream / 'hermes_constants.py').write_text(changed)
    _git(upstream, 'commit', '-qam', 'upstream function body')
    monkeypatch.setattr('hermes_cli.config.load_config', lambda: {'updates': {'merge_upstream': True}})
    if broken:
        with pytest.raises(SystemExit) as refusal:
            _pull()
        assert refusal.value.code == 1
        assert _git(origin, 'rev-parse', 'refs/heads/main') == before, 'invalid merge was published'
        assert _git(clone, 'rev-parse', 'HEAD') == start
    else:
        _pull()
        after = _git(clone, 'rev-parse', 'HEAD')
        assert after != before
        assert _git(origin, 'rev-parse', 'refs/heads/main') == after
        assert len(_git(clone, 'show', '-s', '--format=%P').split()) == 2
        compile((clone / 'hermes_constants.py').read_text(), 'result', 'exec')


def test_validated_fast_forward_publishes_without_merge_toggle(fork, capsys):
    upstream, origin, clone, source = fork
    (upstream / 'hermes_constants.py').write_text(source + '\n# upstream\n')
    _git(upstream, 'commit', '-qam', 'upstream')
    _git(clone, 'fetch', '-q', 'upstream')
    _git(clone, 'merge', '--ff-only', 'refs/remotes/upstream/main')
    assert update_cmd._validate_critical_files_syntax(clone)[0]
    _push_synced_fork(['git'], clone)
    assert _git(origin, 'rev-parse', 'refs/heads/main') == _git(clone, 'rev-parse', 'HEAD')
    assert "couldn't push" not in capsys.readouterr().out


def test_divergent_disabled_preserves_both_refs(fork, monkeypatch):
    upstream, origin, clone, _source = fork
    (clone / 'local.txt').write_text('fork')
    _git(clone, 'add', '.')
    _git(clone, 'commit', '-qm', 'fork')
    _git(clone, 'push', '-q', 'origin', 'main')
    before = _git(clone, 'rev-parse', 'HEAD')
    _git(clone, 'reset', '-q', '--hard', 'HEAD~1')
    (upstream / 'upstream.txt').write_text('upstream')
    _git(upstream, 'add', '.')
    _git(upstream, 'commit', '-qm', 'upstream')
    monkeypatch.setattr('hermes_cli.config.load_config', lambda: {'updates': {'merge_upstream': False}})
    _pull()
    assert _git(clone, 'rev-parse', 'HEAD') == before
    assert _git(origin, 'rev-parse', 'refs/heads/main') == before


def test_failed_push_retains_local_validated_tree(fork, capsys):
    upstream, origin, clone, source = fork
    (upstream / 'hermes_constants.py').write_text(source + '\n# upstream\n')
    _git(upstream, 'commit', '-qam', 'upstream')
    _git(clone, 'fetch', '-q', 'upstream')
    _git(clone, 'merge', '--ff-only', 'refs/remotes/upstream/main')
    before, local = _git(origin, 'rev-parse', 'refs/heads/main'), _git(clone, 'rev-parse', 'HEAD')
    _git(clone, 'remote', 'set-url', '--push', 'origin', str(clone / 'missing-origin.git'))
    _push_synced_fork(['git'], clone)
    assert _git(origin, 'rev-parse', 'refs/heads/main') == before
    assert _git(clone, 'rev-parse', 'HEAD') == local
    assert "couldn't push" in capsys.readouterr().out


def test_no_code_update_still_publishes_validated_fast_forward(fork, monkeypatch):
    upstream, origin, clone, source = fork
    (upstream / 'hermes_constants.py').write_text(source + '\n# new upstream\n')
    _git(upstream, 'commit', '-qam', 'upstream')
    _git(clone, 'fetch', '-q', 'upstream')
    _git(clone, 'merge', '--ff-only', 'refs/remotes/upstream/main')
    monkeypatch.setattr('hermes_cli.config.load_config', lambda: {'updates': {'merge_upstream': False}})
    plan = update_cmd._prepare_checkout_for_update(['git'], 'main', 'main', is_fork=True,
        assume_yes=True, gateway_mode=False, gw_input_fn=None, switch_branch=False,
        _windows_gateway_resume=None)
    assert plan.commit_count == 0
    # Completion hands off to PM/runtime maintenance, outside this local Git contract.
    monkeypatch.setattr(update_cmd, '_complete_source_update', lambda request: None)
    update_cmd._finish_already_up_to_date(['git'], 'main', 'main', plan,
        gw_input_fn=None, completion_request={})
    assert _git(origin, 'rev-parse', 'refs/heads/main') == _git(clone, 'rev-parse', 'HEAD')
