"""
Data models for Agentum.

Contains Pydantic models for agent configuration, results, and metrics.
"""
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    """Status of task execution."""
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    ERROR = "ERROR"


class OutputSchema(BaseModel):
    """
    Strict output schema for agent task results.

    All fields must be present in the output.yaml file.
    Fields without values should be empty strings.
    """
    session_id: str = Field(
        default="",
        description="""
Session ID for tracking and resuming.
Provided by the system."""
    )
    status: Literal["COMPLETE", "PARTIAL", "FAILED"] = Field(
        default="FAILED",
        description="""
Task completion status:
- COMPLETE: Task completed fully as requested
- PARTIAL: Task partially completed; some aspects could not be done
- FAILED: Task could not be completed"""
    )
    error: str = Field(
        default="",
        description="""
Error message if status is PARTIAL or FAILED.
Empty string if no error occurred."""
    )
    comments: str = Field(
        default="",
        description="""
Optional comments to the user explaining PARTIAL or FAILED status.
Empty string if no additional comments needed."""
    )
    output: str = Field(
        default="",
        description="""
Result data of the task in the format requested by the user.
Empty string if no text output to report."""
    )
    result_files: list[str] = Field(
        default_factory=list,
        description="""
List of generated files with relative paths to the working folder.
Each path must start with "./". Empty list if no files were generated."""
    )

    @classmethod
    def get_yaml_schema_example(cls) -> str:
        """
        Generate a YAML schema example string for template injection.

        Returns:
            YAML string showing the schema structure with descriptions.
        """
        schema_lines = [
            "session_id: \"<session_id>\"  # Provided by system, do not modify",
            "status: \"COMPLETE\" | \"PARTIAL\" | \"FAILED\"",
            "error: \"Error message if status is PARTIAL or FAILED, empty string otherwise\"",
            "comments: \"Optional comments explaining PARTIAL or FAILED status, empty string otherwise\"",
            "output: \"Result data as text, empty string if no text output\"",
            "result_files:  # List of generated files (relative paths starting with \"./\"), empty list if none",
            "  - \"./path/to/file1.ext\"",
            "  - \"./path/to/file2.ext\"",
        ]
        return "\n".join(schema_lines)

    @classmethod
    def create_empty(cls, session_id: str = "") -> "OutputSchema":
        """
        Create an empty OutputSchema with default values.

        Args:
            session_id: The session ID to include.

        Returns:
            OutputSchema with all fields set to defaults.
        """
        return cls(session_id=session_id)

    def to_yaml(self) -> str:
        """
        Serialize to YAML string.

        Returns:
            YAML representation of the output.
        """
        data = self.model_dump()
        return yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)

    @classmethod
    def from_yaml(cls, yaml_str: str) -> "OutputSchema":
        """
        Parse from YAML string.

        Args:
            yaml_str: YAML string to parse.

        Returns:
            OutputSchema instance.
        """
        data = yaml.safe_load(yaml_str)
        if data is None:
            data = {}
        return cls(**data)


# Model context window sizes (in tokens)
# These are the maximum context window sizes for each model
MODEL_CONTEXT_SIZES: dict[str, int] = {
    # Claude 4 models
    "claude-sonnet-4-5-20250929": 200_000,
    "claude-sonnet-4-20250514": 200_000,
    "claude-opus-4-20250514": 200_000,
    # Claude 3.5 models
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-5-sonnet-20240620": 200_000,
    "claude-3-5-haiku-20241022": 200_000,
    # Claude 3 models
    "claude-3-opus-20240229": 200_000,
    "claude-3-sonnet-20240229": 200_000,
    "claude-3-haiku-20240307": 200_000,
    # Default fallback
    "default": 200_000,
}


def get_model_context_size(model: str) -> int:
    """
    Get the context window size for a model.
    
    Args:
        model: The model name/identifier.
    
    Returns:
        The context window size in tokens.
    """
    return MODEL_CONTEXT_SIZES.get(model, MODEL_CONTEXT_SIZES["default"])


