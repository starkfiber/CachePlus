"""Small read-only MCP stdio server for CachePlus (JSON-RPC 2.0)."""

import json
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
