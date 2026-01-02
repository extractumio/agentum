"""
Core modules for Agentum.

This package contains all the core functionality:
- agent.py: CLI entry point
- agent_core.py: Core agent implementation
- conversation.py: Multi-turn conversation sessions
- hooks.py: SDK hooks implementation
- permissions.py: Permission management
- permission_config.py: Centralized permission configuration
- schemas.py: Pydantic data models
- sessions.py: Session management
- skills.py: Skills loading and execution
- skill_tools.py: MCP tool wrappers for script-based skills
- tasks.py: Task loading and management
- exceptions.py: Custom exceptions
- tracer.py: Execution tracing with console output
- trace_processor.py: SDK message to tracer bridge
"""
from .agent_core import ClaudeAgent, run_agent
from .conversation import (
    ConversationSession,
    ConversationTurn,
    ConversationMetrics,
)
from .exceptions import AgentError, TaskError, SkillError
from .hooks import (
    HooksManager,
    HookResult,
    ToolUsageRecord,
    create_permission_hook,
    create_audit_hook,
    create_prompt_enhancement_hook,
    create_stop_hook,
    create_subagent_stop_hook,
    create_dangerous_command_hook,
)
from .permissions import (
    PermissionManager,
    PermissionDenial,
    PermissionDenialTracker,
    load_permissions_from_config,
    create_permission_callback,
    create_permission_hooks,
)
from .schemas import (
    AgentConfig,
    AgentResult,
    Checkpoint,
    CheckpointType,
    TaskStatus,
    SessionInfo,
    TokenUsage,
    LLMMetrics,
    get_model_context_size,
    MODEL_CONTEXT_SIZES,
)
from .sessions import SessionManager
from .skills import (
    SkillManager,
    SkillType,
    SkillIntegration,
    categorize_skills,
    get_instruction_skills_prompt,
)
from .skill_tools import (
    SkillToolsManager,
    SkillToolDefinition,
    SkillExecutionResult,
    create_skills_mcp_server,
    execute_skill_sync,
)
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
    # Conversation
    "ConversationSession",
    "ConversationTurn",
    "ConversationMetrics",
    # Exceptions
    "AgentError",
    "TaskError",
    "SkillError",
    # Hooks (new)
    "HooksManager",
    "HookResult",
    "ToolUsageRecord",
    "create_permission_hook",
    "create_audit_hook",
    "create_prompt_enhancement_hook",
    "create_stop_hook",
    "create_subagent_stop_hook",
    "create_dangerous_command_hook",
    # Permissions
    "PermissionManager",
    "PermissionDenial",
    "PermissionDenialTracker",
    "load_permissions_from_config",
    "create_permission_callback",
    "create_permission_hooks",
    # Schemas
    "AgentConfig",
    "AgentResult",
    "Checkpoint",
    "CheckpointType",
    "TaskStatus",
    "SessionInfo",
    "TokenUsage",
    "LLMMetrics",
    "get_model_context_size",
    "MODEL_CONTEXT_SIZES",
    # Managers
    "SessionManager",
    "SkillManager",
    # Skills (updated)
    "SkillType",
    "SkillIntegration",
    "categorize_skills",
    "get_instruction_skills_prompt",
    # Skill Tools (new)
    "SkillToolsManager",
    "SkillToolDefinition",
    "SkillExecutionResult",
    "create_skills_mcp_server",
    "execute_skill_sync",
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