class TokenUsage(BaseModel):
    """
    Token usage statistics.
    
    Tracks input and output token counts, including cached tokens.
    """
    input_tokens: int = Field(
        default=0,
        description="Number of input tokens processed"
    )
    output_tokens: int = Field(
        default=0,
        description="Number of output tokens generated"
    )
    cache_creation_input_tokens: int = Field(
        default=0,
        description="Number of tokens used to create cache"
    )
    cache_read_input_tokens: int = Field(
        default=0,
        description="Number of tokens read from cache"
    )
    
    @property
    def total_input_tokens(self) -> int:
        """
        Total input tokens including cache operations.
        """
        return (
            self.input_tokens +
            self.cache_creation_input_tokens +
            self.cache_read_input_tokens
        )
    
    @property
    def total_tokens(self) -> int:
        """
        Total tokens (input + output).
        """
        return self.total_input_tokens + self.output_tokens
    
    def add(self, other: "TokenUsage") -> "TokenUsage":
        """
        Add another TokenUsage to this one (for accumulating stats).
        
        Args:
            other: Another TokenUsage instance to add.
        
        Returns:
            A new TokenUsage with combined values.
        """
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens +
                other.cache_creation_input_tokens
            ),
            cache_read_input_tokens=(
                self.cache_read_input_tokens +
                other.cache_read_input_tokens
            ),
        )
    
    @classmethod
    def from_sdk_usage(cls, usage: Optional[dict]) -> "TokenUsage":
        """
        Create TokenUsage from SDK usage dictionary.
        
        Args:
            usage: The usage dict from ResultMessage.
        
        Returns:
            TokenUsage instance.
        """
        if not usage:
            return cls()
        
        return cls(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_creation_input_tokens=usage.get(
                "cache_creation_input_tokens", 0
            ),
            cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
        )


class LLMMetrics(BaseModel):
    """
    Metrics from LLM execution.

    Contains information about the model used, execution time,
    number of turns, cost, and token usage.
    """
    model: str
    duration_ms: int
    num_turns: int
    session_id: str
    total_cost_usd: Optional[float] = None
    usage: Optional[TokenUsage] = Field(
        default=None,
        description="Token usage statistics"
    )


class AgentConfig(BaseModel):
    """
    Agent configuration.

    Defines the model, tools, execution limits, paths, and permissions.
    """
    model: str = Field(
        default="claude-sonnet-4-5-20250929",
        description="The Claude model to use"
    )
    allowed_tools: list[str] = Field(
        default_factory=lambda: ["Bash", "Read", "Write", "Edit", "Grep"],
        description="""
List of tools the agent can use.
Overridden by permissions_config if provided."""
    )
    max_turns: int = Field(
        default=100,
        description="Maximum number of turns for the agent"
    )
    timeout_seconds: int = Field(
        default=1800,
        description="Timeout in seconds (default 30 minutes)"
    )
    enable_skills: bool = Field(
        default=True,
        description="Enable custom skills from the skills/ folder"
    )
    skills_dir: Optional[str] = Field(
        default=None,
        description="Custom skills directory (defaults to AGENT/skills)"
    )
    working_dir: Optional[str] = Field(
        default=None,
        description="Working directory for the agent"
    )
    additional_dirs: list[str] = Field(
        default_factory=list,
        description="Additional directories the agent can access"
    )
    permissions_config: Optional[str] = Field(
        default=None,
        description="""
Path to permissions.json configuration file.
If not provided, uses AGENT/config/permissions.json or defaults."""
    )


class SessionInfo(BaseModel):
    """
    Session information.

    Contains session ID, timestamps, state, metrics, and cumulative
    token/cost statistics that persist across session resumptions.
    """
    session_id: str
    created_at: datetime = Field(default_factory=datetime.now)
    working_dir: str
    status: TaskStatus = TaskStatus.PARTIAL
    resume_id: Optional[str] = Field(
        default=None,
        description="Claude session ID for resuming"
    )
    num_turns: Optional[int] = Field(
        default=None,
        description="Number of turns in the session (current run only)"
    )
    duration_ms: Optional[int] = Field(
        default=None,
        description="Duration of the session in milliseconds (current run only)"
    )
    total_cost_usd: Optional[float] = Field(
        default=None,
        description="Total cost of the session in USD (current run only)"
    )
    # Cumulative statistics (persist across session resumptions)
    cumulative_turns: int = Field(
        default=0,
        description="Cumulative number of turns across all runs"
    )
    cumulative_duration_ms: int = Field(
        default=0,
        description="Cumulative duration in milliseconds across all runs"
    )
    cumulative_cost_usd: float = Field(
        default=0.0,
        description="Cumulative cost in USD across all runs"
    )
    cumulative_usage: Optional[TokenUsage] = Field(
        default=None,
        description="Cumulative token usage across all runs"
    )
    # Model information for context load tracking
    model: Optional[str] = Field(
        default=None,
        description="The model used in this session"
    )


class AgentResult(BaseModel):
    """
    Agent execution result.

    Contains the status, output, error, comments, result_files, and metrics.
    """
    status: TaskStatus
    output: Optional[str] = None
    error: Optional[str] = None
    comments: Optional[str] = None
    result_files: list[str] = Field(default_factory=list)
    metrics: Optional[LLMMetrics] = None
    session_info: Optional[SessionInfo] = None
