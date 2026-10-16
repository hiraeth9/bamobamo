"""Prepare a relocatable Linux checkout, then start its existing services.

Only project-local tools/configuration are written. --check is read-only;
--setup-only prepares dependencies/assets without starting services or jobs.
"""
from __future__ import annotations

import argparse
import ctypes.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request

PROJECT = Path(__file__).resolve().parents[1]
BLENDER_NAME = 'blender-5.2.2-linux-x64'
BLENDER_URL = 'https://download.blender.org/release/Blender5.2/'+BLENDER_NAME+'.tar.xz'
UV_URL = 'https://releases.astral.sh/github/uv/releases/download/0.12.19/uv-x86_64-unknown-linux-gnu.tar.gz'
FFMPEG_URL = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz'
SYSTEM_PACKAGES = ('git tmux xvfb libsm6 libice6 libx11-6 libxrender1 libxi6 '
                   'libxfixes3 libxkbcommon0 libgl1 libegl1 libgl1-mesa-dri')


def project_environment(root, inherited=None):
    """Read dotenv as data, without shell expansion or logging credentials."""
    env = dict(os.environ if inherited is None else inherited)
    path = root/'.env'
    if path.is_file():
        for line in path.read_text(encoding='utf8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            name, value = line.removeprefix('export ').split('=', 1)
            value = value.strip()
            if len(value)>1 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            env.setdefault(name.strip(), value)
    env['PATH'] = str(root/'config/bootstrap/bin')+os.pathsep+env.get('PATH', os.defpath)
    for name,directory in (('UV_PYTHON_INSTALL_DIR','python'),('UV_CACHE_DIR','cache'),
                           ('UV_TOOL_DIR','tools'),('UV_TOOL_BIN_DIR','bin')):
        env.setdefault(name,str(root/'config/bootstrap'/directory))
    if env.get('BAMO_HERMES_HOME'):
        env['HERMES_HOME'] = str(Path(env['BAMO_HERMES_HOME']).expanduser().resolve())
    elif (root/'config/hermes-runtime/config.yaml').is_file():
        env['HERMES_HOME'] = str(root/'config/hermes-runtime')
    return env


def command(arguments, *, cwd=None, env=None):
    """Do not echo arguments/environment: provider configuration stays private."""
    result = subprocess.run([str(value) for value in arguments], cwd=cwd, env=env)
    if result.returncode:
        raise RuntimeError(Path(str(arguments[0])).name+' 执行失败；请根据上方安装错误处理后重试')


def download(url, destination, label):
    destination.parent.mkdir(parents=True, exist_ok=True)
    print('下载 '+label+'（首次安装需要联网）…', flush=True)
    request = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0 bamo-bootstrap/1.0'})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open('wb') as output:
            total = int(response.headers.get('Content-Length') or 0)
            done, reported = 0, 0
            while True:
                chunk = response.read(1024*1024)
                if not chunk:
                    break
                output.write(chunk); done += len(chunk)
                if done-reported >= 32*1024*1024:
                    suffix = '/%.0f MiB' % (total/1024**2) if total else ' MiB'
                    print('  %.0f' % (done/1024**2)+suffix, flush=True); reported = done
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        code = getattr(error, 'code', type(error).__name__)
        raise RuntimeError(f'{label} 下载失败（{code}）；检查网络后重试。入口：{url.split("?")[0]}') from None


def unpack(archive, destination):
    with tarfile.open(archive) as package:
        package.extractall(destination, filter='data')


def ensure_uv(root):
    tools = root/'config/bootstrap/bin'
    if (tools/'uv').is_file() and (tools/'uvx').is_file():
        return tools/'uv'
    existing, companion = shutil.which('uv'), shutil.which('uvx')
    if existing and companion:
        return Path(existing)
    tools.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='bamo-uv-') as folder:
        folder = Path(folder)
        archive = folder/'uv.tar.gz'
        download(UV_URL, archive, 'uv 0.12.19')
        unpack(archive, folder/'unpacked')
        for name in ('uv','uvx'):
            source = next((folder/'unpacked').rglob(name))
            shutil.copy2(source, tools/name)
            (tools/name).chmod(0o755)
    return tools/'uv'


