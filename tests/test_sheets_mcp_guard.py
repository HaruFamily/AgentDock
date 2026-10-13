import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp import FastMCP

from agentdock.sheets_mcp_guard import MAX_RESULT_BYTES, install_guard


def test_grid_boundaries_and_lossless_compaction():
    async def run():
        server = FastMCP('fake sheets')
        calls = []

        @server.tool()
        def get_sheet_data(range: str = '', include_grid_data: bool = False) -> dict[str, Any]:
            calls.append(range)
            return {'values': [['中文', '="quoted"', '\n']], 'format': {'bold': True}}

        install_guard(server._mcp_server)
        install_guard(server._mcp_server)  # Idempotent.
        call = server._mcp_server.request_handlers[types.CallToolRequest]
        for value in ['', 'A:Z', 'A1:Z', '1:100', 'A0:B2', 'B2:A1', 'Tab!A1:B2']:
            reply = await call(types.CallToolRequest(method='tools/call', params=types.CallToolRequestParams(
                name='get_sheet_data', arguments={'range': value, 'include_grid_data': True})))
            assert reply.root.isError
        assert calls == []  # Reject before upstream/Google execution.
        for value in ['A1', '$A$1:$K$50', 'a1:aa20']:
            reply = await call(types.CallToolRequest(method='tools/call', params=types.CallToolRequestParams(
                name='get_sheet_data', arguments={'range': value, 'include_grid_data': True})))
            assert not reply.root.isError
            assert json.loads(reply.root.content[0].text) == reply.root.structuredContent
            assert reply.root.structuredContent['format'] == {'bold': True}
            assert '\\u4e2d' not in reply.root.content[0].text
        reply = await call(types.CallToolRequest(method='tools/call', params=types.CallToolRequestParams(
            name='get_sheet_data', arguments={})))
        assert not reply.root.isError  # Values-only whole-sheet reads remain supported.

    asyncio.run(run())


def test_large_response_keeps_real_stdio_session_alive(tmp_path):
    fixture = tmp_path / 'fake_server.py'
    fixture.write_text('''
from mcp.server.fastmcp import FastMCP
from agentdock.sheets_mcp_guard import install_guard
from typing import Any
server = FastMCP('isolated fixture')
count = 0
@server.tool()
def get_sheet_data(range: str = '', include_grid_data: bool = False, large: bool = False) -> dict[str, Any]:
    return {'data': '中' * 4_000_000 if large else 'ok'}
@server.tool()
def update_cells() -> str:
    global count
    count += 1
    return 'x' * 11_000_000
@server.tool()
def write_count() -> int:
    return count
install_guard(server._mcp_server)
import sys
assert 'PySide6' not in sys.modules
server.run()
''', encoding='utf-8')

    async def run():
        env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
        params = StdioServerParameters(command=sys.executable, args=[str(fixture)], env=env, cwd=str(tmp_path))
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                assert len((await session.list_tools()).tools) == 3
                blocked = await session.call_tool('get_sheet_data', {'include_grid_data': True})
                assert blocked.isError
                large = await session.call_tool('get_sheet_data', {'large': True, 'range': 'A1:B2'})
                assert large.isError and 'not sent' in large.content[0].text
                assert len(large.model_dump_json().encode()) < MAX_RESULT_BYTES
                small = await session.call_tool('get_sheet_data', {'range': 'A1', 'include_grid_data': True})
                assert not small.isError and small.structuredContent == {'data': 'ok'}
                write = await session.call_tool('update_cells')
                assert write.isError and 'Do not automatically repeat a write' in write.content[0].text
                count = await session.call_tool('write_count')
                assert count.structuredContent == {'result': 1}

    asyncio.run(run())
