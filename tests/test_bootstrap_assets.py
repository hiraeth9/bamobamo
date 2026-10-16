"""Offline restore/check behavior, using actual tiny files in a temporary project."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import bootstrap_assets as bootstrap


@pytest.fixture
def project(tmp_path):
    manifest = {'profiles': {'core': {'assets': [
        {'id': 'animation', 'path': 'runtime/animation.glb', 'bundled_source': 'bundled/animation.glb'},
        {'id': 'character_source', 'path': 'bundled/character/source.fbx'},
        {'id': 'character_cache', 'path': 'bundled/character/character.blend'},
    ]}}}
    (tmp_path/'knowledge/assets').mkdir(parents=True)
    (tmp_path/'knowledge/assets/distribution.json').write_text(json.dumps(manifest))
    (tmp_path/'bundled/character').mkdir(parents=True)
    (tmp_path/'bundled/animation.glb').write_bytes(b'animation fixture')
    (tmp_path/'bundled/character/source.fbx').write_bytes(b'character fixture')
    (tmp_path/'bundled/character/character.blend').write_bytes(b'cache fixture')
    return tmp_path


def test_bootstrap_restores_missing_runtime_files_and_keeps_character(project):
    result = bootstrap.bootstrap_assets(project)
    assert result['ok']
    assert result['installed'] == ['runtime/animation.glb']
    assert (project/'runtime/animation.glb').read_bytes() == b'animation fixture'
    assert (project/'bundled/character/character.blend').read_bytes() == b'cache fixture'


def test_existing_local_asset_is_never_overwritten_and_repeat_is_idempotent(project):
    (project/'runtime').mkdir()
    (project/'runtime/animation.glb').write_bytes(b'my local copy')
    first = bootstrap.bootstrap_assets(project)
    second = bootstrap.bootstrap_assets(project)
    assert first['ok'] and second['ok']
    assert first['installed'] == second['installed'] == []
    assert (project/'runtime/animation.glb').read_bytes() == b'my local copy'


def test_check_is_read_only_and_reports_missing_runtime(project):
    before = set(project.rglob('*'))
    result = bootstrap.bootstrap_assets(project, check=True)
    assert not result['ok']
    assert result['missing'][0]['path'] == 'runtime/animation.glb'
    assert not result['installed']
    assert set(project.rglob('*')) == before
    assert bootstrap.main(['--project', str(project), '--profile', 'core', '--check']) == 1


def test_check_passes_after_restore_and_project_is_injectable(project, monkeypatch):
    monkeypatch.setattr(bootstrap, 'PROJECT', project)
    assert bootstrap.bootstrap_assets()['ok']
    assert bootstrap.main(['--profile', 'core', '--check']) == 0


def test_missing_bundled_source_is_reported_without_creating_placeholder(project):
    (project/'bundled/animation.glb').unlink()
    result = bootstrap.bootstrap_assets(project)
    assert not result['ok']
    assert result['missing'][0]['bundled_source'] == 'bundled/animation.glb'
    assert not (project/'runtime/animation.glb').exists()


def test_character_source_and_cache_are_required_and_are_not_rebuilt(project):
    (project/'bundled/character/character.blend').unlink()
    result = bootstrap.bootstrap_assets(project)
    assert not result['ok']
    assert any(item['id'] == 'character_cache' for item in result['missing'])
    assert not (project/'bundled/character/character.blend').exists()


def test_unknown_profile_has_an_actionable_error(project):
    with pytest.raises(ValueError, match='Unknown asset profile'):
        bootstrap.bootstrap_assets(project, profile='unknown')


def test_real_core_manifest_includes_small_cmu_sources_with_their_own_terms():
    project = Path(__file__).resolve().parents[1]
    manifest = json.loads((project/bootstrap.MANIFEST).read_text())
    core = manifest['profiles']['core']
    sources = [asset for asset in core['assets'] if asset.get('license') == 'CMU-Mocap']
    assert {asset['id'] for asset in sources} == {'cmu_02_01', 'cmu_07_01', 'cmu_02_03', 'cmu_79_08', 'cmu_80_10'}
    assert sum((project/asset['path']).stat().st_size for asset in sources) < 1_000_000
    assert {'cmu.02_01', 'cmu.07_01', 'cmu.02_03', 'cmu.79_08', 'cmu.80_10'} <= set(core['motion_ids'])
    terms = (project/'bundled_assets/animations/cmu/LICENSE.txt').read_text()
    assert 'not CC0' in terms and 'Direct\nsale of the motion data itself is prohibited' in terms
    assert 'pass them\nalong' in terms
    provenance = next(source for source in manifest['sources'] if source['license'] == 'CMU-Mocap')
    assert provenance['source_url'] == 'https://mocap.cs.cmu.edu/'
    assert provenance['download_mirror'].startswith('https://huggingface.co/')
    assert 'not the original' in provenance['mirror_role']
    assert {'bundled_assets/animations/cmu/LICENSE.txt', 'bundled_assets/animations/cmu/SOURCES.md'} <= {
        asset['path'] for asset in core['assets']}


def test_bootstrap_reports_licensed_runtime_scope_without_relabeling_all_sources_cc0(project):
    result = bootstrap.bootstrap_assets(project)
    assert 'individual source terms' in result['scope']
    assert 'CC0 runtime files only' not in result['scope']