def ensure_web(root, uv, env):
    python = root/'.venv/bin/python'
    if not python.is_file():
        print('准备项目 Python 3.12 和网页依赖…', flush=True)
        command([uv,'venv','--python','3.12',root/'.venv'], env=env)
    command([uv,'pip','install','--python',python,'-r',root/'requirements-linux.txt'], env=env)
    return python


def ensure_hermes(root, uv, env):
    source = root/'hermes'
    pin = json.loads((root/'config/hermes-source.json').read_text(encoding='utf8'))
    if not (source/'pyproject.toml').is_file():
        if source.exists() and any(source.iterdir()):
            raise RuntimeError('hermes 已有内容但缺少 pyproject.toml；请提供完整源码目录，不会覆盖现有内容')
        print('获取项目指定版本的 Hermes…', flush=True)
        command(['git','clone','--no-checkout',pin['repository_url'],source], env=env)
        command(['git','-C',source,'checkout',pin['commit']], env=env)
        shutil.copy2(root/pin['current_lockfile_snapshot'],source/'uv.lock')
    print('准备 Hermes Python 与 MCP 依赖…', flush=True)
    command([uv,'sync','--project',source,'--python','3.12','--frozen','--no-dev','--extra','mcp'], env=env)


def blender_python(root):
    candidates = sorted((root/BLENDER_NAME/'5.2/python/bin').glob('python3*'))
    return next((path for path in candidates if path.is_file() and not path.name.endswith('-config')), None)


def ensure_blender(root, uv, env):
    executable = root/BLENDER_NAME/'blender'
    if not executable.is_file():
        local = env.get('BAMO_BLENDER_ARCHIVE')
        if local:
            archive = Path(local).expanduser()
            if not archive.is_file():
                raise RuntimeError('BAMO_BLENDER_ARCHIVE 指定的压缩包不存在')
            unpack(archive, root)
        else:
            with tempfile.TemporaryDirectory(prefix='bamo-blender-') as folder:
                archive = Path(folder)/'blender.tar.xz'
                download(BLENDER_URL, archive, 'Blender 5.2.2 LTS，约 366 MiB')
                unpack(archive, root)
        if not executable.is_file():
            raise RuntimeError('压缩包未提供 '+BLENDER_NAME+'/blender；请使用 Linux x64 官方包')
    probe = subprocess.run([str(root/'blender'),'--version'], env=env, capture_output=True, text=True)
    if probe.returncode:
        detail = probe.stderr.strip().splitlines()[-1:] or ['Blender 无法启动']
        raise RuntimeError(detail[0]+'；Ubuntu/Debian：sudo apt-get install '+SYSTEM_PACKAGES)
    python = blender_python(root)
    if python is None:
        raise RuntimeError('Blender 包缺少内置 Python；请使用完整官方发行包')
    command([uv,'pip','install','--python',python,'-r',root/'requirements-blender.txt'], env=env)


def ensure_ffmpeg(root):
    target = root/'ffmpeg-env/bin'
    if all((target/name).is_file() for name in ('ffmpeg','ffprobe')):
        return
    system = {name:shutil.which(name) for name in ('ffmpeg','ffprobe')}
    if all(path and Path(path).resolve() != (root/name).resolve() for name,path in system.items()):
        target.mkdir(parents=True, exist_ok=True)
        for name,path in system.items():
            if not (target/name).exists():
                (target/name).symlink_to(Path(path).resolve())
        return
    with tempfile.TemporaryDirectory(prefix='bamo-ffmpeg-') as folder:
        folder = Path(folder)
        archive = folder/'ffmpeg.tar.xz'
        download(FFMPEG_URL, archive, 'FFmpeg 9.0 分支 Linux 静态包')
        unpack(archive, folder/'unpacked')
        source = next(path.parent for path in (folder/'unpacked').rglob('ffmpeg') if path.is_file())
        target.mkdir(parents=True, exist_ok=True)
        for name in ('ffmpeg','ffprobe'):
            if not (target/name).exists():
                shutil.copy2(source/name,target/name)
                (target/name).chmod(0o755)


