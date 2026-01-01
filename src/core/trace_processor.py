"""
Trace Processor for Claude Agent SDK.

Bridges the SDK's streaming messages and hooks to the ExecutionTracer.

Usage:
    from tracer import ExecutionTracer
    from trace_processor import TraceProcessor
    
    tracer = ExecutionTracer(verbose=True)
    processor = TraceProcessor(tracer)
    
    # Use in agent execution
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            processor.process_message(message)
"""
import json
import re
from dataclasses import asdict
from typing import Any, Optional, Union

from claude_agent_sdk import (
    AssistantMessage,
    HookContext,
    HookMatcher,
    PostToolUseHookInput,
    PreToolUseHookInput,
    ResultMessage,
    StopHookInput,
    SystemMessage,
    UserMessage,
)
from claude_agent_sdk.types import (
    ContentBlock,
    StreamEvent,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)

from tracer import ExecutionTracer, TracerBase


# Type alias for SDK messages
SDKMessage = Union[
    UserMessage,
    AssistantMessage,
    SystemMessage,
    ResultMessage,
    StreamEvent,
]


class TraceProcessor:
    """
    Processes Claude Agent SDK messages and dispatches to tracer.
    
    This class bridges the SDK's streaming message types to the
    TracerBase interface, handling message parsing and event dispatching.
    
    Args:
        tracer: The tracer instance to dispatch events to.
        include_user_messages: Whether to trace user messages.
    """
    
    def __init__(
        self,
        tracer: TracerBase,
        include_user_messages: bool = False
    ) -> None:
        self.tracer = tracer
        self.include_user_messages = include_user_messages
        self._pending_tool_calls: dict[str, dict[str, Any]] = {}
        self._initialized = False
        self._task: Optional[str] = None
        self._output_status: Optional[str] = None  # Status from output.yaml Write
        self._model: Optional[str] = None  # Model used in this session
        self._permission_denied: bool = False  # Set when permission denial interrupts
        # Cumulative stats (set externally when resuming a session)
        self._cumulative_cost_usd: Optional[float] = None
        self._cumulative_turns: Optional[int] = None
        self._cumulative_tokens: Optional[int] = None
    
    def set_task(self, task: str) -> None:
        """
        Set the task text to be displayed when agent starts.
        
        Args:
            task: The task description text.
        """
        self._task = task
    
    def set_model(self, model: str) -> None:
        """
        Set the model name for context size calculations.
        
        Args:
            model: The model identifier.
        """
        self._model = model

    def set_permission_denied(self, denied: bool = True) -> None:
        """
        Mark that the agent was interrupted due to permission denial.
        
        This affects the status displayed in the completion box.
        
        Args:
            denied: Whether permission was denied.
        """
        self._permission_denied = denied
    
    def set_cumulative_stats(
        self,
        cost_usd: Optional[float] = None,
        turns: Optional[int] = None,
        tokens: Optional[int] = None
    ) -> None:
        """
        Set cumulative statistics from previous runs (for resumed sessions).
        
        These values will be added to the current run's stats to show
        the total across all runs.
        
        Args:
            cost_usd: Previous cumulative cost in USD.
            turns: Previous cumulative turn count.
            tokens: Previous cumulative token count.
        """
        self._cumulative_cost_usd = cost_usd
        self._cumulative_turns = turns
        self._cumulative_tokens = tokens
    
    def process_message(self, message: SDKMessage) -> None:
        """
        Process a single SDK message and dispatch to tracer.
        
        Args:
            message: The SDK message to process.
        """
        if isinstance(message, SystemMessage):
            self._handle_system_message(message)
        elif isinstance(message, AssistantMessage):
            self._handle_assistant_message(message)
        elif isinstance(message, UserMessage):
            self._handle_user_message(message)
        elif isinstance(message, ResultMessage):
            self._handle_result_message(message)
        elif isinstance(message, StreamEvent):
            self._handle_stream_event(message)
        else:
            # Unknown message type - try to handle generically
            self._handle_unknown_message(message)
    
    def _handle_system_message(self, msg: SystemMessage) -> None:
        """Handle system lifecycle messages."""
        subtype = msg.subtype
        data = msg.data
        
        if subtype == "init":
            self._initialized = True
            # Extract skills from init data if available
            skills = data.get("skills", [])
            if not skills:
                # Skills might be empty list or not present
                skills = None
            
            # Extract task if available
            task = data.get("task")
            
            self.tracer.on_agent_start(
                session_id=data.get("session_id", "unknown"),
                model=data.get("model", "unknown"),
                tools=data.get("tools", []),
                working_dir=data.get("cwd", "."),
                skills=skills,
                task=task or self._task
            )
        elif subtype in ("error", "api_error", "server_error"):
            error_msg = data.get("message", data.get("error", str(data)))
            self.tracer.on_error(str(error_msg), error_type=subtype)
        else:
            # Other system events (status changes, etc.)
            self.tracer.on_system_event(subtype, data) \
                if hasattr(self.tracer, 'on_system_event') else None
    
    def _handle_assistant_message(self, msg: AssistantMessage) -> None:
        """Handle assistant responses with content blocks."""
        if msg.error:
            self.tracer.on_error(
                f"Assistant error: {msg.error}",
                error_type="assistant_error"
            )
            return
        
        for block in msg.content:
            self._process_content_block(block)
    
    def _process_content_block(self, block: ContentBlock) -> None:
        """Process a single content block."""
        if isinstance(block, TextBlock):
            self.tracer.on_message(block.text)
        
        elif isinstance(block, ThinkingBlock):
            self.tracer.on_thinking(block.thinking)
        
        elif isinstance(block, ToolUseBlock):
            # Store pending tool call info
            self._pending_tool_calls[block.id] = {
                "name": block.name,
                "input": block.input,
            }
            # Track status from Write tool calls to output.yaml
            self._track_output_status(block.name, block.input)
            
            self.tracer.on_tool_start(
                tool_name=block.name,
                tool_input=block.input,
                tool_id=block.id
            )
        
        elif isinstance(block, ToolResultBlock):
            tool_id = block.tool_use_id
            tool_info = self._pending_tool_calls.pop(tool_id, {})
            tool_name = tool_info.get("name", "unknown")
            
            self.tracer.on_tool_complete(
                tool_name=tool_name,
                tool_id=tool_id,
                result=block.content,
                duration_ms=0,  # Will be calculated by tracer
                is_error=block.is_error or False
            )
        
        elif isinstance(block, dict):
            # Handle dict-style blocks (from JSON parsing)
            self._process_dict_block(block)
    
    def _process_dict_block(self, block: dict[str, Any]) -> None:
        """Process a dictionary-style content block."""
        if "text" in block:
            self.tracer.on_message(block["text"])
        elif "thinking" in block:
            self.tracer.on_thinking(block["thinking"])
        elif "name" in block and "input" in block:
            # Tool use
            tool_id = block.get("id", "unknown")
            self._pending_tool_calls[tool_id] = {
                "name": block["name"],
                "input": block["input"],
            }
            # Track status from Write tool calls to output.yaml
            self._track_output_status(block["name"], block["input"])
            
            self.tracer.on_tool_start(
                tool_name=block["name"],
                tool_input=block["input"],
                tool_id=tool_id
            )
        elif "tool_use_id" in block:
            # Tool result
            tool_id = block["tool_use_id"]
            tool_info = self._pending_tool_calls.pop(tool_id, {})
            self.tracer.on_tool_complete(
                tool_name=tool_info.get("name", "unknown"),
                tool_id=tool_id,
                result=block.get("content", ""),
                duration_ms=0,
                is_error=block.get("is_error", False)
            )
    
    def _handle_user_message(self, msg: UserMessage) -> None:
        """Handle user input messages."""
        if not self.include_user_messages:
            return
        
        content = msg.content
        if isinstance(content, str):
            self.tracer.on_message(f"[USER] {content}")
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, TextBlock):
                    self.tracer.on_message(f"[USER] {block.text}")
    
    def _handle_result_message(self, msg: ResultMessage) -> None:
        """Handle final result with metrics and usage."""
        status = self._determine_status(msg)
        
        # Get usage data from result message
        usage = msg.usage if hasattr(msg, 'usage') else None
        
        # Calculate cumulative totals if we have previous stats
        cumulative_cost = None
        cumulative_turns = None
        cumulative_tokens = None
        
        if self._cumulative_cost_usd is not None:
            cumulative_cost = self._cumulative_cost_usd + (msg.total_cost_usd or 0)
        
        if self._cumulative_turns is not None:
            cumulative_turns = self._cumulative_turns + msg.num_turns
        
        if self._cumulative_tokens is not None and usage:
            current_tokens = (
                usage.get("input_tokens", 0) +
                usage.get("cache_creation_input_tokens", 0) +
                usage.get("cache_read_input_tokens", 0) +
                usage.get("output_tokens", 0)
            )
            cumulative_tokens = self._cumulative_tokens + current_tokens
        
        self.tracer.on_agent_complete(
            status=status,
            num_turns=msg.num_turns,
            duration_ms=msg.duration_ms,
            total_cost_usd=msg.total_cost_usd,
            result=msg.result,
            session_id=getattr(msg, 'session_id', None),
            usage=usage,
            model=self._model,
            cumulative_cost_usd=cumulative_cost,
            cumulative_turns=cumulative_turns,
            cumulative_tokens=cumulative_tokens,
        )
    
    def _determine_status(self, msg: ResultMessage) -> str:
        """
        Determine the final status from the result message.
        
        Checks in order:
        1. Permission denial (agent interrupted by permission callback)
        2. SDK-level errors (msg.is_error)
        3. Status tracked from Write tool calls to output.yaml
        4. Status patterns in the result text
        
        Args:
            msg: The ResultMessage from the SDK.
        
        Returns:
            Status string: "COMPLETE", "PARTIAL", or "FAILED".
        """
        # If permission was denied, report failed
        if self._permission_denied:
            return "FAILED"
        
        # If there's an SDK-level error, report it
        if msg.is_error:
            return msg.subtype.upper() if msg.subtype else "FAILED"
        
        # Check if we tracked a status from output.yaml Write
        if self._output_status:
            status_upper = self._output_status.upper()
            if status_upper in ("FAILED", "PARTIAL"):
                return status_upper
        
        # Fallback: check if the result text contains a non-COMPLETE status
        if msg.result:
            status = self._extract_status_from_result(msg.result)
            if status:
                status_upper = status.upper()
                if status_upper in ("FAILED", "PARTIAL"):
                    return status_upper
        
        return "COMPLETE"
    
    def _extract_status_from_result(self, result: str) -> Optional[str]:
        """
        Extract status field from result if it contains JSON.
        
        Looks for patterns like status: FAILED in the result text,
        which may appear when the agent writes output.yaml.
        
        Args:
            result: The result text from the agent.
        
        Returns:
            The status value if found, None otherwise.
        """
        if not result:
            return None
        
        # Try to find JSON with status field in the result
        # Pattern: "status": "VALUE" (case insensitive)
        status_pattern = r'"status"\s*:\s*"([^"]+)"'
        match = re.search(status_pattern, result, re.IGNORECASE)
        if match:
            return match.group(1)
        
        # Try parsing as JSON if it looks like JSON
        result_stripped = result.strip()
        if result_stripped.startswith('{') and result_stripped.endswith('}'):
            try:
                data = json.loads(result_stripped)
                if isinstance(data, dict) and "status" in data:
                    return str(data["status"])
            except json.JSONDecodeError:
                pass
        
        return None
    
    def _track_output_status(self, tool_name: str, tool_input: Any) -> None:
        """
        Track status from Write tool calls to output.yaml.
        
        When the agent writes to output.yaml, extract the status
        from the content to determine final success/failure.
        
        Args:
            tool_name: Name of the tool being called.
            tool_input: Input parameters for the tool.
        """
        if tool_name != "Write":
            return
        
        if not isinstance(tool_input, dict):
            return
        
        # Check if writing to output.yaml
        file_path = tool_input.get("file_path", "")
        if not file_path.endswith("output.yaml"):
            return
        
        # Extract status from content
        content = tool_input.get("content", "")
        if not content:
            return
        
        # Try to parse the content as YAML
        try:
            import yaml
            data = yaml.safe_load(content)
            if isinstance(data, dict) and "status" in data:
                self._output_status = str(data["status"])
        except Exception:
            # Try regex pattern match for YAML (status: "VALUE" or status: VALUE)
            status_pattern = r'status:\s*["\']?([^"\'\n]+)["\']?'
            match = re.search(status_pattern, content, re.IGNORECASE)
            if match:
                self._output_status = match.group(1).strip()
    
    def _handle_stream_event(self, event: StreamEvent) -> None:
        """Handle low-level stream events."""
        # Stream events are typically progress updates
        event_data = event.event
        event_type = event_data.get("type", "unknown")
        
        # Could be extended to handle specific streaming events
        # For now, we ignore most stream events as they're low-level
        pass
    
    def _handle_unknown_message(self, message: Any) -> None:
        """Handle unknown message types."""
        if hasattr(message, '__dict__'):
            self.tracer.on_message(f"[UNKNOWN] {type(message).__name__}")


