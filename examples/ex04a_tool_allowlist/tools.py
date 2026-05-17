"""Tool catalog for Example 4a — operator-defined at build time.

Centralises the allowlist constants and the ToolSpec list so that
handlers.py and run_demo.py can import them without touching graph.py
(which would create a circular import).
"""

from __future__ import annotations

from opg.core.model_client import ToolSpec

# ---------------------------------------------------------------------------
# Allowlist constants
# ---------------------------------------------------------------------------

ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "list_directory",
        "write_file",
        "send_email",
        "delete_file",
    }
)

HIGH_BLAST_RADIUS_TOOLS: frozenset[str] = frozenset(
    {
        "write_file",
        "send_email",
        "delete_file",
    }
)

# ---------------------------------------------------------------------------
# ToolSpec list — what the model is told it may call
# ---------------------------------------------------------------------------

EX04A_TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="read_file",
        description="Read the contents of a file at the given path.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file."},
            },
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="list_directory",
        description="List the files and subdirectories in the given directory.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the directory."},
            },
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="write_file",
        description="Write content to a file, creating or overwriting it.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file."},
                "content": {"type": "string", "description": "Text content to write."},
            },
            "required": ["path", "content"],
        },
    ),
    ToolSpec(
        name="send_email",
        description="Send an email to one or more recipients.",
        parameters={
            "type": "object",
            "properties": {
                "to": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Recipient email addresses.",
                },
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    ),
    ToolSpec(
        name="delete_file",
        description="Permanently delete a file at the given path.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file to delete."},
            },
            "required": ["path"],
        },
    ),
]
