import os
import socket


def _load_env_file(path='.env'):
    if not os.path.exists(path):
        return
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_env_file()

from imagedetection import creat_app

app = creat_app()


def _port_available(host, port):
    bind_host = '' if host == '0.0.0.0' else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((bind_host, port))
        except OSError:
            return False
    return True


def _pick_port(host, start_port):
    if os.environ.get('REALGUARD_PORT'):
        return start_port
    for port in range(start_port, start_port + 20):
        if _port_available(host, port):
            return port
    return start_port


if __name__ == '__main__':
    host = os.environ.get('REALGUARD_HOST', '0.0.0.0')
    port = int(os.environ.get('REALGUARD_PORT', '5000'))
    port = _pick_port(host, port)
    app.run(host=host, port=port)
