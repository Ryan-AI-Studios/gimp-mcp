"""Host-side gimp-agent CLI package (install, doctor, probe, exit-code map).

Does not import the FastMCP server. May import ``gimp_mcp_security`` (stdlib host module).
"""

from __future__ import annotations

__version__ = "0.2.0"

__all__ = ["__version__"]
