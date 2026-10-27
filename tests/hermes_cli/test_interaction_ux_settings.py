"""Native settings schema uses the existing JSON editor, not comma-separated strings."""
from copy import deepcopy

import pytest


def test_settings_schema_and_saved_menu_roundtrip(tmp_path, monkeypatch):
    from hermes_cli.config import save_config, read_raw_config
    from hermes_cli.interaction_defaults import INTERACTION_DEFAULTS
    from hermes_cli.interaction_config import resolve_interaction
    from hermes_cli.web_server_config import CONFIG_SCHEMA, _normalize_config_for_web, _denormalize_config_from_web
    assert CONFIG_SCHEMA['interaction.menu']['type'] == 'json'
    assert CONFIG_SCHEMA['interaction.show_countdown']['type'] == 'boolean'
    assert 'interaction.max_output_tokens' in CONFIG_SCHEMA
    assert resolve_interaction({}).max_output_tokens == 150000
    menu = deepcopy(INTERACTION_DEFAULTS['menu'])
    menu.insert(0, {'id': 'audit', 'label': 'Audit', 'prompt': 'Review only.', 'description': 'Custom prompt'})
    menu[1]['enabled'] = False
    homes = [tmp_path / name for name in ('a', 'b')]
    fingerprints = []
    for home in (homes[0], homes[1], homes[0]):
        home.mkdir(exist_ok=True)
        monkeypatch.setenv('HERMES_HOME', str(home))
        if not (home / 'config.yaml').exists():
            save_config({'interaction': {'menu': menu if home == homes[0] else INTERACTION_DEFAULTS['menu'],
                'show_countdown': False, 'max_output_tokens': 150000 if home == homes[0] else 256}})
        config = _denormalize_config_from_web(_normalize_config_for_web(read_raw_config()))
        effective = resolve_interaction(config)
        fingerprints.append(effective.fingerprint)
        if home == homes[0]:
            assert effective.menu[0].id == 'audit' and effective.menu[0].prompt == 'Review only.'
            assert not effective.menu[1].enabled
        assert effective.menu[-1].id == 'stop' and effective.menu[-1].enabled
        assert not effective.show_countdown
        assert effective.max_output_tokens == (150000 if home == homes[0] else 256)
    assert fingerprints[0] == fingerprints[2] != fingerprints[1]
    with pytest.raises(ValueError):
        resolve_interaction({'interaction': {'menu': [{'id': 'shell', 'handler': 'shell', 'prompt': 'never'}]}})


@pytest.mark.parametrize('value', [150001, 0, -1, True, 1.5, float('inf'), float('nan'), '150000', None])
def test_output_cap_requires_a_positive_finite_integer_at_most_owner_ceiling(value):
    from hermes_cli.interaction_config import resolve_interaction
    with pytest.raises(ValueError, match='max_output_tokens'):
        resolve_interaction({'interaction': {'max_output_tokens': value}})
