import importlib.util
import json
from pathlib import Path
import os
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('compose_context', ROOT / 'scripts/compose-context.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def container(root, label=None):
    labels = {'com.docker.compose.project': 'stock',
              'com.docker.compose.project.working_dir': str(root),
              'com.docker.compose.project.config_files': str(root / 'compose.yaml') + ',' + str(root / 'scheduler.yaml')}
    if label is not None: labels['com.docker.compose.project.environment_file'] = label
    return {'Config': {'Labels': labels, 'Env': ['SQLITE_PATH=/data/stock.db', 'DO_NOT_PRINT=TOP_SECRET']},
            'Mounts': [{'Type': 'bind', 'Source': str(root / 'secrets'), 'Destination': module.SECRET_TARGET},
                       {'Type': 'bind', 'Source': str(root), 'Destination': '/data'}]}


@pytest.fixture
def deployment(tmp_path):
    for name in ['compose.yaml', 'scheduler.yaml', '.env', 'first.env', 'second.env']:
        (tmp_path / name).write_text('TEST_VALUE=not-secret\n')
    return tmp_path


@pytest.mark.parametrize('mode', ['default', 'label', 'explicit'])
def test_environment_files_and_compose_order_preserved(deployment, mode):
    root = deployment
    label = str(root / 'first.env') + ',' + str(root / 'second.env')
    c = container(root, label if mode == 'label' else None)
    data = module.context(c, root, ['first.env', 'second.env'] if mode == 'explicit' else [])
    expected = [str(root / '.env')] if mode == 'default' else [str(root / 'first.env'), str(root / 'second.env')]
    args = data['arguments']
    assert [args[i + 1] for i, v in enumerate(args) if v == '--env-file'] == expected
    assert [args[i + 1] for i, v in enumerate(args) if v == '--file'] == [str(root / 'compose.yaml'), str(root / 'scheduler.yaml')]
    assert 'TOP_SECRET' not in json.dumps(data)


def test_missing_explicit_file_does_not_fall_back(deployment):
    with pytest.raises(ValueError):
        module.context(container(deployment), deployment, ['absent.env'])


def configuration(root, changed=False):
    source = str(root / ('wrong' if changed else 'secrets'))
    return {'services': {**{n: {'volumes': [{'type': 'bind', 'source': source, 'target': module.SECRET_TARGET}]} for n in ('api', 'telegram')},
                         'db': {'volumes': [{'type': 'bind', 'source': source + '/postgres_password', 'target': '/run/secrets/postgres_password'}]}}}


def test_changed_secret_mount_is_rejected(deployment):
    module.validate(configuration(deployment), str(deployment / 'secrets'))
    with pytest.raises(ValueError):
        module.validate(configuration(deployment, True), str(deployment / 'secrets'))


@pytest.mark.parametrize('script', ['migrate-postgres.sh', 'update-server.sh'])
def test_scripts_abort_before_side_effects_on_mount_drift(tmp_path, script):
    import shutil
    root = tmp_path / 'repo'
    root.mkdir()
    shutil.copytree(ROOT / 'scripts', root / 'scripts')
    for name in ['compose.yaml', 'scheduler.yaml', '.env']:
        (root / name).write_text('')
    (root / 'stock.db').touch()
    fakebin = root / 'bin'; fakebin.mkdir()
    inspect = root / 'inspect.json'; inspect.write_text(json.dumps([container(root)]))
    config = root / 'config.json'; config.write_text(json.dumps(configuration(root, True)))
    log = root / 'calls'
    docker = fakebin / 'docker'
    docker.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['CALL_LOG'],'a') as f:f.write(json.dumps(args)+'\\n')
if args[0]=='inspect': print(Path(os.environ['INSPECT']).read_text())
elif 'config' in args: print(Path(os.environ['CONFIG']).read_text())
else: raise SystemExit('UNEXPECTED SIDE EFFECT')
''')
    docker.chmod(0o755)
    git = fakebin / 'git'
    git.write_text('#!/bin/sh\nif [ "$1" = branch ]; then echo main; fi\nif [ "$1" = rev-parse ]; then echo test-sha; fi\n')
    git.chmod(0o755)
    env = {**os.environ, 'PATH': str(fakebin) + ':' + os.environ['PATH'],
           'CALL_LOG': str(log), 'INSPECT': str(inspect), 'CONFIG': str(config)}
    result = subprocess.run(['bash', str(root / 'scripts' / script)], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Deployment context invalid' in result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == 2
    assert not (root / 'wrong').exists()
    assert 'TOP_SECRET' not in result.stdout + result.stderr


def test_unreadable_environment_file_is_rejected(deployment, monkeypatch):
    monkeypatch.setattr(module.os, 'access', lambda *args: False)
    with pytest.raises(ValueError):
        module.context(container(deployment), deployment, [])


def test_explicit_files_override_stale_saved_paths(deployment):
    data = module.context(container(deployment, '/missing/old.env'), deployment, ['first.env', 'second.env'])
    assert str(deployment / 'first.env') in data['arguments']
    assert '/missing/old.env' not in data['arguments']
