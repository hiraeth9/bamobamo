"""Install the bundled licensed starter assets without changing local extensions.

No download, model call, rendering or scene generation is involved. Existing
destination files are kept. --check only reads the runtime paths.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

PROJECT = Path(__file__).resolve().parents[1]
MANIFEST = Path('knowledge/assets/distribution.json')


def bootstrap_assets(project=None, *, profile='core', check=False):
    project = Path(project) if project is not None else PROJECT
    manifest = json.loads((project / MANIFEST).read_text(encoding='utf-8'))
    if profile not in manifest['profiles']:
        raise ValueError('Unknown asset profile: '+profile)
    installed, retained, missing = [], [], []
    for asset in manifest['profiles'][profile]['assets']:
        target = project / asset['path']
        if target.is_file():
            retained.append(asset['path'])
            continue
        source = project / asset['bundled_source'] if asset.get('bundled_source') else None
        if not check and source is not None and source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            installed.append(asset['path'])
        else:
            missing.append({'id': asset['id'], 'path': asset['path'],
                            'reason': ('Runtime file is missing; run bootstrap without --check.' if check and source is not None and source.is_file()
                                       else 'Required bundled file is missing; restore it from this repository.'),
                            **({'bundled_source': asset['bundled_source']} if source is not None else {})})
    return {'ok': not missing, 'profile': profile, 'check_only': check,
            'installed': installed, 'retained': retained, 'missing': missing,
            'scope': 'Bundled runtime files under their individual source terms; Mixamo/Rokoko extensions are not downloaded or overwritten.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='core')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--project', type=Path, default=None)
    args = parser.parse_args(argv)
    result = bootstrap_assets(args.project, profile=args.profile, check=args.check)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