def model_home(root, env):
    return Path(env.get('BAMO_HERMES_HOME') or root/'config/hermes-runtime').expanduser().resolve()


def validate_model_configuration(root, env):
    """Fail before large downloads when the user's fresh setup is incomplete."""
    managed_update = (not env.get('BAMO_HERMES_HOME') and bool(env.get('BAMO_API_KEY','').strip()))
    if (model_home(root,env)/'config.yaml').is_file() and not managed_update:
        return
    if not env.get('BAMO_API_KEY','').strip():
        raise RuntimeError('复制 .env.example 为 .env，填自己的 BAMO_API_KEY、BAMO_API_BASE、BAMO_MODEL；不读取其他用户的密钥')
    base, model = env.get('BAMO_API_BASE','').strip(),env.get('BAMO_MODEL','').strip()
    if not base.startswith(('http://','https://')) or '.example' in base or not model or model=='your-model':
        raise RuntimeError('请在 .env 填真实的 BAMO_API_BASE 与 BAMO_MODEL')


def configure_model(root, env, uv):
    home = model_home(root, env)
    config = home/'config.yaml'
    managed_update = (not env.get('BAMO_HERMES_HOME') and bool(env.get('BAMO_API_KEY','').strip()))
    if not config.is_file() or managed_update:
        validate_model_configuration(root,env)
        base, model = env.get('BAMO_API_BASE','').strip(),env.get('BAMO_MODEL','').strip()
        home.mkdir(parents=True, exist_ok=True)
        value = {'model':{'default':model,'provider':'custom:bamo-compatible','api_mode':'chat_completions','base_url':base},
                 'custom_providers':[{'name':'bamo-compatible','model':model,'models':[model],'base_url':base,
                                      'key_env':'BAMO_API_KEY','api_mode':'chat_completions'}],
                 'mcp_servers':{'blender':{'command':str(Path(uv).with_name('uvx')),'args':['mcp-for-blender==2.1.9']}}}
        config.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        config.chmod(0o600)
        keyfile = home/'.env'
        if not keyfile.exists() or managed_update:
            keyfile.write_text('BAMO_API_KEY='+json.dumps(env['BAMO_API_KEY'])+'\n',encoding='utf8')
            keyfile.chmod(0o600)
    destination = home/'skills/creative/white-model-director'
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(root/'white-model-director',destination,dirs_exist_ok=True)
    env['HERMES_HOME'] = str(home)
    env['BAMO_HERMES_HOME'] = str(home)
    print('Hermes 配置和 Skill 已就绪；使用你自己的模型账号，不发起模型请求。', flush=True)


def asset_command(root, python, check=False):
    script = root/'scripts/bootstrap_assets.py'
    if not script.is_file():
        print('缺少素材恢复入口 scripts/bootstrap_assets.py，尚不能确认素材就绪。',file=sys.stderr)
        return 1
    return subprocess.run([str(python),str(script),'--profile','core',*(['--check'] if check else [])]).returncode


def system_issues():
    issues = []
    if platform.system()!='Linux' or platform.machine().lower() not in ('x86_64','amd64'):
        issues.append('当前自动安装支持 Linux x86_64；其他架构需单独提供兼容的 Blender 运行时')
    missing = [name for name in ('git','tmux','Xvfb') if not shutil.which(name)]
    libraries = [name for name in ('SM','ICE','X11','Xi','Xfixes','Xrender','xkbcommon','GL','EGL')
                 if not ctypes.util.find_library(name)]
    if missing or libraries:
        issues.append('缺少系统依赖：'+', '.join(missing+libraries)+'。Ubuntu/Debian：sudo apt-get install '+SYSTEM_PACKAGES)
    return issues


