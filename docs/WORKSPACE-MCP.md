# Cache++ Workspace MCP

Use this optional Python 3.9+ stdio connector for the hosted Cache++ Space and Universe workspace. An invited account and active workspace access are required for file operations.

The invited email and password belong in a private owner-only JSON file with string fields `email` and `password`. Keep that file outside the repository. Register the connector separately from the local CachePlus jobs plugin:

```sh
codex mcp add cacheplus-workspace -- /usr/bin/python3 -B \
  /absolute/path/to/CachePlus/plugins/cache/scripts/cacheplus_workspace_mcp.py \
  --credentials-file /absolute/path/to/private-signin.json
```

| Tool | Action |
| --- | --- |
| cacheplus_workspace_status | Check Space and Universe access. |
| cacheplus_list_files | List files by tier. Pass an offset and a limit for additional pages. |
| cacheplus_upload_file | Upload a filename and base64-encoded file. Supply a lifetime in seconds for Universe. |
| cacheplus_download_file | Download verified bytes as base64 using a file ID. |

The current file limit is 256 KiB. Universe accepts lifetimes from 1 second through 72 hours. Downloaded content is returned to the MCP client. Use only material that you intend that client to receive.
