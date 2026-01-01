"""
Session management for Agentum.

Handles session creation, persistence, and resumption.
Each session has an isolated workspace with:
- skills/ - On-demand copied skills from global skills library
- output.yaml - Session-specific output (YAML format)
"""
import json
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from exceptions import SessionError
from schemas import OutputSchema, SessionInfo, TaskStatus, TokenUsage

logger = logging.getLogger(__name__)


class SessionManager:
    """
    Manages agent sessions.
    
    Sessions are stored in the sessions directory and include:
    - Session metadata (session_info.json)
    - Agent logs (agent.jsonl)
    - Output files (output.yaml)
    """
    
    def __init__(self, sessions_dir: Path) -> None:
        """
        Initialize the session manager.
        
        Args:
            sessions_dir: Directory to store sessions.
        """
        self._sessions_dir = sessions_dir
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
    
    def create_session(
        self,
        working_dir: str,
        session_id: Optional[str] = None
    ) -> SessionInfo:
        """
        Create a new session.
        
        Args:
            working_dir: Working directory for the session.
            session_id: Optional session ID. If None, generates one.
        
        Returns:
            SessionInfo for the new session.
        """
        if session_id is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            uid = uuid.uuid4().hex[:8]
            session_id = f"{ts}_{uid}"
        
        session_info = SessionInfo(
            session_id=session_id,
            working_dir=working_dir,
            status=TaskStatus.PARTIAL
        )
        
        session_dir = self.get_session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        
        self._save_session_info(session_info)
        logger.info(f"Created session: {session_id}")
        
        return session_info
    
    def get_session_dir(self, session_id: str) -> Path:
        """
        Get the directory for a session.
        
        Args:
            session_id: The session ID.
        
        Returns:
            Path to the session directory.
        """
        return self._sessions_dir / session_id
    
    def get_log_file(self, session_id: str) -> Path:
        """
        Get the log file path for a session.
        
        Args:
            session_id: The session ID.
        
        Returns:
            Path to the agent.jsonl file.
        """
        return self.get_session_dir(session_id) / "agent.jsonl"
    
    def get_output_file(self, session_id: str) -> Path:
        """
        Get the output file path for a session.
        
        Args:
            session_id: The session ID.
        
        Returns:
            Path to the output.yaml file.
        """
        return self.get_workspace_dir(session_id) / "output.yaml"
    
    def get_workspace_dir(self, session_id: str) -> Path:
        """
        Get the workspace directory for a session.
        
        The workspace is a sandboxed subdirectory where the agent can
        write output. This is separate from the session directory to
        prevent the agent from reading logs and other sensitive files.
        
        Args:
            session_id: The session ID.
        
        Returns:
            Path to the workspace directory.
        """
        workspace = self.get_session_dir(session_id) / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def get_workspace_skills_dir(self, session_id: str) -> Path:
        """
        Get the skills directory within a session's workspace.
        
        Skills are copied here on-demand when invoked.
        
        Args:
            session_id: The session ID.
        
        Returns:
            Path to the workspace/skills directory.
        """
        skills_dir = self.get_workspace_dir(session_id) / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        return skills_dir

    def copy_skill_to_workspace(
        self,
        session_id: str,
        skill_name: str,
        skill_source_dir: Path
    ) -> Path:
        """
        Copy a skill folder to the session's workspace.
        
        This enables skills to read/write files in their own folder
        with full isolation between sessions.
        
        Args:
            session_id: The session ID.
            skill_name: Name of the skill (folder name).
            skill_source_dir: Path to the source skill folder.
        
        Returns:
            Path to the copied skill folder in the workspace.
        
        Raises:
            SessionError: If the skill source doesn't exist.
        """
        if not skill_source_dir.exists():
            raise SessionError(
                f"Skill source not found: {skill_source_dir}"
            )
        
        workspace_skills = self.get_workspace_skills_dir(session_id)
        target_skill_dir = workspace_skills / skill_name
        
        # Skip if already copied
        if target_skill_dir.exists():
            logger.debug(
                f"Skill '{skill_name}' already in workspace for session {session_id}"
            )
            return target_skill_dir
        
        # Copy the entire skill folder
        shutil.copytree(skill_source_dir, target_skill_dir)
        logger.info(
            f"Copied skill '{skill_name}' to workspace for session {session_id}"
        )
        
        return target_skill_dir

    def is_skill_in_workspace(self, session_id: str, skill_name: str) -> bool:
        """
        Check if a skill has been copied to the session's workspace.
        
        Args:
            session_id: The session ID.
            skill_name: Name of the skill.
        
        Returns:
            True if skill is in workspace, False otherwise.
        """
        target_skill_dir = self.get_workspace_skills_dir(session_id) / skill_name
        return target_skill_dir.exists()

    def cleanup_workspace_skills(self, session_id: str) -> None:
        """
        Remove the skills folder from a session's workspace.
        
        Called after agent run completes to clean up copied skills.
        The output.yaml and other workspace files are preserved.
        
        Args:
            session_id: The session ID.
        """
        skills_dir = self.get_session_dir(session_id) / "workspace" / "skills"
        if skills_dir.exists():
            try:
                shutil.rmtree(skills_dir)
                logger.info(
                    f"Cleaned up workspace skills for session {session_id}"
                )
            except Exception as e:
                logger.warning(
                    f"Failed to cleanup workspace skills for session {session_id}: {e}"
                )
    
    def _save_session_info(self, session_info: SessionInfo) -> None:
        """Save session info to disk."""
        session_dir = self.get_session_dir(session_info.session_id)
        info_file = session_dir / "session_info.json"
        info_file.write_text(session_info.model_dump_json(indent=2))
    
    def load_session(self, session_id: str) -> SessionInfo:
        """
        Load an existing session.
        
        Args:
            session_id: The session ID to load.
        
        Returns:
            SessionInfo for the session.
        
        Raises:
            SessionError: If the session cannot be loaded.
        """
        session_dir = self.get_session_dir(session_id)
        info_file = session_dir / "session_info.json"
        
        if not info_file.exists():
            raise SessionError(f"Session not found: {session_id}")
        
        try:
            data = json.loads(info_file.read_text())
            return SessionInfo(**data)
        except (json.JSONDecodeError, ValueError) as e:
            raise SessionError(f"Failed to load session {session_id}: {e}")
    
    def update_session(
        self,
        session_info: SessionInfo,
        status: Optional[TaskStatus] = None,
        resume_id: Optional[str] = None,
        num_turns: Optional[int] = None,
        duration_ms: Optional[int] = None,
        total_cost_usd: Optional[float] = None,
        usage: Optional[TokenUsage] = None,
        model: Optional[str] = None
    ) -> SessionInfo:
        """
        Update an existing session with cumulative statistics.
        
        Stats from the current run are stored and also added to cumulative
        totals, enabling tracking across session resumptions.
        
        Args:
            session_info: The session to update.
            status: New status (optional).
            resume_id: Claude session ID for resuming (optional).
            num_turns: Number of turns in this run (optional).
            duration_ms: Duration of this run in milliseconds (optional).
            total_cost_usd: Cost of this run in USD (optional).
            usage: Token usage for this run (optional).
            model: The model used in this session (optional).
        
        Returns:
            Updated SessionInfo with cumulative stats.
        """
        if status is not None:
            session_info.status = status
        if resume_id is not None:
            session_info.resume_id = resume_id
        if model is not None:
            session_info.model = model
        
        # Update current run stats
        if num_turns is not None:
            session_info.num_turns = num_turns
            # Add to cumulative
            session_info.cumulative_turns += num_turns
        
        if duration_ms is not None:
            session_info.duration_ms = duration_ms
            # Add to cumulative
            session_info.cumulative_duration_ms += duration_ms
        
        if total_cost_usd is not None:
            session_info.total_cost_usd = total_cost_usd
            # Add to cumulative
            session_info.cumulative_cost_usd += total_cost_usd
        
        if usage is not None:
            # Add to cumulative usage
            if session_info.cumulative_usage is None:
                session_info.cumulative_usage = usage
            else:
                session_info.cumulative_usage = (
                    session_info.cumulative_usage.add(usage)
                )
        
        self._save_session_info(session_info)
        return session_info
    
    def list_sessions(self) -> list[SessionInfo]:
        """
        List all sessions.
        
        Returns:
            List of SessionInfo objects.
        """
        sessions = []
        for session_dir in self._sessions_dir.iterdir():
            if session_dir.is_dir():
                try:
                    sessions.append(self.load_session(session_dir.name))
                except SessionError:
                    continue
        
        return sorted(sessions, key=lambda s: s.created_at, reverse=True)
    
    def parse_output(self, session_id: str) -> dict:
        """
        Parse the output.yaml from a session.
        
        Args:
            session_id: The session ID.
        
        Returns:
            Parsed output as a dictionary with all schema fields.
        """
        output_file = self.get_output_file(session_id)
        if output_file.exists():
            try:
                data = yaml.safe_load(output_file.read_text())
                if data is None:
                    data = {}
                # Ensure all fields are present with defaults
                output = OutputSchema.create_empty(session_id=session_id)
                return output.model_copy(update=data).model_dump()
            except yaml.YAMLError:
                pass
        # Return default output with all fields
        return OutputSchema.create_empty(session_id=session_id).model_dump()


def generate_session_id() -> str:
    """Generate a unique session ID."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    uid = uuid.uuid4().hex[:8]
    return f"{ts}_{uid}"

