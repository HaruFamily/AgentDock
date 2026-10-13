"""Size protection for the optional, separately installed Google Sheets MCP.

No Google credentials or GUI dependencies are loaded by importing this module.
The upstream server/SDK versions are pinned in the accompanying launcher setup.
"""
from __future__ import annotations

import json
import re

from mcp import types

MAX_RESULT_BYTES = 8 * 1024 * 1024  # Leave room below the client's 10 MiB buffer.
_RECTANGLE = re.compile(r"\$?([A-Za-z]+)\$?([1-9][0-9]*)(?::\$?([A-Za-z]+)\$?([1-9][0-9]*))?")


def _bounded_range(value: object) -> bool:
    if not isinstance(value, str):
        return False
    match = _RECTANGLE.fullmatch(value)
    if not match:
        return False
    first_col, first_row, last_col, last_row = match.groups()
    if last_col is None:
        return True

    def column(label: str) -> int:
        result = 0
        for char in label.upper():
            result = result * 26 + ord(char) - ord('A') + 1
        return result

    return int(first_row) <= int(last_row) and column(first_col) <= column(last_col)


def _error(message: str) -> types.ServerResult:
    return types.ServerResult(types.CallToolResult(
        content=[types.TextContent(type='text', text=message)], isError=True,
    ))


def install_guard(server) -> None:
    """Wrap the normalized tool response, before stdio serialization.

Uses the low-level SDK request handler registry; test against pinned MCP 1.30.0.
Never retries a tool or drops fields from a successful result.
"""
    handlers = server.request_handlers
    original = handlers[types.CallToolRequest]
    if getattr(original, '_sheets_size_guard', False):
        return

    async def guarded(request: types.CallToolRequest) -> types.ServerResult:
        args = request.params.arguments or {}
        if (request.params.name == 'get_sheet_data'
                and args.get('include_grid_data')
                and not _bounded_range(args.get('range'))):
            return _error(
                'Full grid metadata requires a bounded cell range, e.g. A1:K50. '
                'No Google API request was made. Read smaller rectangles separately. '
                'For values use include_grid_data=false; for formulas use get_sheet_formulas. '
                'Whole-sheet formatting can exceed the MCP client 10 MiB transport limit.'
            )

        result = await original(request)
        root = result.root
        # FastMCP emits indented, ASCII-escaped JSON in addition to structuredContent.
        # Compact only a provably identical JSON mirror; preserve both representations.
        if isinstance(root, types.CallToolResult) and root.structuredContent is not None:
            if len(root.content) == 1 and isinstance(root.content[0], types.TextContent):
                text = root.content[0]
                try:
                    mirror = json.loads(text.text)
                except (ValueError, TypeError):
                    mirror = None
                if mirror == root.structuredContent:
                    compact = json.dumps(mirror, ensure_ascii=False, separators=(',', ':'))
                    root = root.model_copy(update={'content': [text.model_copy(update={'text': compact})]})
                    result = types.ServerResult(root)

        size = len(result.model_dump_json(by_alias=True, exclude_none=True).encode('utf-8'))
        if size > MAX_RESULT_BYTES:
            if request.params.name in {'get_sheet_data', 'get_sheet_formulas', 'get_multiple_sheet_data'}:
                return _error(
                    f'Response is {size} bytes, exceeding the safe {MAX_RESULT_BYTES}-byte limit. '
                    'The read completed, but its data was not sent. Request fewer cells/sheets, '
                    'or omit grid metadata. No data was silently truncated; the connection remains usable.'
                )
            return _error(
                f'Tool executed, but its response ({size} bytes) exceeds the safe transport limit. '
                'Its effects may already have been applied. Do not automatically repeat a write; '
                'verify its outcome using a small read. The connection remains usable.'
            )
        return result

    guarded._sheets_size_guard = True
    handlers[types.CallToolRequest] = guarded
