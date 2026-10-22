"""Send tasks to Agent CLIs and follow them in the Inbox.

Everything here runs inside the App process. The broker gains no route that can start work,
and permissions, questions and stops are only sent after a click in the UI.
No PySide6 imports: the store that the MCP side also loads imports this package.
"""
