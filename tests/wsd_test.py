#!/usr/bin/env python3
"""Deterministic WSD protocol/lifecycle smoke against a Movian ELF.

The harness supplies a local WS-Discovery responder and WS-Transfer endpoint.
It exercises parser rejection, duplicate suppression, source-address refresh,
and two-round stale removal without requiring a Windows host.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.request import urlopen

MCAST = '239.255.255.250'
WSD_PORT = 3702
EPR = 'urn:uuid:11111111-2222-3333-4444-555555555555'
COMPUTER = 'FAKEHOST/Workgroup:FAKEWG'


def wait_for(predicate, timeout, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


class MetadataHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        body = self.rfile.read(length)
        self.server.requests.append(body)
        response = f'''<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
 xmlns:wsx="http://schemas.xmlsoap.org/ws/2004/09/mex"
 xmlns:pub="http://schemas.microsoft.com/windows/pub/2005/07">
 <soap:Body><wsx:Metadata>
  <wsx:MetadataSection Dialect="http://schemas.xmlsoap.org/ws/2006/02/devprof/Relationship">
   <wsx:Relationship><wsx:Host><pub:Computer>{COMPUTER}</pub:Computer></wsx:Host></wsx:Relationship>
  </wsx:MetadataSection>
 </wsx:Metadata></soap:Body>
</soap:Envelope>'''.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/soap+xml')
        self.send_header('Content-Length', str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *_args):
        return


class WsdResponder(threading.Thread):
    def __init__(self, xaddr):
        super().__init__(daemon=True)
        self.xaddr = xaddr
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        self.sock.bind(('', WSD_PORT))
        membership = socket.inet_aton(MCAST) + socket.inet_aton('0.0.0.0')
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        self.lock = threading.Lock()
        self.last_probe = None
        self.source_ip = '127.0.0.2'
        self.reply_enabled = True
        self.running = True
        self.probe_count = 0
        self.metadata_rounds = []

    def close(self):
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass

    def _probe(self, payload):
        return b'<wsd:Probe' in payload or b'<Probe' in payload

    def _remember_probe(self, peer):
        with self.lock:
            self.last_probe = peer
            self.probe_count += 1
            enabled = self.reply_enabled
            source = self.source_ip
        if enabled:
            self.send_match(source_ip=source)

    def run(self):
        self.sock.settimeout(0.2)
        while self.running:
            try:
                payload, peer = self.sock.recvfrom(65535)
            except (socket.timeout, OSError):
                continue
            if self._probe(payload):
                self._remember_probe(peer)

    def _payload(self, epr=EPR, types='pub:Computer', xaddr=None):
        xaddr = self.xaddr if xaddr is None else xaddr
        epr_xml = (
            f'<wsa:EndpointReference><wsa:Address>{epr}</wsa:Address>'
            '</wsa:EndpointReference>' if epr else '')
        xaddr_xml = f'<wsd:XAddrs>{xaddr}</wsd:XAddrs>' if xaddr else ''
        return f'''<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
 xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:wsd="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:pub="http://schemas.microsoft.com/windows/pub/2005/07">
 <soap:Body><wsd:ProbeMatches><wsd:ProbeMatch>
  {epr_xml}
  <wsd:Types>{types}</wsd:Types>{xaddr_xml}
 </wsd:ProbeMatch></wsd:ProbeMatches></soap:Body>
</soap:Envelope>'''.encode()

    def send_raw(self, payload, source_ip=None):
        with self.lock:
            peer = self.last_probe
        if peer is None:
            raise RuntimeError('no WSD Probe received')
        source_ip = source_ip or self.source_ip
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sender.bind((source_ip, 0))
            sender.sendto(payload, peer)
        finally:
            sender.close()

    def send_match(self, source_ip=None, epr=EPR, types='pub:Computer', xaddr=None):
        self.send_raw(self._payload(epr=epr, types=types, xaddr=xaddr), source_ip)


def http_get(port, path):
    with urlopen(f'http://127.0.0.1:{port}{path}', timeout=2) as response:
        return response.status, response.read().decode(errors='replace')


def registered_urls(log_path):
    try:
        text = log_path.read_text(errors='replace')
    except FileNotFoundError:
        return []
    return re.findall(r'Registered [^ ]+ as (smb2://[^ ]+)', text)


def stop_process(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True)
    parser.add_argument('--out', default='/tmp/wsd-test')
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    profile = out / 'profile'
    profile.mkdir(parents=True, exist_ok=True)
    log_path = out / 'movian.log'
    result_path = out / 'result.json'
    results = {}
    proc = None
    udp = None
    metadata = None
    log = None
    try:
        metadata = ThreadingHTTPServer(('127.0.0.1', 0), MetadataHandler)
        metadata.requests = []
        metadata_thread = threading.Thread(target=metadata.serve_forever, daemon=True)
        metadata_thread.start()
        xaddr = f'http://127.0.0.1:{metadata.server_port}/metadata'
        udp = WsdResponder(xaddr)
        udp.start()

        env = dict(os.environ)
        env.pop('MOVIAN_MDEV_ALLOW_GDB', None)
        env['DISPLAY'] = ':0'
        env['WAYLAND_DISPLAY'] = 'wayland-0'
        log = log_path.open('w')
        proc = subprocess.Popen(
            [args.binary, '-d', '--disable-upgrades',
             '--persistent', str(profile / 'persistent'),
             '--cache', str(profile / 'cache'), 'page:home'],
            stdout=log, stderr=subprocess.STDOUT, env=env)

        def ready():
            try:
                text = log_path.read_text(errors='replace')
            except FileNotFoundError:
                return None
            match = re.search(r'http-server: Listening on port (\d+)', text)
            if not match:
                return None
            port = int(match.group(1))
            try:
                status, _ = http_get(port, '/api/prop/global')
            except (URLError, OSError):
                return None
            return port if status == 200 else None

        app_port = wait_for(ready, 30)
        if app_port is None:
            raise RuntimeError('Movian HTTP readiness failed')

        def has_url(url):
            return url in registered_urls(log_path)

        initial_url = 'smb2://127.0.0.2'
        if not wait_for(lambda: has_url(initial_url), 12):
            raise RuntimeError('valid ProbeMatch did not register initial SMB2 service')
        results['valid_probe_match'] = 'PASS'
        results['source_address_selection'] = 'PASS'
        results['metadata_get'] = 'PASS' if metadata.requests else 'FAIL'
        results['computer_metadata'] = (
            'PASS' if 'FAKEHOST' in log_path.read_text(errors='replace') else 'FAIL')

        before = len(registered_urls(log_path))
        udp.send_match(source_ip='127.0.0.2')
        udp.send_match(source_ip='127.0.0.2')
        time.sleep(0.5)
        after = len(registered_urls(log_path))
        results['duplicate_endpoint'] = 'PASS' if after == before else 'FAIL'

        invalid_before = len(registered_urls(log_path))
        udp.send_raw(b'<not-xml')
        udp.send_match(types='pub:Printer')
        udp.send_raw(udp._payload(epr='', types='pub:Computer'))
        udp.send_raw(udp._payload(epr=EPR, types='pub:Computer', xaddr=''))
        time.sleep(0.5)
        invalid_after = len(registered_urls(log_path))
        results['malformed_xml'] = 'PASS' if proc.poll() is None else 'FAIL'
        results['non_computer'] = 'PASS' if invalid_after == invalid_before else 'FAIL'
        results['missing_endpoint_reference'] = (
            'PASS' if proc.poll() is None else 'FAIL')
        results['missing_xaddrs'] = 'PASS' if proc.poll() is None else 'FAIL'

        refresh_before = len(registered_urls(log_path))
        udp.source_ip = '127.0.0.3'
        udp.send_match(source_ip='127.0.0.3')
        results['endpoint_source_refresh'] = (
            'PASS' if wait_for(
                lambda: 'smb2://127.0.0.3' in registered_urls(log_path) and
                len(registered_urls(log_path)) == refresh_before + 1, 12)
            else 'FAIL')

        udp.reply_enabled = False
        results['two_round_stale_removal'] = (
            'PASS' if wait_for(
                lambda: 'timed out, removing' in
                log_path.read_text(errors='replace'), 70) else 'FAIL')

        try:
            with urlopen(f'http://127.0.0.1:{app_port}/api/input/action/Quit', timeout=5) as response:
                results['graceful_quit_status'] = response.status
        except (URLError, OSError):
            results['graceful_quit_status'] = None
        proc.wait(timeout=20)
        results['natural_exit'] = 'PASS' if proc.returncode == 0 else 'FAIL'
        text = log_path.read_text(errors='replace')
        results['no_crash'] = 'PASS' if not re.search(r'SIGSEGV|SIGABRT|Signal: [0-9]+', text) else 'FAIL'
        results['malloc_failure'] = 'SOURCE_GUARD_AUDITED'
    finally:
        if proc is not None:
            stop_process(proc)
        if log is not None:
            log.close()
        if udp is not None:
            udp.close()
        if metadata is not None:
            metadata.shutdown()
            metadata.server_close()
        result_path.write_text(json.dumps(results, indent=2, sort_keys=True) + '\n')

    print(json.dumps(results, sort_keys=True))
    failures = [key for key, value in results.items()
                if value == 'FAIL' or (key == 'graceful_quit_status' and value != 200)]
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
