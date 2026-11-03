"""Vendored SchoolSoft client and parsers.

Copied from https://github.com/kanylbullen/schoolsoft-mcp (MIT licence, see
LICENSE in this directory). The MCP server itself is not included; only the
HTTP client, the pydantic models and the page/REST parsers. Local change:
``client.SchoolSoftClient`` accepts an existing ``httpx.AsyncClient`` so Home
Assistant can supply one created off the event loop.
"""
