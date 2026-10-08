"""Three-round Docker/PostgreSQL smoke check in a unique disposable project.

Requires Docker Compose >=2.24.4 and free localhost ports 18080/18081.
Never targets the regular ECDAT Compose project or its database volume.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def request(path, token=None, payload=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request('http://127.0.0.1:18080' + path, data=data, headers=headers)
    # The URL is assembled under the fixed HTTP loopback origin above.
    with urllib.request.urlopen(req, timeout=10) as response:  # nosec B310
        return json.load(response)


def main():
    if not shutil.which('docker'):
        raise SystemExit('BLOCKED: Docker is not installed; no deployment tests ran.')
    project = 'ecdat-verify-' + uuid.uuid4().hex[:12]
    env = os.environ.copy()
    password = secrets.token_urlsafe(32)
    env.update(ECDAT_DB_PASSWORD=secrets.token_hex(32), ECDAT_TOKEN_SECRET=secrets.token_hex(32),
               ECDAT_USERS_JSON=json.dumps({'verifier': {'role': 'admin', 'password': password}}))
    with tempfile.TemporaryDirectory(prefix='ecdat-compose-') as directory:
        override = Path(directory) / 'override.yaml'
        override.write_text('''services:
  db:
    ports: !reset []
  backend:
    ports: !override ["127.0.0.1:18080:8000"]
    environment:
      ECDAT_CORS_ORIGINS: http://127.0.0.1:18081
  dashboard:
    ports: !override ["127.0.0.1:18081:3000"]
    build:
      args:
        VITE_API_URL: http://127.0.0.1:18080
''', encoding='utf-8')
        command = ['docker', 'compose', '-p', project, '-f', str(ROOT / 'docker-compose.yml'),
                   '-f', str(override)]
        def compose(*args):
            subprocess.run(command + list(args), cwd=ROOT, env=env, check=True, timeout=600)
        try:
            compose('config', '--quiet')  # never print generated secrets
            compose('up', '-d', '--build', '--wait', '--wait-timeout', '180')
            for round_no in range(1, 4):
                require(request('/ready')['database'] == 'reachable', 'Database readiness check failed')
                try:
                    request('/api/assets')
                    raise AssertionError('Anonymous inventory was accessible')
                except urllib.error.HTTPError as exc:
                    require(exc.code == 401, f'Anonymous inventory returned HTTP {exc.code}')
                token = request('/api/auth/login', payload={'username': 'verifier', 'password': password})['access_token']
                scan = request('/api/scan', token, {'repo_path': '/test-repo'})['scan_id']
                deadline = time.monotonic() + 120
                while True:
                    state = request(f'/api/scans/{scan}', token)
                    if state['status'] == 'completed':
                        break
                    require(state['status'] in {'queued', 'pending', 'running'},
                            f"Unexpected scan status: {state['status']}; "
                            f"diagnostics: {state.get('blind_spots', [])}")
                    if time.monotonic() >= deadline:
                        raise AssertionError('Docker scan timed out')
                    time.sleep(0.2)
                require(state['assets_found'] > 0, 'Docker scan returned no assets')
                require(request('/api/evaluation', token)['recall'] == 1.0,
                        'Controlled evaluation recall was not 1.0')
                require(bool(request('/api/cbom', token)['components']), 'CBOM contained no components')
                # The dashboard verifier always targets this fixed loopback URL.
                with urllib.request.urlopen(  # nosec B310
                    'http://127.0.0.1:18081', timeout=10,
                ) as response:
                    require(response.status == 200, f'Dashboard returned HTTP {response.status}')
                compose('exec', '-T', 'db', 'psql', '-U', 'ecdat', '-d', 'ecdat', '-c', 'SELECT 1;')
                compose('restart', 'backend')
                for attempt in range(50):
                    try:
                        require(request(f'/api/scans/{scan}', token)['status'] == 'completed',
                                'Persisted scan was not completed after backend restart')
                        break
                    except (OSError, RuntimeError):
                        if attempt == 49:
                            raise
                        time.sleep(0.2)
                print(f'Docker/PostgreSQL round {round_no}/3 passed; persisted scan {scan}')
        finally:
            # Only this randomly named verification project is destroyed.
            compose('down', '--volumes', '--remove-orphans')


if __name__ == '__main__':
    main()
