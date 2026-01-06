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

from .tracer import TracerBase


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
        self._model: Optional[str] = None  # Model used in this session
        self._permission_denied: bool = False  # Set when permission denial interrupts
        self._metrics_input_tokens = 0
        self._metrics_output_tokens = 0
        self._metrics_cache_creation_tokens = 0
        self._metrics_cache_read_tokens = 0
        self._metrics_turns = 0
        self._metrics_cost_usd: Optional[float] = None
        self._last_metrics_snapshot: Optional[tuple[int, int, int, int, int, Optional[float]]] = None
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
            self.tracer.on_tool_start(
                tool_name=block.name,
                tool_input=block.input,
                tool_id=block.id
            )
            self._metrics_turns += 1
            self._emit_metrics_update()

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
        _ = msg

    def _handle_stream_event(self, event: StreamEvent) -> None:
        """Handle low-level stream events."""
        raw_event = event.event
        if not isinstance(raw_event, dict):
            return

        event_type = raw_event.get("type")
        usage = None

        if event_type == "message_start":
            message = raw_event.get("message", {})
            if isinstance(message, dict):
                usage = message.get("usage")
        elif event_type == "message_delta":
            usage = raw_event.get("usage")
        elif event_type == "message_stop":
            usage = raw_event.get("usage")
        else:
            usage = raw_event.get("usage")

        if isinstance(usage, dict):
            self._apply_usage_update(usage)

    def _apply_usage_update(self, usage: dict[str, Any]) -> None:
        def update_max(current: int, value: Optional[int]) -> int:
            if value is None:
                return current
            try:
                numeric = int(value)
            except (TypeError, ValueError):
                return current
            return max(current, numeric)

        self._metrics_input_tokens = update_max(
            self._metrics_input_tokens, usage.get("input_tokens")
        )
        self._metrics_output_tokens = update_max(
            self._metrics_output_tokens, usage.get("output_tokens")
        )
        self._metrics_cache_creation_tokens = update_max(
            self._metrics_cache_creation_tokens, usage.get("cache_creation_input_tokens")
        )
        self._metrics_cache_read_tokens = update_max(
            self._metrics_cache_read_tokens, usage.get("cache_read_input_tokens")
        )

        cost_value = usage.get("total_cost_usd") or usage.get("cost_usd")
        if cost_value is not None:
            try:
                self._metrics_cost_usd = float(cost_value)
            except (TypeError, ValueError):
                pass

        if self._metrics_cost_usd is None:
            estimated = self._estimate_cost_usd()
            if estimated is not None:
                self._metrics_cost_usd = estimated

        self._emit_metrics_update()

    def _estimate_cost_usd(self) -> Optional[float]:
        if not self._model:
            return None

        pricing_map = {
            "claude-sonnet-4-20250514": (3.0, 15.0),
            "claude-opus-4-20250514": (15.0, 75.0),
            "claude-3-7-sonnet-20250219": (3.0, 15.0),
            "claude-3-5-sonnet-20241022": (3.0, 15.0),
        }

        rates = pricing_map.get(self._model)
        if not rates:
            return None

        input_rate, output_rate = rates
        total_input = (
            self._metrics_input_tokens
            + self._metrics_cache_creation_tokens
            + self._metrics_cache_read_tokens
        )
        return (total_input / 1_000_000) * input_rate + (
            self._metrics_output_tokens / 1_000_000
        ) * output_rate

    def _emit_metrics_update(self) -> None:
        snapshot = (
            self._metrics_input_tokens,
            self._metrics_output_tokens,
            self._metrics_cache_creation_tokens,
            self._metrics_cache_read_tokens,
            self._metrics_turns,
            self._metrics_cost_usd,
        )
        if snapshot == self._last_metrics_snapshot:
            return

        self._last_metrics_snapshot = snapshot
        payload: dict[str, Any] = {
            "tokens_in": self._metrics_input_tokens,
            "tokens_out": self._metrics_output_tokens,
            "cache_creation_input_tokens": self._metrics_cache_creation_tokens,
            "cache_read_input_tokens": self._metrics_cache_read_tokens,
            "turns": self._metrics_turns,
        }
        if self._metrics_cost_usd is not None:
            payload["total_cost_usd"] = self._metrics_cost_usd
        if self._model:
            payload["model"] = self._model

        self.tracer.on_metrics_update(payload)

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
