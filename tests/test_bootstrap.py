"""Fresh-path setup tests using tiny archives and mocked installers/services."""
import io
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import bootstrap


def archive(path, entries):
    with tarfile.open(path,'w:gz') as package:
        for name,content in entries.items():
            item=tarfile.TarInfo(name)
            item.size=len(content); item.mode=0o755
            package.addfile(item,io.BytesIO(content))


def skill(root):
    path=root/'white-model-director'
    path.mkdir()
    (path/'SKILL.md').write_text('test director skill',encoding='utf8')


def test_dotenv_is_data_and_preserves_environment_precedence(tmp_path):
    (tmp_path/'.env').write_text('BAMO_API_KEY="literal-$NOT_EXECUTED"\nBAMO_MODEL=file-model\n# comment\n',encoding='utf8')
    values=bootstrap.project_environment(tmp_path,{'PATH':'/usr/bin','BAMO_MODEL':'shell-model'})
    assert values['BAMO_API_KEY']=='literal-$NOT_EXECUTED'
    assert values['BAMO_MODEL']=='shell-model'
    assert values['UV_CACHE_DIR']==str(tmp_path/'config/bootstrap/cache')
    assert not (tmp_path/'config').exists()


def test_fresh_model_configuration_uses_only_explicit_user_settings(tmp_path,capsys):
    skill(tmp_path)
    env={'BAMO_API_KEY':'test-local-token','BAMO_API_BASE':'https://provider.test/v1','BAMO_MODEL':'test-model'}
    bootstrap.configure_model(tmp_path,env,Path('/portable/tools/uv'))
    home=tmp_path/'config/hermes-runtime'
    config=json.loads((home/'config.yaml').read_text())
    assert config['custom_providers'][0]['key_env']=='BAMO_API_KEY'
    assert config['mcp_servers']['blender']['command']=='/portable/tools/uvx'
    assert 'test-local-token' not in (home/'config.yaml').read_text()
    assert (home/'.env').read_text()=='BAMO_API_KEY="test-local-token"\n'
    assert stat.S_IMODE((home/'.env').stat().st_mode)==0o600
    assert (home/'skills/creative/white-model-director/SKILL.md').is_file()
    assert 'test-local-token' not in capsys.readouterr().out


def test_missing_user_key_does_not_copy_another_profile(tmp_path):
    with pytest.raises(RuntimeError,match='自己的 BAMO_API_KEY'):
        bootstrap.configure_model(tmp_path,{},Path('/tools/uv'))
    assert not (tmp_path/'config').exists()


def test_user_supplied_profile_is_kept_intact(tmp_path):
    skill(tmp_path)
    home=tmp_path/'own-profile'
    home.mkdir()
    (home/'config.yaml').write_text('model:\n  default: existing-model\n',encoding='utf8')
    (home/'.env').write_text('CUSTOM_KEY=keep-my-token\n',encoding='utf8')
    bootstrap.configure_model(tmp_path,{'BAMO_HERMES_HOME':str(home),'BAMO_API_KEY':'do-not-copy'},Path('/tools/uv'))
    assert (home/'.env').read_text()=='CUSTOM_KEY=keep-my-token\n'
    assert 'existing-model' in (home/'config.yaml').read_text()


def test_managed_model_updates_from_new_user_configuration(tmp_path):
    skill(tmp_path)
    values={'BAMO_API_KEY':'first-token','BAMO_API_BASE':'https://provider.test/v1','BAMO_MODEL':'first-model'}
    bootstrap.configure_model(tmp_path,dict(values),Path('/tools/uv'))
    bootstrap.configure_model(tmp_path,{**values,'BAMO_MODEL':'second-model','BAMO_API_KEY':'second-token'},Path('/tools/uv'))
    home=tmp_path/'config/hermes-runtime'
    assert json.loads((home/'config.yaml').read_text())['model']['default']=='second-model'
    assert 'second-token' in (home/'.env').read_text()


def test_uv_download_and_extract_in_project_local_directory(tmp_path,monkeypatch):
    bundle=tmp_path/'fixture.tar.gz'
    archive(bundle,{'uv-test/uv':b'uv-fixture','uv-test/uvx':b'uvx-fixture'})
    monkeypatch.setattr(bootstrap.shutil,'which',lambda name:None)
    monkeypatch.setattr(bootstrap,'UV_URL',bundle.as_uri())
    root=tmp_path/'另一个 项目'; root.mkdir()
    result=bootstrap.ensure_uv(root)
    assert result.read_bytes()==b'uv-fixture'
    assert result.with_name('uvx').read_bytes()==b'uvx-fixture'
    assert result.stat().st_mode&stat.S_IXUSR


