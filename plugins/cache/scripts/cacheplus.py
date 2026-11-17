"""CachePlus command line interface."""

import sys

import cachectl


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "api":
        import cacheplus_api
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        return cacheplus_api.main()
    if len(sys.argv) > 1 and sys.argv[1] == "mcp":
        import cacheplus_mcp
        return cacheplus_mcp.main()
    return cachectl.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