def check(root, env):
    issues = system_issues()
    web = root/'.venv/bin/python'
    hermes = root/'hermes/.venv/bin/python'
    for name,path in (('网页Python',web),('Hermes Python',hermes),('Blender',root/BLENDER_NAME/'blender'),
                      ('FFmpeg',root/'ffmpeg-env/bin/ffmpeg'),('ffprobe',root/'ffmpeg-env/bin/ffprobe')):
        if not path.is_file():
            issues.append('未准备 '+name)
    if web.is_file():
        result = subprocess.run([str(web),'-c','import flask'],capture_output=True,env={**env,'PYTHONDONTWRITEBYTECODE':'1'})
        if result.returncode:
            issues.append('网页虚拟环境缺少 Flask')
    if hermes.is_file():
        probe = 'import importlib.util,sys; sys.exit(not all(importlib.util.find_spec(n) for n in ("openai","mcp","yaml")))'
        if subprocess.run([str(hermes),'-c',probe],capture_output=True,env={**env,'PYTHONDONTWRITEBYTECODE':'1'}).returncode:
            issues.append('Hermes依赖不完整；运行 ./run.sh --setup-only 补齐')
    embedded = blender_python(root)
    if embedded is not None:
        probe = 'import importlib.util,sys; sys.exit(not all(importlib.util.find_spec(n) for n in ("requests","numpy")))'
        if subprocess.run([str(embedded),'-c',probe],capture_output=True,env={**env,'PYTHONDONTWRITEBYTECODE':'1'}).returncode:
            issues.append('Blender内置Python缺少 requests/numpy')
    for name,arguments in (('Blender',[root/'blender','--version']),('FFmpeg',[root/'ffmpeg','-version']),
                           ('ffprobe',[root/'ffprobe','-version'])):
        if Path(arguments[0]).is_file() and ((root/BLENDER_NAME/'blender').is_file() if name=='Blender' else
                                             (root/'ffmpeg-env/bin'/name.lower()).is_file()):
            if subprocess.run([str(value) for value in arguments],capture_output=True,env=env).returncode:
                issues.append(name+'无法执行；检查系统共享库或重新准备本地运行时')
    home = model_home(root,env)
    if not (home/'config.yaml').is_file():
        issues.append('Hermes模型配置尚未准备；填写 .env 后运行 ./run.sh --setup-only')
    if not (home/'skills/creative/white-model-director/SKILL.md').is_file():
        issues.append('Hermes项目Skill尚未安装')
    assets = asset_command(root, web if web.is_file() else sys.executable, check=True)
    for issue in issues:
        print('未就绪：'+issue, file=sys.stderr)
    if not issues and assets==0:
        print('本地依赖、模型配置文件与 core 素材检查通过；未验证API认证、未调用模型或运行生成任务。')
        return 0
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description='Linux本地安装/启动；首次联网下载工具，不自动生成或调用模型')
    parser.add_argument('--check',action='store_true',help='只读检查，不下载、不写配置、不启动服务')
    parser.add_argument('--setup-only',action='store_true',help='只准备依赖/配置/素材，不启动服务')
    parser.add_argument('--serve-web',action='store_true',help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if sys.version_info < (3,10) or not hasattr(tarfile,'data_filter'):
        raise RuntimeError('启动入口需要 Python 3.10 或更高版本及最新补丁；请更新系统 Python 后重试（项目依赖会另建 Python 3.12 环境）')
    env = project_environment(PROJECT)
    if args.serve_web:
        os.execve(sys.executable,[sys.executable,str(PROJECT/'web/app.py')],env)
    if args.check:
        return check(PROJECT,env)
    issues = system_issues()
    if issues:
        raise RuntimeError('\n'.join(issues))
    validate_model_configuration(PROJECT,env)
    uv = ensure_uv(PROJECT)
    env['PATH'] = str(Path(uv).parent)+os.pathsep+env['PATH']
    web = ensure_web(PROJECT,uv,env)
    ensure_hermes(PROJECT,uv,env)
    ensure_blender(PROJECT,uv,env)
    ensure_ffmpeg(PROJECT)
    configure_model(PROJECT,env,uv)
    if asset_command(PROJECT,web):
        print('素材仍有缺项；按上方来源与放置路径处理，再运行 ./run.sh。未启动服务。',file=sys.stderr)
        return 1
    if args.setup_only:
        print('本地准备完成；未启动服务或生成任务。运行 ./run.sh 启动。')
        return 0
    return subprocess.run(['bash',str(PROJECT/'start_all.sh')],env=env,cwd=PROJECT).returncode


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError,OSError) as error:
        print('启动准备失败：'+str(error),file=sys.stderr)
        raise SystemExit(1)