def create_trace_hooks(
    tracer: TracerBase,
    trace_permissions: bool = False
) -> dict[str, list[HookMatcher]]:
    """
    Create SDK hook configuration for tracing.
    
    This creates hook matchers that integrate with the SDK's
    native hook system for PreToolUse and PostToolUse events.
    
    Args:
        tracer: The tracer to dispatch events to.
        trace_permissions: Also trace permission decisions.
    
    Returns:
        Hook configuration dict for ClaudeAgentOptions.
    """
    
    async def pre_tool_hook(
        hook_input: PreToolUseHookInput,
        transcript_path: Optional[str],
        context: HookContext
    ) -> dict[str, Any]:
        """Hook called before tool execution."""
        tracer.on_tool_start(
            tool_name=hook_input["tool_name"],
            tool_input=hook_input["tool_input"],
            tool_id=hook_input.get("session_id", "hook")
        )
        return {}  # Allow execution to continue
    
    async def post_tool_hook(
        hook_input: PostToolUseHookInput,
        transcript_path: Optional[str],
        context: HookContext
    ) -> dict[str, Any]:
        """Hook called after tool execution."""
        tracer.on_tool_complete(
            tool_name=hook_input["tool_name"],
            tool_id=hook_input.get("session_id", "hook"),
            result=hook_input.get("tool_response", ""),
            duration_ms=0,
            is_error="error" in str(hook_input.get("tool_response", "")).lower()
        )
        return {}
    
    async def stop_hook(
        hook_input: StopHookInput,
        transcript_path: Optional[str],
        context: HookContext
    ) -> dict[str, Any]:
        """Hook called when agent stops."""
        # Signal tracer that agent is stopping
        if hasattr(tracer, 'on_system_event'):
            tracer.on_system_event("stop", {"active": hook_input["stop_hook_active"]})
        return {}
    
    hooks: dict[str, list[HookMatcher]] = {
        "PreToolUse": [
            HookMatcher(
                matcher=None,  # Match all tools
                hooks=[pre_tool_hook],
                timeout=30.0,
            )
        ],
        "PostToolUse": [
            HookMatcher(
                matcher=None,
                hooks=[post_tool_hook],
                timeout=30.0,
            )
        ],
        "Stop": [
            HookMatcher(
                matcher=None,
                hooks=[stop_hook],
                timeout=10.0,
            )
        ],
    }
    
    return hooks


def create_stderr_callback(tracer: TracerBase):
    """
    Create a stderr callback that traces CLI errors.
    
    Args:
        tracer: The tracer to dispatch events to.
    
    Returns:
        Callback for ClaudeAgentOptions.stderr.
    """
    def stderr_callback(text: str) -> None:
        if text.strip():
            tracer.on_error(text.strip(), error_type="stderr")
    
    return stderr_callback

