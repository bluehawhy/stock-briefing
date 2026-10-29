"""Read only deployment paths; never emit container environment values."""
import argparse
import json
import os
from pathlib import Path
import sys

SECRET_TARGET = '/run/stock-briefing-secrets'


def readable(path):
    if not path.is_file() or not os.access(path, os.R_OK):
        raise ValueError('Deployment environment file is missing or unreadable')
    return str(path)


def context(container, root, env_files):
    labels = container['Config']['Labels']
    directory = Path(labels['com.docker.compose.project.working_dir']).resolve()
    if directory != root.resolve():
        raise ValueError('Deployment working directory mismatch')
    files = labels['com.docker.compose.project.config_files'].split(',')
    if not files or any(not Path(p).is_absolute() or not Path(p).is_file() for p in files):
        raise ValueError('Deployment Compose files are missing')
    if not env_files:
        saved = labels.get('com.docker.compose.project.environment_file', '')
        env_files = saved.split(',') if saved else [str(directory / '.env')]
    env_files = [readable((directory / p).resolve()) for p in env_files]
    arguments = ['docker', 'compose', '--project-name', labels['com.docker.compose.project'],
                 '--project-directory', str(directory)]
    for path in env_files:
        arguments += ['--env-file', path]
    for path in files:
        arguments += ['--file', path]
    mounts = container['Mounts']
    secret = next((m for m in mounts if m['Destination'] == SECRET_TARGET and m['Type'] == 'bind'), None)
    if not secret:
        raise ValueError('Existing broker secret bind mount is required')
    # Select only the SQLite path/backend needed for offline migration.
    environment = dict(value.split('=', 1) for value in container['Config']['Env'])
    sqlite = Path(environment.get('SQLITE_PATH', '/data/stock-briefing.db'))
    host_path = ''
    for mount in sorted(mounts, key=lambda m: len(m['Destination']), reverse=True):
        if sqlite.is_relative_to(mount['Destination']):
            host_path = str(Path(mount['Source']) / sqlite.relative_to(mount['Destination']))
            break
    return {'arguments': arguments, 'secret_directory': str(Path(secret['Source']).resolve()),
            'deployment': [labels['com.docker.compose.project'], str(directory), ','.join(files), host_path, str(sqlite)],
            'backend': environment.get('STOCK_DATABASE_BACKEND', 'sqlite')}


def validate(configuration, secret_directory):
    expected = Path(secret_directory).resolve()
    services = configuration['services']
    for name in ('api',):
        mounts = services[name].get('volumes', [])
        mount = next((m for m in mounts if m['target'] == SECRET_TARGET), None)
        if not mount or mount.get('type') != 'bind' or Path(mount['source']).resolve() != expected:
            raise ValueError('Resolved broker secret mount differs from running deployment')
    mount = next((m for m in services['db'].get('volumes', []) if m['target'] == '/run/secrets/postgres_password'), None)
    if not mount or mount.get('type') != 'bind' or Path(mount['source']).resolve() != expected / 'postgres_password':
        raise ValueError('Resolved database password mount differs from running deployment')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    read = sub.add_parser('read')
    read.add_argument('directory', type=Path)
    read.add_argument('--env-file', action='append', default=[])
    check = sub.add_parser('validate')
    check.add_argument('secret_directory')
    args = parser.parse_args()
    try:
        data = json.load(sys.stdin)
        if args.command == 'read':
            print(json.dumps(context(data[0], args.directory, args.env_file)))
        else:
            validate(data, args.secret_directory)
    except (KeyError, IndexError, TypeError, ValueError, OSError):
        # Neither raw inspect/config input nor secret values belong in output.
        raise SystemExit('Deployment context invalid: check environment files and secret mount paths') from None


if __name__ == '__main__':
    main()
