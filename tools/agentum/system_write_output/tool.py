"""
SystemWriteOutput tool implementation.

A system-level tool that writes task output to output.yaml.
This tool cannot be disabled via permissions - it is always available
to ensure the agent can report results even in restricted environments.
"""
import logging
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool
from pydantic import ValidationError
from src.core.schemas import OutputSchema

logger = logging.getLogger(__name__)


# System tools that cannot be disabled via permissions.
# These tools are critical for agent operation and output reporting.
# Add new system tools to this list as needed.
SYSTEM_TOOLS: list[str] = [
    "mcp__agentum__WriteOutput",
]


def create_write_output_tool(workspace_path: Path, session_id: str = ""):
    """
    Create the write_output tool function with workspace binding.

    The tool is created as a closure with the workspace path bound at creation time.
    This prevents the agent from manipulating the output file path.

    Args:
        workspace_path: Absolute path to the workspace directory.
        session_id: Session ID to include in output (optional).

    Returns:
        Tool function decorated with @tool.
    """
    # Bind paths at creation time - agent cannot override
    output_file = workspace_path / "output.yaml"
    bound_session_id = session_id

    @tool(
        "WriteOutput",
        """Write task results to the session output file.

This is a SYSTEM TOOL that writes to the predefined output.yaml location.
You CANNOT specify a different file - this is a security feature.

Use this tool to report:
- Task completion status (COMPLETE, PARTIAL, or FAILED)
- Error messages if the task failed or partially completed
- Comments explaining why the task wasn't fully completed
- The actual output/results of the task
- List of files that were generated (relative paths)

IMPORTANT: Call this tool ONCE at the end of your task to report results.
The file path is predetermined - you only provide the content.

Parameters:
- status: Must be "COMPLETE", "PARTIAL", or "FAILED"
- error: Error description if status is PARTIAL/FAILED, empty string otherwise
- comments: Optional explanation for PARTIAL/FAILED status
- output: The task result data (text, analysis, answer, etc.)
- result_files: List of files you created (use "./" prefix)

Example:
{
    "status": "COMPLETE",
    "error": "",
    "comments": "",
    "output": "Analysis complete. Found 5 issues in the codebase.",
    "result_files": ["./report.md", "./issues.json"]
}""",
        {
            "status": str,
            "error": str,
            "comments": str,
            "output": str,
            "result_files": list,
        }
    )
    async def write_output(args: dict[str, Any]) -> dict[str, Any]:
        """
        Write task output to output.yaml.

        This tool CANNOT write to arbitrary files - it only writes to
        the predefined output.yaml in the session workspace.
        """
        try:
            # Validate input using OutputSchema from schemas.py
            validated = OutputSchema(
                session_id=bound_session_id,
                status=args.get("status", "FAILED"),
                error=args.get("error", ""),
                comments=args.get("comments", ""),
                output=args.get("output", ""),
                result_files=args.get("result_files", []),
            )

            # Validate result_files paths (must start with ./)
            for path in validated.result_files:
                if not path.startswith("./"):
                    return {
                        "content": [{
                            "type": "text",
                            "text": f"Error: Invalid path in result_files: '{path}'. All paths must start with './'"
                        }],
                        "is_error": True
                    }

            # Write to output.yaml using OutputSchema's to_yaml method
            yaml_content = validated.to_yaml()

            # Ensure parent directory exists
            output_file.parent.mkdir(parents=True, exist_ok=True)

            # Write atomically
            temp_file = output_file.with_suffix(".yaml.tmp")
            temp_file.write_text(yaml_content, encoding="utf-8")
            temp_file.rename(output_file)

            logger.info(
                f"SystemWriteOutput: Wrote output.yaml "
                f"(status={validated.status}, files={len(validated.result_files)})"
            )

            # Return MCP-compatible content format
            return {
                "content": [{
                    "type": "text",
                    "text": f"Output written to output.yaml (status: {validated.status})"
                }]
            }

        except ValidationError as e:
            error_msg = f"Validation error: {e}"
            logger.error(f"SystemWriteOutput: {error_msg}")
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error: {error_msg}"
                }],
                "is_error": True
            }

        except Exception as e:
            error_msg = f"Failed to write output: {e}"
            logger.error(f"SystemWriteOutput: {error_msg}")
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error: {error_msg}"
                }],
                "is_error": True
            }

    return write_output


def create_agentum_mcp_server(
    workspace_path: Path,
    session_id: str = "",
    server_name: str = "agentum",
    version: str = "1.0.0"
):
    """
    Create an in-process MCP server for the SystemWriteOutput tool.

    Args:
        workspace_path: Absolute path to session workspace.
        session_id: Session ID to embed in output.
        server_name: MCP server name.
        version: Server version.

    Returns:
        McpSdkServerConfig for use in ClaudeAgentOptions.mcp_servers.
    """
    # Create the bound tool
    write_output_tool = create_write_output_tool(workspace_path, session_id)

    logger.info(
        f"Created SystemWriteOutput MCP server: "
        f"workspace={workspace_path}, session_id={session_id}"
    )

    return create_sdk_mcp_server(
        name=server_name,
        version=version,
        tools=[write_output_tool]
    )


def is_system_tool(tool_name: str) -> bool:
    """
    Check if a tool is a system tool that bypasses permissions.

    System tools are critical for agent operation and cannot be
    disabled by the permission system.

    Args:
        tool_name: Full tool name (may include mcp__ prefix).

    Returns:
        True if the tool is a system tool.
    """
    return tool_name in SYSTEM_TOOLS