def test_blender_local_archive_restores_required_layout(tmp_path,monkeypatch):
    bundle=tmp_path/'fixture.tar.gz'
    archive(bundle,{bootstrap.BLENDER_NAME+'/blender':b'blender-fixture',
                    bootstrap.BLENDER_NAME+'/5.2/python/bin/python3.13':b'python-fixture'})
    root=tmp_path/'another checkout'; root.mkdir()
    (root/'blender').write_text('wrapper',encoding='utf8')
    calls=[]
    monkeypatch.setattr(bootstrap.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0))
    monkeypatch.setattr(bootstrap,'command',lambda args,**kwargs:calls.append(args))
    bootstrap.ensure_blender(root,Path('/tools/uv'),{'BAMO_BLENDER_ARCHIVE':str(bundle)})
    assert (root/bootstrap.BLENDER_NAME/'blender').read_bytes()==b'blender-fixture'
    assert bootstrap.blender_python(root).name=='python3.13'
    assert calls[0][-1]==root/'requirements-blender.txt'


def test_ffmpeg_can_reuse_system_pair_without_download(tmp_path,monkeypatch):
    system=tmp_path/'system'; system.mkdir()
    for name in ('ffmpeg','ffprobe'): (system/name).write_text(name)
    monkeypatch.setattr(bootstrap.shutil,'which',lambda name:str(system/name))
    monkeypatch.setattr(bootstrap,'download',lambda *args:pytest.fail('download not needed'))
    root=tmp_path/'checkout'; root.mkdir()
    bootstrap.ensure_ffmpeg(root)
    assert (root/'ffmpeg-env/bin/ffmpeg').resolve()==system/'ffmpeg'
    assert (root/'ffmpeg-env/bin/ffprobe').resolve()==system/'ffprobe'


def test_asset_check_propagates_missing_material_status(tmp_path,monkeypatch):
    (tmp_path/'scripts').mkdir()
    (tmp_path/'scripts/bootstrap_assets.py').touch()
    calls=[]
    monkeypatch.setattr(bootstrap.subprocess,'run',lambda args,**kwargs:(calls.append(args) or SimpleNamespace(returncode=2)))
    assert bootstrap.asset_command(tmp_path,'python',check=True)==2
    assert calls==[['python',str(tmp_path/'scripts/bootstrap_assets.py'),'--profile','core','--check']]


def test_read_only_check_never_installs_or_writes_configuration(tmp_path,monkeypatch):
    monkeypatch.setattr(bootstrap,'system_issues',lambda:[])
    calls=[]
    monkeypatch.setattr(bootstrap,'asset_command',lambda root,python,check=False:(calls.append(check) or 1))
    assert bootstrap.check(tmp_path,{})==1
    assert calls==[True]
    assert list(tmp_path.iterdir())==[]


def test_existing_hermes_source_and_lock_are_not_reset(tmp_path,monkeypatch):
    (tmp_path/'config').mkdir()
    (tmp_path/'config/hermes-source.json').write_text(json.dumps({'repository_url':'https://example.test/source.git','commit':'pin','current_lockfile_snapshot':'config/lock'}))
    source=tmp_path/'hermes'; source.mkdir()
    (source/'pyproject.toml').write_text('existing source')
    (source/'uv.lock').write_text('local lock')
    calls=[]
    monkeypatch.setattr(bootstrap,'command',lambda args,**kwargs:calls.append(args))
    bootstrap.ensure_hermes(tmp_path,Path('/tools/uv'),{})
    assert len(calls)==1 and calls[0][1]=='sync'
    assert (source/'uv.lock').read_text()=='local lock'


