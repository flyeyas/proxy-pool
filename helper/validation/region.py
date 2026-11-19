"""Optional, independently supervised geolocation; failure never means dead proxy."""
import ipaddress
import json
import multiprocessing
import time
from urllib.parse import quote

import urllib3
from .worker import supervised


def _lookup(connection, host):
    manager = urllib3.PoolManager(cert_reqs='CERT_REQUIRED')
    try:
        ipaddress.ip_address(host)
        response = manager.request('GET', 'https://api.ip.sb/geoip/' + quote(host, safe=''),
                                   timeout=urllib3.Timeout(connect=2, read=2), retries=False,
                                   redirect=False, preload_content=False)
        try:
            payload = json.loads(response.read(4097)) if response.status == 200 else {}
            country = payload.get('country_code', '')
            connection.send(country if isinstance(country, str) and len(country) == 2 and country.isalpha() else '')
        finally:
            response.close()
    except Exception:
        try:
            connection.send('')
        except (OSError, EOFError):
            pass
    finally:
        manager.clear()
        connection.close()


def lookup_country(host, deadline=5):
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=supervised, args=(_lookup, (child, host), time.monotonic() + deadline))
    process.start()
    child.close()
    try:
        return parent.recv().upper() if parent.poll(deadline) else ''
    except (EOFError, OSError):
        return ''
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=0.5)
        if process.is_alive():
            process.kill()
            process.join(timeout=0.5)
        parent.close()
        process.close()
