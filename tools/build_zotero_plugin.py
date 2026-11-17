"""Build the local XPI, validating the installed Zotero loader's manifest contract."""
from __future__ import annotations
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent


def validate_manifest(manifest):
    app = manifest.get('applications', {}).get('zotero', {})
    # Zotero's parser imposes these in addition to the base extension schema.
    required = {'id', 'update_url', 'strict_max_version'}
    omni = Path('C:/Program Files/Zotero/omni.ja')
    if omni.exists():
        with zipfile.ZipFile(omni) as z:
            source = z.read('modules/Extension.sys.mjs').decode('utf-8')
        required.update(re.findall(r'manifestError\("applications\.zotero\.([a-z_]+) not provided"\)', source))
    absent = sorted(k for k in required if not app.get(k))
    if absent:
        raise ValueError('Zotero required manifest fields missing: ' + ', '.join(absent))
    if manifest.get('manifest_version') != 2:
        raise ValueError('This bootstrap plugin requires manifest_version=2')
    u = urlparse(app['update_url'])
    if u.scheme != 'https' or not u.netloc:
        raise ValueError('update_url must be HTTPS; do not disable update security')
    if '*' in app.get('strict_min_version', ''):
        raise ValueError('strict_min_version must not contain a wildcard')
    if app.get('strict_min_version') != '10.0' or app['strict_max_version'] != '10.0.*':
        raise ValueError('This build is validated for Zotero 10.0.x')


def build():
    manifest = json.loads((ROOT / 'zotero-plugin/manifest.json').read_text(encoding='utf-8'))
    validate_manifest(manifest)
    inbox = ROOT / 'Doclab-Inbox'
    cfg = json.loads((inbox / 'config/intake.local.json').read_text(encoding='utf-8'))
    plugin_cfg = {'token': cfg['bridge_token'], 'jobs_root': str(inbox / 'jobs'),
                  'allowed_roots': [str(inbox / 'jobs'), str(inbox / 'inbox')]}
    dist = ROOT / 'zotero-plugin/dist'
    dist.mkdir(parents=True, exist_ok=True)
    xpi = dist / 'doclab-archive.xpi'
    temporary = dist / 'doclab-archive.building'
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in ('manifest.json', 'bootstrap.js'):
            z.write(ROOT / 'zotero-plugin' / name, name)
        z.writestr('bridge-config.json', json.dumps(plugin_cfg, ensure_ascii=False))
    with zipfile.ZipFile(temporary) as z:
        if z.testzip() is not None:
            raise ValueError('XPI failed ZIP integrity verification')
        validate_manifest(json.loads(z.read('manifest.json')))
        if set(z.namelist()) != {'manifest.json', 'bootstrap.js', 'bridge-config.json'}:
            raise ValueError('Unexpected package layout')
    temporary.replace(xpi)
    return {'ok': True, 'version': manifest['version'], 'zotero_xpi': str(xpi)}


if __name__ == '__main__':
    print(json.dumps(build(), ensure_ascii=False))
