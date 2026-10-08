"""Public authoring guidance; safe to import from the headless MCP server."""

QUESTION_FORMAT = (
    "Free-form text with optional Markdown: blank lines for paragraphs, **bold** for key points, "
    "*italic*, ### headings, - bullet lists, numbered lists, > quotes, `inline code`, and fenced "
    "code blocks using triple backticks (optional language). "
    "Optional AgentDock extensions: put :::warning or :::note on its own line, then the content, "
    "then ::: on its own line. warning highlights an important impact; note uses smaller, muted text. "
    "These blocks are optional, may appear anywhere, and cannot be nested. "
    "Use actual newline characters, not literal backslash-n. Keep a clear question, short paragraphs "
    "and selective emphasis; do not bold the entire message. No required section structure. "
    "Colors and sizes follow the user's theme; raw HTML/CSS and inline images are not rendered. "
    "Use the images parameter for images. Option labels stay plain text; option descriptions support the same formatting."
)
