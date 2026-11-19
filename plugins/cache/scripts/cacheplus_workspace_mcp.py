"""Authenticated stdio MCP connector for Cache++ Space and Universe."""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import http.cookiejar
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://starkfiber.my/cacheplus"
MAX_BYTES = 256 * 1024
MAX_METADATA = 8 * 1024 * 1024
MAX_LINE = 512 * 1024
OBJECT_ID = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}")
TIERS = {"Space", "Universe"}
PROTOCOL = "2025-06-18"
SUPPORTED = {PROTOCOL, "2025-03-26", "2024-11-05"}


class WorkspaceError(Exception):
    """A bounded error safe to return to the MCP client."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise WorkspaceError("The Cache++ server redirected. No credentials or files were forwarded.")


def read_credentials(path):
    if not os.path.isabs(path):
        raise WorkspaceError("Use an absolute credentials file path.")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise WorkspaceError("Use a private credentials file with mode 0600.")
        data = json.loads(stream.read(4097))
    if not isinstance(data, dict):
        raise WorkspaceError("Credentials must be a JSON object.")
    email, password = data.get("email"), data.get("password")
    if not isinstance(email, str) or not isinstance(password, str) or not email or not password:
        raise WorkspaceError("Credentials must contain email and password.")
    if len(email) > 254 or len(password) > 256:
        raise WorkspaceError("The credentials file contains an invalid field.")
    return {"email": email, "password": password}


class CacheWorkspace:
    def __init__(self, credentials_file, base_url=BASE):
        self.credentials_file = credentials_file
        self.base = base_url.rstrip("/")
        url = urllib.parse.urlsplit(self.base)
        self.origin = f"{url.scheme}://{url.netloc}"
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPCookieProcessor(self.cookies))
        self.signed_in = False

    def request(self, method, path, data=None, content_type=None, authenticate=True, limit=MAX_BYTES):
        if authenticate and not self.signed_in:
            self.login()
        try:
            return self.send(method, path, data, content_type, limit)
        except WorkspaceError as exc:
            if not authenticate or exc.args[0] != "HTTP 401":
                raise
            self.signed_in = False
            self.cookies.clear()
            self.login()
            return self.send(method, path, data, content_type, limit)

    def send(self, method, path, data, content_type, limit):
        headers = {"Origin": self.origin, "Accept": "application/json",
                   "User-Agent": "CachePlus-Workspace-MCP/0.1.0"}
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=20) as response:
                output = response.read(limit + 1)
                if len(output) > limit:
                    raise WorkspaceError("The response exceeded the connector size limit.")
                return response.headers, output
        except urllib.error.HTTPError as exc:
            is_api_error = exc.headers.get_content_type() == "application/json"
            exc.close()
            messages = {
                401: "HTTP 401", 403: "This workspace is locked or its access period has ended.",
                404: "The file was not found or has expired.", 409: "The workspace limit was reached.",
                413: "This file exceeds the server upload limit.", 429: "Too many requests. Try again later.",
            }
            raise WorkspaceError(messages.get(exc.code) if is_api_error and exc.code in messages else f"Cache++ returned HTTP {exc.code}.") from None
        except (OSError, urllib.error.URLError):
            raise WorkspaceError("Cache++ could not be reached.") from None

    def login(self):
        try:
            credentials = read_credentials(self.credentials_file)
        except (OSError, ValueError):
            raise WorkspaceError("The private credentials file could not be read.") from None
        try:
            self.request("POST", "/v1/login", json.dumps(credentials).encode(), "application/json", authenticate=False)
        except WorkspaceError as exc:
            if exc.args[0] == "HTTP 401":
                raise WorkspaceError("Cache++ sign-in failed. Check your invited account.") from None
            raise
        self.signed_in = True

    def json_request(self, method, path, data=None, content_type=None, limit=MAX_BYTES):
        _, response = self.request(method, path, data, content_type, limit=limit)
        try:
            return json.loads(response)
        except (ValueError, UnicodeError):
            raise WorkspaceError("Cache++ returned an invalid response.") from None

    def call(self, name, args):
        if name == "cacheplus_workspace_status":
            value = self.json_request("GET", "/v1/workspace")
            return {"access": value.get("access", [])}
        if name in {"cacheplus_list_files", "cacheplus_upload_file"}:
            plan = args["plan"]
            if not isinstance(plan, str) or plan not in TIERS:
                raise WorkspaceError("Choose Space or Universe.")
            query = {"plan": plan}
            if name == "cacheplus_list_files":
                offset, limit = args.get("offset", 0), args.get("limit", 50)
                if type(offset) is not int or not 0 <= offset <= 10000 or type(limit) is not int or not 1 <= limit <= 100:
                    raise WorkspaceError("Use an offset from 0 to 10,000 and a page size from 1 to 100.")
                rows = self.json_request("GET", "/v1/objects?" + urllib.parse.urlencode(query), limit=MAX_METADATA)
                if not isinstance(rows, list):
                    raise WorkspaceError("Cache++ returned an invalid file list.")
                end = offset + limit
                return {"files": rows[offset:end], "next_offset": end if end < len(rows) else None}
            filename = args["filename"]
            if (not isinstance(filename, str) or not filename.strip() or len(filename) > 100
                    or any(ch in filename for ch in "/\\") or any(ord(ch) < 32 for ch in filename)):
                raise WorkspaceError("Supply a filename without directory separators.")
            encoded = args["data_base64"]
            if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_BYTES + 2) // 3):
                raise WorkspaceError("The encoded file exceeds the 256 KiB upload limit.")
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                raise WorkspaceError("File data must be valid base64.") from None
            if not 0 < len(data) <= MAX_BYTES:
                raise WorkspaceError("Upload between 1 byte and 256 KiB.")
            ttl = args.get("ttl_seconds")
            if plan == "Universe":
                if type(ttl) is not int or not 1 <= ttl <= 259200:
                    raise WorkspaceError("Universe requires a file lifetime of 1 to 259,200 seconds.")
                query["ttl"] = ttl
            elif "ttl_seconds" in args:
                raise WorkspaceError("Space has no per-file expiry.")
            query["name"] = filename
            result = self.json_request("PUT", "/v1/objects?" + urllib.parse.urlencode(query), data, "application/octet-stream")
            if result.get("sha256") != hashlib.sha256(data).hexdigest():
                raise WorkspaceError("Upload returned an unexpected checksum. List files before retrying.")
            return result
        file_id = args["file_id"]
        if not isinstance(file_id, str) or not OBJECT_ID.fullmatch(file_id):
            raise WorkspaceError("Use the file ID returned by Cache++.")
        headers, blob = self.request("GET", "/v1/objects/" + file_id)
        checksum = hashlib.sha256(blob).hexdigest()
        if headers.get("X-Content-SHA256") != checksum:
            raise WorkspaceError("The download checksum did not match.")
        return {"file_id": file_id, "bytes": len(blob), "sha256": checksum,
                "data_base64": base64.b64encode(blob).decode("ascii")}


PLAN = {"type": "string", "enum": ["Space", "Universe"]}
FILE = {"type": "string", "pattern": OBJECT_ID.pattern}
TOOL_DEFS = [
    ("cacheplus_workspace_status", "Show Space and Universe access and expiry.", {}, []),
    ("cacheplus_list_files", "List accessible files in one Cache++ workspace.", {"plan": PLAN,
        "offset": {"type": "integer", "minimum": 0, "maximum": 10000},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, ["plan"]),
    ("cacheplus_upload_file", "Upload a base64-encoded file up to 256 KiB. Universe requires ttl_seconds.", {
        "plan": PLAN, "filename": {"type": "string", "minLength": 1, "maxLength": 100},
        "data_base64": {"type": "string", "minLength": 4, "maxLength": 349528},
        "ttl_seconds": {"type": "integer", "minimum": 1, "maximum": 259200},
    }, ["plan", "filename", "data_base64"]),
    ("cacheplus_download_file", "Download one accessible file as base64 and verify its checksum.", {"file_id": FILE}, ["file_id"]),
]
TOOLS = {name: {"name": name, "description": description, "inputSchema": {
    "type": "object", "properties": props, "required": required, "additionalProperties": False,
}, "annotations": {"readOnlyHint": name != "cacheplus_upload_file", "openWorldHint": True}}
    for name, description, props, required in TOOL_DEFS}


def serve(workspace, incoming=sys.stdin.buffer, outgoing=sys.stdout):
    version = None

    def send(message):
        outgoing.write(json.dumps({"jsonrpc": "2.0", **message}, separators=(",", ":")) + "\n")
        outgoing.flush()

    def error(request_id, code, message):
        send({"id": request_id, "error": {"code": code, "message": message}})

    while line := incoming.readline(MAX_LINE + 1):
        if len(line) > MAX_LINE:
            while line and not line.endswith(b"\n"):
                line = incoming.readline(MAX_LINE + 1)
            error(None, -32700, "Request exceeds the connector size limit.")
            continue
        try:
            msg = json.loads(line)
        except (ValueError, UnicodeError):
            error(None, -32700, "Invalid JSON.")
            continue
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            error(None, -32600, "Invalid request.")
            continue
        if "id" not in msg:
            continue
        request_id = msg["id"]
        method, params = msg["method"], msg.get("params", {})
        if not isinstance(params, dict):
            error(request_id, -32602, "Expected an object for params.")
            continue
        if method == "initialize":
            client = params.get("protocolVersion")
            version = client if isinstance(client, str) and client in SUPPORTED else PROTOCOL
            result = {"protocolVersion": version, "capabilities": {"tools": {}},
                      "serverInfo": {"name": "cacheplus-workspace", "version": "0.1.0"}}
        elif method == "ping":
            result = {}
        elif not version:
            error(request_id, -32000, "Initialize first.")
            continue
        elif method == "tools/list":
            result = {"tools": list(TOOLS.values())}
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments", {})
            spec = TOOLS.get(name) if isinstance(name, str) else None
            if not spec or not isinstance(args, dict):
                error(request_id, -32602, "Unknown tool or invalid arguments.")
                continue
            schema = spec["inputSchema"]
            if args.keys() - schema["properties"].keys() or not set(schema["required"]) <= args.keys():
                error(request_id, -32602, "Tool arguments do not match the schema.")
                continue
            try:
                output = workspace.call(name, args)
                result = {"content": [{"type": "text", "text": json.dumps(output, separators=(",", ":"))}]}
                if version == PROTOCOL:
                    result["structuredContent"] = output
            except (WorkspaceError, AttributeError, TypeError) as exc:
                text = str(exc) if isinstance(exc, WorkspaceError) else "Cache++ returned an invalid response."
                result = {"content": [{"type": "text", "text": text}], "isError": True}
        else:
            error(request_id, -32601, "Method not found.")
            continue
        send({"id": request_id, "result": result})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials-file", required=True)
    options = parser.parse_args()
    serve(CacheWorkspace(options.credentials_file))