def test_fresh_hermes_checks_out_pin_and_uses_saved_lock(tmp_path,monkeypatch):
    (tmp_path/'config').mkdir()
    (tmp_path/'config/hermes-source.json').write_text(json.dumps({'repository_url':'https://example.test/hermes.git','commit':'specified-pin','current_lockfile_snapshot':'config/lock'}))
    (tmp_path/'config/lock').write_text('project dependency lock')
    source=tmp_path/'hermes'
    calls=[]
    def simulate_command(args,**kwargs):
        calls.append(args)
        if args[:2]==['git','clone']:
            source.mkdir()
        elif args[0]=='git' and args[3]=='checkout':
            (source/'pyproject.toml').write_text('fresh source')
    monkeypatch.setattr(bootstrap,'command',simulate_command)
    bootstrap.ensure_hermes(tmp_path,Path('/tools/uv'),{})
    assert calls[0]==['git','clone','--no-checkout','https://example.test/hermes.git',source]
    assert calls[1]==['git','-C',source,'checkout','specified-pin']
    assert (source/'uv.lock').read_text()=='project dependency lock'
    assert calls[2][1]=='sync' and '--frozen' in calls[2]


def test_entrypoint_accepts_new_checkout_path_with_spaces(tmp_path):
    root=tmp_path/'新 克隆'; (root/'scripts').mkdir(parents=True)
    shutil.copy2(bootstrap.PROJECT/'run.sh',root/'run.sh')
    (root/'scripts/bootstrap.py').write_text('import pathlib,sys\nprint(pathlib.Path(__file__).resolve().parents[1])\nprint(sys.argv[1:])\n',encoding='utf8')
    result=subprocess.run(['bash',str(root/'run.sh'),'--check'],cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode==0
    assert str(root) in result.stdout and "['--check']" in result.stdout


def test_setup_only_never_starts_services_or_model_calls(tmp_path,monkeypatch):
    monkeypatch.setattr(bootstrap,'PROJECT',tmp_path)
    monkeypatch.setattr(bootstrap,'system_issues',lambda:[])
    monkeypatch.setattr(bootstrap,'validate_model_configuration',lambda *args:None)
    monkeypatch.setattr(bootstrap,'ensure_uv',lambda root:Path('/tools/uv'))
    monkeypatch.setattr(bootstrap,'ensure_web',lambda *args:tmp_path/'.venv/bin/python')
    for name in ('ensure_hermes','ensure_blender','ensure_ffmpeg','configure_model'):
        monkeypatch.setattr(bootstrap,name,lambda *args:None)
    monkeypatch.setattr(bootstrap,'asset_command',lambda *args:0)
    monkeypatch.setattr(bootstrap.subprocess,'run',lambda *args,**kwargs:pytest.fail('must not start services'))
    assert bootstrap.main(['--setup-only'])==0


def test_missing_assets_block_startup_instead_of_claiming_ready(tmp_path,monkeypatch):
    monkeypatch.setattr(bootstrap,'PROJECT',tmp_path)
    monkeypatch.setattr(bootstrap,'system_issues',lambda:[])
    monkeypatch.setattr(bootstrap,'validate_model_configuration',lambda *args:None)
    monkeypatch.setattr(bootstrap,'ensure_uv',lambda root:Path('/tools/uv'))
    monkeypatch.setattr(bootstrap,'ensure_web',lambda *args:tmp_path/'.venv/bin/python')
    for name in ('ensure_hermes','ensure_blender','ensure_ffmpeg','configure_model'):
        monkeypatch.setattr(bootstrap,name,lambda *args:None)
    monkeypatch.setattr(bootstrap,'asset_command',lambda *args:2)
    monkeypatch.setattr(bootstrap.subprocess,'run',lambda *args,**kwargs:pytest.fail('must not start services'))
    assert bootstrap.main([])==1


def test_old_bootstrap_python_has_actionable_error(monkeypatch):
    monkeypatch.setattr(bootstrap.sys,'version_info',(3,9,18))
    with pytest.raises(RuntimeError,match='更新系统 Python'):
        bootstrap.main(['--check'])


def test_missing_credentials_fail_before_any_installation(tmp_path,monkeypatch):
    monkeypatch.setattr(bootstrap,'PROJECT',tmp_path)
    monkeypatch.setattr(bootstrap,'project_environment',lambda root:{})
    monkeypatch.setattr(bootstrap,'system_issues',lambda:[])
    monkeypatch.setattr(bootstrap,'ensure_uv',lambda *args:pytest.fail('must not download before configuration'))
    with pytest.raises(RuntimeError,match='自己的 BAMO_API_KEY'):
        bootstrap.main([])
    assert list(tmp_path.iterdir())==[]
