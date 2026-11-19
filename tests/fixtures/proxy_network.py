"""Real CONNECT/TLS/SOCKS servers for end-to-end routing assertions."""
import base64
import contextlib
import gzip
import ipaddress
import json
import select
import socket
import socketserver
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def certificates(directory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Validation Test CA')])
    ca = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
          .public_key(key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=7))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                      key_encipherment=False, data_encipherment=False,
                                      key_agreement=False, key_cert_sign=True, crl_sign=True,
                                      encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
          .sign(key, hashes.SHA256()))
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'remote.test')]))
            .issuer_name(subject).public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=7))
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('remote.test'), x509.DNSName('localhost'),
                                                       x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
            .sign(key, hashes.SHA256()))
    ca_path, cert_path, key_path = [directory / n for n in ('ca.pem', 'leaf.pem', 'key.pem')]
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(leaf_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
    key_path.chmod(0o600)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_path), str(key_path))
    return str(ca_path), ctx


def exact(sock, n):
    value = b''
    while len(value) < n:
        chunk = sock.recv(n - len(value))
        if not chunk:
            raise EOFError
        value += chunk
    return value


def cstring(sock):
    value = bytearray()
    while len(value) < 4096:
        char = exact(sock, 1)
        if char == b'\0':
            return bytes(value)
        value.extend(char)
    raise ValueError('overlong test string')


def tunnel(client, upstream):
    until = time.monotonic() + 120
    while time.monotonic() < until:
        pending = [s for s in (client, upstream) if isinstance(s, ssl.SSLSocket) and s.pending()]
        ready = pending or select.select([client, upstream], [], [], 0.05)[0]
        for source in ready:
            data = source.recv(16384)
            if not data:
                return
            (upstream if source is client else client).sendall(data)


class QuietServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def handle_error(self, request, address):
        pass


@contextlib.contextmanager
def proxy_service(kind, tls=None, authentication=False):
    events = []
    stop = threading.Event()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            client = self.request
            upstream = None
            try:
                client.settimeout(3)
                if kind == 'stall':
                    stop.wait(10)
                    return
                if tls:
                    client = tls.wrap_socket(client, server_side=True)
                if kind == 'http':
                    request = b''
                    while b'\r\n\r\n' not in request and len(request) < 16384:
                        request += exact(client, 1)
                    if not request.startswith(b'CONNECT '):
                        client.sendall(b'HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n')
                        return
                    headers = {}
                    for line in request.split(b'\r\n')[1:]:
                        name, separator, value = line.partition(b':')
                        if separator:
                            headers[name.lower()] = value.strip()
                    if authentication and headers.get(b'proxy-authorization') != b'Basic ' + base64.b64encode(b'user:pass'):
                        client.sendall(b'HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n')
                        return
                    host, port = request.split(b' ', 2)[1].decode().rsplit(':', 1)
                    port = int(port)
                    reply = b'HTTP/1.1 200 Connection established\r\n\r\n'
                elif kind == 'socks5':
                    version, count = exact(client, 2)
                    assert version == 5
                    methods = exact(client, count)
                    method = 2 if authentication else 0
                    if method not in methods:
                        client.sendall(b'\x05\xff')
                        return
                    client.sendall(bytes([5, method]))
                    if method == 2:
                        assert exact(client, 1) == b'\x01'
                        user = exact(client, exact(client, 1)[0])
                        password = exact(client, exact(client, 1)[0])
                        accepted = user == b'user' and password == b'pass'
                        client.sendall(bytes([1, 0 if accepted else 1]))
                        if not accepted:
                            return
                    version, command, reserved, address_type = exact(client, 4)
                    assert (version, command) == (5, 1)
                    if address_type == 1:
                        host = socket.inet_ntop(socket.AF_INET, exact(client, 4))
                    elif address_type == 4:
                        host = socket.inet_ntop(socket.AF_INET6, exact(client, 16))
                    else:
                        host = exact(client, exact(client, 1)[0]).decode()
                    port = int.from_bytes(exact(client, 2), 'big')
                    reply = b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00'
                else:
                    version, command = exact(client, 2)
                    assert (version, command) == (4, 1)
                    port = int.from_bytes(exact(client, 2), 'big')
                    address = exact(client, 4)
                    cstring(client)
                    host = cstring(client).decode() if address[:3] == b'\0\0\0' and address[3] else socket.inet_ntoa(address)
                    reply = b'\x00\x5a\x00\x00\x00\x00\x00\x00'
                events.append({'host': host, 'port': port, 'kind': kind, 'tls_proxy': bool(tls)})
                upstream = socket.create_connection(('127.0.0.1' if host in ('remote.test', 'localhost', '::1') else host, port), timeout=3)
                client.sendall(reply)
                tunnel(client, upstream)
            except (OSError, EOFError, AssertionError, ValueError):
                pass
            finally:
                if upstream:
                    upstream.close()
                client.close()

    server = QuietServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], events
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextlib.contextmanager
def target_service(tls, source_payload=None):
    events = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            events.append({'path': self.path, 'proxy_authorization': self.headers.get('Proxy-Authorization'),
                           'time': time.monotonic()})
            try:
                if self.path == '/redirect':
                    self.send_response(302)
                    self.send_header('Location', '/ok')
                    self.end_headers()
                    return
                if self.path == '/escape':
                    self.send_response(302)
                    self.send_header('Location', 'https://evil.test/ok')
                    self.end_headers()
                    return
                body = b'<html>Google Tencent qq.com</html>'
                if self.path == '/exit':
                    body = json.dumps({'ip': '198.51.100.24', 'country_code': 'US'}).encode()
                if self.path == '/source':
                    body = json.dumps(source_payload or {}).encode()
                if self.path == '/captcha':
                    body = b'Google captcha verify you are human'
                if self.path == '/huge':
                    body += b'x' * 1000000
                if self.path == '/gzip':
                    body = gzip.compress(body)
                self.send_response(429 if self.path == '/limited' else 200)
                if self.path == '/gzip':
                    self.send_header('Content-Encoding', 'gzip')
                if self.path == '/limited':
                    self.send_header('Retry-After', '60')
                if self.path != '/slow':
                    self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                if self.path == '/slow':
                    for _ in range(2000):
                        self.wfile.write(b'x')
                        self.wfile.flush()
                        time.sleep(0.05)
                else:
                    self.wfile.write(body)
            except (OSError, ssl.SSLError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    if tls is not None:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], events
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
