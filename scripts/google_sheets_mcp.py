"""Launch the pinned upstream Sheets MCP with AgentDock's response size guard."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from agentdock.sheets_mcp_guard import install_guard
    from mcp_google_sheets import server

    install_guard(server.mcp._mcp_server)
    server.main()


if __name__ == '__main__':
    main()
