"""Small local MCP stdio server for CachePlus (JSON-RPC 2.0)."""

import json
import re
import subprocess
import sys

import bank
import cachectl

TOOLS = [
    {"name": "cache_status", "description": "Read physical free space and disposable CachePlus job state.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "cache_backends", "description": "List configured cache storage profiles and mount state.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "cache_assess", "description": "Conservatively assess one path; never removes it.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "cache_run", "description": "Run a command only when its outputs are explicitly rebuildable. This is not a sandbox: the command may still write outside the cache if the tool ignores cache environment paths. Require a reviewed command and label; never use for source, Git, chats, credentials or unique data.",
     "inputSchema": {"type": "object", "properties": {
         "label": {"type": "string"}, "command": {"type": "array", "items": {"type": "string"}},
         "disposable": {"type": "boolean", "const": True}},
         "required": ["label", "command", "disposable"]}},
]


def dispatch(message):
    method = message.get("method")
    params = message.get("params") or {}
    if method == "initialize":
        requested = params.get("protocolVersion", "2025-11-25")
        version = requested if requested in ("2025-11-25", "2025-06-18") else "2025-11-25"
        return {"protocolVersion": version, "capabilities": {"tools": {}},
                "serverInfo": {"name": "cacheplus", "version": "0.3.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "cache_status":
            value = bank.status()
        elif name == "cache_backends":
            config = cachectl.load_config()
            value = {"active": config.get("active_profile", "default"),
                     "backends": [{"name": key, "kind": profile.get("kind", "external"),
                                   "path": profile["volume"],
                                   "mounted": cachectl.os.path.ismount(profile["volume"])}
                                  for key, profile in cachectl.profiles(config).items()]}
        elif name == "cache_assess":
            if not isinstance(args.get("path"), str) or not args["path"]:
                raise ValueError("path must be a non-empty string")
            value = cachectl.assess(args["path"])
        elif name == "cache_run":
            label, command = args.get("label"), args.get("command")
            if (args.get("disposable") is not True or not isinstance(label, str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", label)
                    or not isinstance(command, list) or not command
                    or len(command) > 128 or not all(isinstance(item, str) and item and "\x00" not in item
                                                for item in command)):
                raise ValueError("Explicit disposable=true, safe label and non-empty string command array are required")
            finished = subprocess.run([sys.executable, str(cachectl.PACKAGED_BANK),
                                       "run", label, "--", *command],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            value = {"exit_code": finished.returncode, "label": label,
                     "slot": str(bank.JOBS / ("slot-" + label)) if bank.JOBS else None,
                     "note": "Inspect command outputs; CachePlus is not a sandbox. Use CLI for live output."}
        else:
            raise ValueError("Unknown tool")
        return {"content": [{"type": "text", "text": json.dumps(value)}],
                "structuredContent": value}
    raise ValueError("Method not found")


def main():
    for line in sys.stdin:
        message = None
        try:
            message = json.loads(line)
            if "id" not in message:
                continue  # MCP notifications have no response.
            result = dispatch(message)
            response = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        except Exception as exc:
            response = {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                        "error": {"code": -32602 if isinstance(exc, ValueError) else -32603,
                                  "message": str(exc)}}
        print(json.dumps(response, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
