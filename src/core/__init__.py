"""
Core modules for Agentum.

This package contains all the core functionality:
- agent.py: CLI entry point
- agent_core.py: Core agent implementation
- permissions.py: Permission management
- permission_config.py: Centralized permission configuration
- schemas.py: Pydantic data models
- sessions.py: Session management
- skills.py: Skills loading and execution
- tasks.py: Task loading and management
- exceptions.py: Custom exceptions
- tracer.py: Execution tracing with console output
- trace_processor.py: SDK message to tracer bridge
"""
from .agent_core import ClaudeAgent, run_agent
from .exceptions import AgentError, TaskError, SkillError
from .permissions import (
    PermissionManager,
    PermissionDenial,
    PermissionDenialTracker,
    load_permissions_from_config,
    create_permission_callback,
)
from .schemas import (
    AgentConfig,
    AgentResult,
    TaskStatus,
    SessionInfo,
    TokenUsage,
    LLMMetrics,
    get_model_context_size,
    MODEL_CONTEXT_SIZES,
)
from .sessions import SessionManager
from .skills import SkillManager
from .tasks import load_task
from .tracer import (
    ExecutionTracer,
    TracerBase,
    QuietTracer,
    NullTracer,
    Color,
    Symbol,
)
from .trace_processor import (
    TraceProcessor,
    create_trace_hooks,
    create_stderr_callback,
)

__all__ = [
    # Agent
    "ClaudeAgent",
    "run_agent",
    # Exceptions
    "AgentError",
    "TaskError",
    "SkillError",
    # Permissions
    "PermissionManager",
    "PermissionDenial",
    "PermissionDenialTracker",
    "load_permissions_from_config",
    "create_permission_callback",
    # Schemas
    "AgentConfig",
    "AgentResult",
    "TaskStatus",
    "SessionInfo",
    "TokenUsage",
    "LLMMetrics",
    "get_model_context_size",
    "MODEL_CONTEXT_SIZES",
    # Managers
    "SessionManager",
    "SkillManager",
    # Tasks
    "load_task",
    # Tracing
    "ExecutionTracer",
    "TracerBase",
    "QuietTracer",
    "NullTracer",
    "Color",
    "Symbol",
    "TraceProcessor",
    "create_trace_hooks",
    "create_stderr_callback",
]
