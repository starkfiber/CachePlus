"""Read-only CachePlus HTTP API, bound to loopback only."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import bank
import cachectl


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Paths passed to /v1/assess can be private; keep them out of stderr.
        pass

    def do_GET(self):
        parsed = urlsplit(self.path)
        try:
            if parsed.path == "/v1/status":
                payload = bank.status()
            elif parsed.path == "/v1/backends":
                config = cachectl.load_config()
                payload = {"active": config.get("active_profile", "default"),
                           "backends": [{"name": name, "kind": entry.get("kind", "external"),
                                         "path": entry["volume"],
                                         "mounted": cachectl.os.path.ismount(entry["volume"])}
                                        for name, entry in cachectl.profiles(config).items()]}
            elif parsed.path == "/v1/assess":
                path = parse_qs(parsed.query).get("path", [None])[0]
                if not path:
                    raise ValueError("path query parameter is required")
                payload = cachectl.assess(path)
            else:
                self.send_error(404)
                return
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
        except (ValueError, RuntimeError) as exc:
            body = json.dumps({"error": str(exc)}).encode("utf-8")
            self.send_response(400)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # A timed-out local client may close while an earlier request finishes.
            pass

    def do_POST(self):
        self.send_error(405, "CachePlus HTTP API is read-only; use the local CLI for jobs")


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be 1–65535")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"CachePlus API listening on http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
