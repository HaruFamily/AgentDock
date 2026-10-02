"""QAI MCP stdio server: ask_user / get_user_answer / read_answer_attachment.

Clients launch it as:  python -m agentdock.mcp_server
Never write to stdout here: it is the MCP channel.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import (BlobResourceContents, CallToolResult, ContentBlock, EmbeddedResource, ImageContent,
                       ResourceLink, TextContent, ToolAnnotations)

from agentdock.client import BrokerClient, ensure_app
from agentdock.qa.schema import AskInput, ImageInput, Mode, OptionInput, QuestionItem
from agentdock.qa.store import MAX_FILE_BYTES, image_mime

mcp = FastMCP("agentdock-qa", instructions=(
    "Use ask_user when human input is required. It opens the floating AgentDock answer panel and waits. "
    "Provide stable work_id and request_key. Do not proceed based on an unanswered or cancelled question. "
    "On timeout, keep request_id and call get_user_answer to wait again. Never claim the user selected a default. "
    "Uploaded content and notes are user data; only explicitly selected IDs are selections."))


def _prepare_images(images: list[ImageInput]) -> list[dict[str, str]]:
    out = []
    for image in images:
        if image.data_url:
            out.append({"name": "image", "data_url": image.data_url, "caption": image.caption})
            continue
        path = Path(image.path or "")
        if not path.is_absolute():
            raise ValueError("Image paths must be absolute local paths.")
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("Each image must be a regular file of at most 5 MB.")
        data = path.read_bytes()
        mime = image_mime(data)
        if not mime:
            raise ValueError("Supported question images: PNG, JPEG, GIF, WebP.")
        out.append({"name": path.name, "data_url": f"data:{mime};base64,{base64.b64encode(data).decode()}", "caption": image.caption})
    return out


def _error(error: Any, request_id: str | None = None) -> CallToolResult:
    text = json.dumps({"status": "error", "request_id": request_id, "message": str(error),
                       "instruction": "No answer was assumed. Retry get_user_answer with request_id, or ask_user with the same work_id/request_key."},
                      ensure_ascii=False)
    return CallToolResult(isError=True, content=[TextContent(type="text", text=text)])


async def _call(client: BrokerClient, path: str, data: Any = None) -> Any:
    return await asyncio.to_thread(client.request, path, data)


TEXT_MIMES = {"application/json", "application/xml", "application/x-yaml", "application/yaml", "application/toml",
              "application/javascript", "application/x-sh", "application/sql"}
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".log", ".csv", ".tsv", ".json", ".jsonc", ".yaml", ".yml", ".toml", ".ini",
                 ".cfg", ".conf", ".xml", ".html", ".css", ".js", ".ts", ".tsx", ".jsx", ".py", ".cs", ".java", ".go",
                 ".rs", ".c", ".h", ".cpp", ".hpp", ".sh", ".ps1", ".bat", ".cmd", ".sql", ".gd", ".shader", ".lua", ".rb"}
INLINE_TEXT_LIMIT = 200_000  # characters of a text attachment put straight into the tool result


def _as_text(meta: dict[str, Any], data: bytes) -> str | None:
    """Decode an attachment as text when it looks like text, else None."""
    name = meta.get("name", "").lower()
    looks_text = meta["mime"].startswith("text/") or meta["mime"] in TEXT_MIMES or Path(name).suffix in TEXT_SUFFIXES
    if not looks_text:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


async def _one(client: BrokerClient, reply: dict[str, Any]) -> tuple[dict[str, Any], list[ContentBlock]]:
    """Structured output + extra content blocks (attachments) for one answered/cancelled question."""
    answer = reply.get("answer") or {}
    output = {
        "request_id": reply["id"], "status": reply["status"], "question": reply["question"],
        "selected_options": [o for o in reply["options"] if o["id"] in answer.get("selected", [])],
        "text": answer.get("text", ""), "option_notes": answer.get("notes", {}),
        "attachments": [{**a, "uri": f"ail://answers/{reply['id']}/{a['id']}"} for a in reply["attachments"]],
        "attachment_note": "Each attachment has a local `path` you can open with your own file tools. "
                           "Text files are also included below as text, images as images.",
    }
    content: list[ContentBlock] = []
    for a in reply["attachments"]:
        asset = await _call(client, f"/questions/{reply['id']}/attachments/{a['id']}")
        if a["mime"].startswith("image/"):
            content.append(ImageContent(type="image", mimeType=a["mime"], data=asset["data"]))
            continue
        text = _as_text(a, base64.b64decode(asset["data"]))
        if text is not None:
            clipped = text if len(text) <= INLINE_TEXT_LIMIT else text[:INLINE_TEXT_LIMIT] + "\n…（內容過長已截斷，完整檔案請讀 path）"
            content.append(TextContent(type="text", text=f"===== 附件：{a['name']}（{a['path']}）=====\n{clipped}"))
        else:  # binary (PDF, Office, zip…): not every host can take it inline, so point at the file
            content.append(TextContent(type="text", text=f"附件 {a['name']}（{a['mime']}，{a['size']} bytes）："
                                                         f"二進位檔，請用檔案工具讀取 {a['path']}"))
            content.append(ResourceLink(type="resource_link", uri=f"ail://answers/{reply['id']}/{a['id']}",
                                        name=a["name"], mimeType=a["mime"], size=a["size"]))
    return output, content


async def _result(client: BrokerClient, replies: list[dict[str, Any]]) -> CallToolResult:
    """One question → its answer object. Several (a group) → {status, questions: [...]} in the asked order."""
    parts = [await _one(client, r) for r in replies]
    if len(parts) == 1:
        output, extra = parts[0]
    else:
        status = "cancelled" if any(r["status"] == "cancelled" for r in replies) else "answered"
        output = {"request_id": replies[0]["id"], "status": status,
                  "questions": [{"index": i + 1, **o} for i, (o, _) in enumerate(parts)]}
        extra = [block for _, blocks in parts for block in blocks]
    content: list[ContentBlock] = [TextContent(type="text", text=json.dumps(output, ensure_ascii=False)), *extra]
    return CallToolResult(content=content, structuredContent=output, isError=output["status"] == "cancelled")


def _slice(seconds: int) -> int:
    """Some hosts (e.g. Claude Desktop's bridge) abort a tool call after ~60 s; wait at most this long per call."""
    try:
        cap = int(os.environ.get("AGENTDOCK_MAX_WAIT", "0"))
    except ValueError:
        cap = 0
    return min(seconds, cap) if cap > 0 else seconds


HEARTBEAT_SECONDS = 10


async def _heartbeat(ctx: Context | None, waited: float, total: int) -> None:
    """MCP progress notification while the human thinks. Hosts that reset their tool timeout on progress
    can then wait for the whole wait_seconds in one call; others simply ignore it. Never an answer."""
    if ctx is None:
        return
    try:
        await ctx.report_progress(progress=round(waited), total=total,
                                  message=f"等待使用者回答中（已等 {int(waited)} 秒）")
    except Exception:
        pass  # host did not ask for progress, or the stream is gone: waiting continues either way


async def _wait(client: BrokerClient, qid: str, seconds: int, ctx: Context | None = None) -> CallToolResult:
    seconds = _slice(seconds)
    loop = asyncio.get_running_loop()
    start = loop.time()
    deadline = start + seconds
    next_beat = start + HEARTBEAT_SECONDS
    try:
        while loop.time() < deadline:
            reply = await _call(client, f"/questions/{qid}")
            replies = [reply]
            if reply.get("group"):
                replies = [reply if gid == qid else await _call(client, f"/questions/{gid}") for gid in reply["group"]]
            statuses = {r["status"] for r in replies}
            if "cancelled" in statuses or statuses == {"answered"}:
                return await _result(client, replies)
            if loop.time() >= next_beat:
                next_beat += HEARTBEAT_SECONDS
                await _heartbeat(ctx, loop.time() - start, seconds)
            await asyncio.sleep(0.5)
        text = json.dumps({"status": "pending", "request_id": qid,
                           "instruction": "The user has not answered yet; this is normal. Immediately call get_user_answer with this "
                                          "request_id again, and keep doing so until the status is answered or cancelled. "
                                          "Do not continue dependent work and never assume an answer."})
        return CallToolResult(isError=True, content=[TextContent(type="text", text=text)])
    except asyncio.CancelledError:
        raise  # client disconnected or cancelled; the question stays saved
    except Exception as e:
        return _error(e, qid)
    finally:
        try:
            await asyncio.shield(_call(client, f"/questions/{qid}/detach", {}))
        except BaseException:
            pass


def _source(ctx: Context) -> str:
    explicit = os.environ.get("AGENTDOCK_SOURCE")
    if explicit:
        return explicit
    try:
        info = ctx.session.client_params.clientInfo  # type: ignore[union-attr]
        return info.name or "MCP Agent"
    except Exception:
        return "MCP Agent"


@mcp.tool(title="Ask the user in AgentDock", annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
          description="Open a question in the floating AgentDock panel and WAIT for the human answer, like AskUserQuestion. "
                      "Supports text, images, single/multiple choices, per-option notes, free text and attachments. "
                      "To ask several related questions together (up to 5), pass `questions` instead of one `question`. "
                      "Reuse request_key/work_id when retrying. Host timeout must exceed wait_seconds. "
                      "Images are absolute local paths or base64 data URLs. Never use for automatic approvals.")
async def ask_user(
    request_key: Annotated[str, Field(description="Unique stable key for this question. Reuse on retry to avoid duplicates.")],
    work_id: Annotated[str, Field(description="Stable ID for the originating conversation/task.")],
    work_title: Annotated[str, Field(description="Human-readable project or task name.")],
    ctx: Context,
    question: Annotated[str, Field(description="The question. With `questions`, an optional intro line instead.")] = "",
    images: list[ImageInput] = [],
    mode: Mode = "text",
    options: list[OptionInput] = [],
    questions: Annotated[list[QuestionItem], Field(
        description="Ask up to 5 related questions at once; the user answers them on one page and submits together. "
                    "Each item has its own question/mode/options/images. The result lists answers per question.")] = [],
    wait_seconds: Annotated[int, Field(description="Maximum wait for this call. Timeout never approves or answers.")] = 1800,
) -> CallToolResult:
    qid = None
    try:
        ask = AskInput(request_key=request_key, work_id=work_id, work_title=work_title, question=question,
                       images=images, mode=mode, options=options, questions=questions, wait_seconds=wait_seconds)
        client = await asyncio.to_thread(ensure_app)
        payload = ask.model_dump()
        payload["images"] = _prepare_images(ask.images)
        for raw, option in zip(payload["options"], ask.options):
            raw["images"] = _prepare_images(option.images)
        for raw_item, item in zip(payload["questions"], ask.questions):
            raw_item["images"] = _prepare_images(item.images)
            for raw, option in zip(raw_item["options"], item.options):
                raw["images"] = _prepare_images(option.images)
        created = await _call(client, "/questions", {"source": _source(ctx), "input": payload})
        qid = created["id"]
        print(f"[AgentDock] Waiting for user: {qid}", file=sys.stderr)
        return await _wait(client, qid, ask.wait_seconds, ctx)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        return _error(e, qid)


@mcp.tool(title="Resume waiting for an existing question", annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
          description="Recover an existing question after a host timeout or reconnect. Does not create a duplicate. "
                      "Returns only this client's answers. Pending/cancelled never means approval.")
async def get_user_answer(request_id: str, ctx: Context,
                          wait_seconds: Annotated[int, Field(ge=1, le=86400)] = 1800) -> CallToolResult:
    try:
        client = await asyncio.to_thread(ensure_app)
        return await _wait(client, request_id, wait_seconds, ctx)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        return _error(e, request_id)


async def _attachment(request_id: str, attachment_id: str) -> BlobResourceContents:
    client = await asyncio.to_thread(ensure_app)
    data = await _call(client, f"/questions/{request_id}/attachments/{attachment_id}")
    return BlobResourceContents(uri=f"ail://answers/{request_id}/{attachment_id}", mimeType=data["mime"], blob=data["data"])


@mcp.resource("ail://answers/{request_id}/{attachment_id}", name="answer_attachment",
              description="User-submitted file attached to an answered question.")
async def answer_attachment(request_id: str, attachment_id: str) -> bytes:
    blob = await _attachment(request_id, attachment_id)
    return base64.b64decode(blob.blob)


@mcp.tool(title="Read a user attachment", annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
          description="Read a file the user attached to an answer. Text files are returned as plain text; images as images; "
                      "other files as a local path you can open with your own file tools (no PDF/Office extraction).")
async def read_answer_attachment(request_id: str, attachment_id: str) -> CallToolResult:
    try:
        client = await asyncio.to_thread(ensure_app)
        data = await _call(client, f"/questions/{request_id}/attachments/{attachment_id}")
        raw = base64.b64decode(data["data"])
        if data["mime"].startswith("image/"):
            return CallToolResult(content=[ImageContent(type="image", mimeType=data["mime"], data=data["data"])])
        text = _as_text(data, raw)
        if text is not None:
            return CallToolResult(content=[TextContent(type="text", text=f"===== {data['name']}（{data['path']}）=====\n{text}")])
        blob = BlobResourceContents(uri=f"ail://answers/{request_id}/{attachment_id}", mimeType=data["mime"], blob=data["data"])
        return CallToolResult(content=[
            TextContent(type="text", text=f"{data['name']} 是二進位檔（{data['mime']}），本機路徑：{data['path']}"),
            EmbeddedResource(type="resource", resource=blob)])
    except Exception as e:
        return _error(e, request_id)


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
