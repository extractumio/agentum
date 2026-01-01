"""
Execution Tracer for Agentum.

Provides real-time, fashionable console output for agent execution tracing
with spinners, colors, and dynamic content updates.

Usage:
    tracer = ExecutionTracer(verbose=True)
    
    # Hook into agent execution
    tracer.on_agent_start(session_id, model, tools)
    tracer.on_tool_start(tool_name, tool_input)
    tracer.on_tool_complete(tool_name, result, duration_ms, is_error)
    tracer.on_thinking(thinking_text)
    tracer.on_message(text)
    tracer.on_error(error_message)
    tracer.on_agent_complete(result_message)
"""
import asyncio
import sys
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Callable, Optional


class Color(StrEnum):
    """ANSI color codes for terminal output."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    UNDERLINE = "\033[4m"
    
    # Foreground colors
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    
    # Bright foreground colors
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"
    
    # Background colors
    BG_BLACK = "\033[40m"
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"
    BG_WHITE = "\033[47m"


class Symbol:
    """Unicode symbols for terminal decoration."""
    # Box drawing
    BOX_H = "─"
    BOX_V = "│"
    BOX_TL = "┌"
    BOX_TR = "┐"
    BOX_BL = "└"
    BOX_BR = "┘"
    BOX_T = "┬"
    BOX_B = "┴"
    BOX_L = "├"
    BOX_R = "┤"
    BOX_X = "┼"
    
    # Double box drawing
    DBOX_H = "═"
    DBOX_V = "║"
    DBOX_TL = "╔"
    DBOX_TR = "╗"
    DBOX_BL = "╚"
    DBOX_BR = "╝"
    
    # Arrows and pointers
    ARROW_RIGHT = "→"
    ARROW_LEFT = "←"
    ARROW_UP = "↑"
    ARROW_DOWN = "↓"
    TRIANGLE_RIGHT = "▶"
    TRIANGLE_DOWN = "▼"
    POINTER = "❯"
    
    # Status indicators
    CHECK = "✓"
    CROSS = "✗"
    WARN = "⚠"
    INFO = "ℹ"
    BULLET = "•"
    CIRCLE = "○"
    CIRCLE_FILLED = "●"
    STAR = "★"
    LIGHTNING = "⚡"
    
    # Spinners
    SPINNER_DOTS = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    SPINNER_LINE = ["—", "\\", "|", "/"]
    SPINNER_ARROW = ["←", "↖", "↑", "↗", "→", "↘", "↓", "↙"]
    SPINNER_BOUNCE = ["⠁", "⠂", "⠄", "⠂"]
    SPINNER_PULSE = ["◐", "◓", "◑", "◒"]
    
    # Misc - single-width Unicode symbols (no picture emojis)
    BRAIN = "◆"
    GEAR = "⚙"
    ROCKET = "»"
    TOOL = "⚙"
    FOLDER = "▣"
    FILE = "▫"
    CLOCK = "◔"
    MONEY = "◈"


class TerminalControl:
    """ANSI escape sequences for terminal control."""
    CLEAR_LINE = "\033[2K"
    CURSOR_UP = "\033[1A"
    CURSOR_DOWN = "\033[1B"
    CURSOR_START = "\033[0G"
    CURSOR_HIDE = "\033[?25l"
    CURSOR_SHOW = "\033[?25h"
    SAVE_CURSOR = "\033[s"
    RESTORE_CURSOR = "\033[u"
    
    @staticmethod
    def move_up(n: int = 1) -> str:
        return f"\033[{n}A"
    
    @staticmethod
    def move_down(n: int = 1) -> str:
        return f"\033[{n}B"
    
    @staticmethod
    def move_to_column(col: int) -> str:
        return f"\033[{col}G"


@dataclass
class SpinnerState:
    """State for spinner animation."""
    active: bool = False
    message: str = ""
    frames: list[str] = field(default_factory=lambda: Symbol.SPINNER_DOTS)
    frame_index: int = 0
    thread: Optional[threading.Thread] = None
    stop_event: Optional[threading.Event] = None


class TracerBase(ABC):
    """
    Abstract base class for execution tracing.
    
    Override these methods to customize tracing behavior.
    """
    
    @abstractmethod
    def on_agent_start(
        self,
        session_id: str,
        model: str,
        tools: list[str],
        working_dir: str,
        skills: Optional[list[str]] = None,
        task: Optional[str] = None
    ) -> None:
        """Called when the agent starts execution."""
        pass
    
    @abstractmethod
    def on_tool_start(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_id: str
    ) -> None:
        """Called before a tool/skill is executed."""
        pass
    
    @abstractmethod
    def on_tool_complete(
        self,
        tool_name: str,
        tool_id: str,
        result: Any,
        duration_ms: int,
        is_error: bool
    ) -> None:
        """Called after a tool/skill completes."""
        pass
    
    @abstractmethod
    def on_thinking(self, thinking_text: str) -> None:
        """Called when the agent is in thinking mode."""
        pass
    
    @abstractmethod
    def on_message(self, text: str, is_partial: bool = False) -> None:
        """Called when the agent generates a message."""
        pass
    
    @abstractmethod
    def on_error(self, error_message: str, error_type: str = "error") -> None:
        """Called when an error occurs."""
        pass
    
    @abstractmethod
    def on_agent_complete(
        self,
        status: str,
        num_turns: int,
        duration_ms: int,
        total_cost_usd: Optional[float],
        result: Optional[str],
        session_id: Optional[str] = None,
        usage: Optional[dict[str, Any]] = None,
        model: Optional[str] = None,
        cumulative_cost_usd: Optional[float] = None,
        cumulative_turns: Optional[int] = None,
        cumulative_tokens: Optional[int] = None
    ) -> None:
        """
        Called when the agent completes execution.
        
        Args:
            status: Final status (COMPLETE, PARTIAL, FAILED, etc.).
            num_turns: Number of turns in this run.
            duration_ms: Duration of this run in milliseconds.
            total_cost_usd: Cost of this run in USD.
            result: The result text/JSON.
            session_id: Claude session ID for resuming.
            usage: Token usage dictionary for this run.
            model: The model used.
            cumulative_cost_usd: Total cost across all runs (for resumed sessions).
            cumulative_turns: Total turns across all runs (for resumed sessions).
            cumulative_tokens: Total tokens across all runs (for resumed sessions).
        """
        pass

    @abstractmethod
    def on_profile_switch(
        self,
        profile_type: str,
        profile_name: str,
        tools: list[str],
        allow_rules_count: int = 0,
        deny_rules_count: int = 0,
        profile_path: Optional[str] = None
    ) -> None:
        """
        Called when permission profile is switched.
        
        Args:
            profile_type: Type of profile ("system" or "user").
            profile_name: Name of the profile.
            tools: List of available tools in this profile.
            allow_rules_count: Number of allow rules in the profile.
            deny_rules_count: Number of deny rules in the profile.
            profile_path: Path to the loaded profile file.
        """
        pass


class ExecutionTracer(TracerBase):
    """
    Fashionable console tracer for Agentum execution.
    
    Provides rich terminal output with:
    - Colored status indicators
    - Animated spinners for ongoing operations
    - Box-drawn sections for clarity
    - Dynamic line updates
    - Timing and cost information
    
    Args:
        verbose: Show detailed output including tool parameters.
        show_thinking: Display thinking block content.
        max_preview_length: Maximum characters for text previews.
        use_colors: Enable ANSI colors (auto-detect TTY).
        use_unicode: Enable Unicode symbols (fallback to ASCII).
    """
    
    def __init__(
        self,
        verbose: bool = True,
        show_thinking: bool = True,
        max_preview_length: int = 80,
        use_colors: bool = True,
        use_unicode: bool = True
    ) -> None:
        self.verbose = verbose
        self.show_thinking = show_thinking
        self.max_preview_length = max_preview_length
        self.use_colors = use_colors and sys.stdout.isatty()
        self.use_unicode = use_unicode
        
        self._spinner = SpinnerState()
        self._start_time: Optional[float] = None
        self._tool_start_times: dict[str, float] = {}
        self._turn_count = 0
        self._lock = threading.Lock()
        self._current_model: str = ""
        
        # Detect console width once at initialization
        self._console_width = self._detect_console_width()
        
        # Track agent start state and stored profile info
        self._agent_started: bool = False
        self._pending_profile: Optional[dict[str, Any]] = None
    
    def _detect_console_width(self) -> int:
        """Detect terminal width, with sensible default."""
        try:
            import shutil
            width = shutil.get_terminal_size().columns
            # Clamp to reasonable range
            return max(60, min(width, 120))
        except Exception:
            return 80  # Safe default
    
    def _get_short_model_id(self, model: str) -> str:
        """
        Extract a short model identifier for display.
        """
        # Fallback: return last segment or truncated model name
        parts = model.split("/")[-1].split(":")
        short = parts[0]
        if len(short) > 20:
            short = short[:17] + "..."
        return short
    
    # ═══════════════════════════════════════════════════════════════
    # Formatting Helpers
    # ═══════════════════════════════════════════════════════════════
    
    def _color(self, text: str, *colors: Color) -> str:
        """Apply color codes to text if colors are enabled."""
        if not self.use_colors:
            return text
        color_codes = "".join(str(c) for c in colors)
        return f"{color_codes}{text}{Color.RESET}"
    
    def _symbol(self, unicode_sym: str, ascii_fallback: str = "") -> str:
        """Return Unicode symbol or ASCII fallback."""
        if self.use_unicode:
            return unicode_sym
        return ascii_fallback or unicode_sym[0] if unicode_sym else ""
    
    def _timestamp(self) -> str:
        """Get formatted timestamp."""
        return datetime.now().strftime("%H:%M:%S")
    
    def _elapsed(self) -> str:
        """Get elapsed time since start."""
        if self._start_time is None:
            return "0.0s"
        elapsed = time.time() - self._start_time
        if elapsed < 60:
            return f"{elapsed:.1f}s"
        minutes = int(elapsed // 60)
        seconds = elapsed % 60
        return f"{minutes}m {seconds:.1f}s"
    
    def _truncate(self, text: str, max_len: Optional[int] = None) -> str:
        """Truncate text with ellipsis."""
        max_len = max_len or self.max_preview_length
        text = text.replace("\n", " ").replace("\r", "").strip()
        if len(text) <= max_len:
            return text
        return text[:max_len - 3] + "..."
    
    def _truncate_path(self, path: str, max_len: int = 80) -> str:
        """
        Truncate a path intelligently, keeping both start and end visible.
        
        Shows the beginning of the path and the filename/end portion,
        with '...' in the middle.
        
        Example:
            '/home/user/projects/myapp/src/components/Button.tsx'
            -> '/home/user/pro.../components/Button.tsx'
        """
        if len(path) <= max_len:
            return path
        
        # Reserve space for ellipsis
        ellipsis = "..."
        available = max_len - len(ellipsis)
        
        # Try to find a good split point - prefer keeping more of the end (filename)
        # Give ~40% to start, ~60% to end
        start_len = available * 2 // 5
        end_len = available - start_len
        
        # Ensure minimum lengths
        start_len = max(start_len, 10)
        end_len = max(end_len, 15)
        
        # Adjust if path is shorter than expected
        if start_len + end_len > available:
            start_len = available // 2
            end_len = available - start_len
        
        return path[:start_len] + ellipsis + path[-end_len:]
    
    def _is_path_like(self, key: str, value: str) -> bool:
        """Check if a key-value pair looks like a file path."""
        path_keys = {"file_path", "path", "filepath", "directory", "dir", "folder", "cwd"}
        if key.lower() in path_keys:
            return True
        # Also check if value looks like a path
        if isinstance(value, str) and (value.startswith("/") or value.startswith("~/")):
            return True
        return False
    
    def _char_width(self, char: str) -> int:
        """Get visual width of a single character."""
        code = ord(char)
        # Wide character ranges (emojis, CJK, etc.) - 2 char width
        wide_ranges = [
            (0x1100, 0x115F),    # Hangul Jamo
            (0x231A, 0x231B),    # Watch, Hourglass
            (0x23E9, 0x23F3),    # Various symbols
            (0x23F8, 0x23FA),    # Various symbols
            (0x25AA, 0x25AB),    # Squares
            (0x25B6, 0x25B6),    # Play button
            (0x25C0, 0x25C0),    # Reverse button
            (0x25FB, 0x25FE),    # Squares
            (0x2614, 0x2615),    # Umbrella, Hot beverage
            (0x2648, 0x2653),    # Zodiac
            (0x267F, 0x267F),    # Wheelchair
            (0x2693, 0x2693),    # Anchor
            (0x26A1, 0x26A1),    # High voltage
            (0x26AA, 0x26AB),    # Circles
            (0x26BD, 0x26BE),    # Soccer, Baseball
            (0x26C4, 0x26C5),    # Snowman, Sun
            (0x26CE, 0x26CE),    # Ophiuchus
            (0x26D4, 0x26D4),    # No entry
            (0x26EA, 0x26EA),    # Church
            (0x26F2, 0x26F3),    # Fountain, Golf
            (0x26F5, 0x26F5),    # Sailboat
            (0x26FA, 0x26FA),    # Tent
            (0x26FD, 0x26FD),    # Fuel pump
            (0x2702, 0x2702),    # Scissors
            (0x2705, 0x2705),    # White check mark ✅
            (0x2708, 0x270D),    # Airplane to Writing hand
            (0x270F, 0x270F),    # Pencil
            (0x2712, 0x2712),    # Black nib
            (0x2714, 0x2714),    # Check mark ✔
            (0x2716, 0x2716),    # X mark ✖
            (0x271D, 0x271D),    # Latin cross
            (0x2721, 0x2721),    # Star of David
            (0x2728, 0x2728),    # Sparkles
            (0x2733, 0x2734),    # Eight spoked asterisk
            (0x2744, 0x2744),    # Snowflake
            (0x2747, 0x2747),    # Sparkle
            (0x274C, 0x274C),    # Cross mark ❌
            (0x274E, 0x274E),    # Cross mark
            (0x2753, 0x2755),    # Question marks
            (0x2757, 0x2757),    # Exclamation mark
            (0x2763, 0x2764),    # Heart exclamation, Heart
            (0x2795, 0x2797),    # Plus, Minus, Division
            (0x27A1, 0x27A1),    # Right arrow
            (0x27B0, 0x27B0),    # Curly loop
            (0x27BF, 0x27BF),    # Double curly loop
            (0x2934, 0x2935),    # Arrows
            (0x2E80, 0x9FFF),    # CJK
            (0xF900, 0xFAFF),    # CJK Compatibility
            (0x1F000, 0x1F02F),  # Mahjong
            (0x1F0A0, 0x1F0FF),  # Playing Cards
            (0x1F100, 0x1F1FF),  # Enclosed Alphanumeric Supplement (flags)
            (0x1F200, 0x1F2FF),  # Enclosed Ideographic Supplement
            (0x1F300, 0x1F5FF),  # Misc Symbols and Pictographs (🌀-🗿)
            (0x1F600, 0x1F64F),  # Emoticons (😀-🙏)
            (0x1F680, 0x1F6FF),  # Transport and Map Symbols (🚀 etc)
            (0x1F700, 0x1F77F),  # Alchemical Symbols
            (0x1F780, 0x1F7FF),  # Geometric Shapes Extended
            (0x1F800, 0x1F8FF),  # Supplemental Arrows-C
            (0x1F900, 0x1F9FF),  # Supplemental Symbols (🤖 etc)
            (0x1FA00, 0x1FA6F),  # Chess Symbols
            (0x1FA70, 0x1FAFF),  # Symbols and Pictographs Extended-A
        ]
        
        for start, end in wide_ranges:
            if start <= code <= end:
                return 2
        
        # Variation selectors (invisible, zero width)
        if 0xFE00 <= code <= 0xFE0F:
            return 0
        
        return 1
    
    def _visual_width(self, text: str) -> int:
        """
        Calculate visual width of text, accounting for wide characters (emojis).
        """
        return sum(self._char_width(char) for char in text)
    
    def _truncate_to_visual_width(self, text: str, max_width: int) -> str:
        """Truncate text to fit within a visual width, accounting for wide chars."""
        if self._visual_width(text) <= max_width:
            return text
        
        result = []
        current_width = 0
        for char in text:
            char_width = self._char_width(char)
            if current_width + char_width > max_width - 3:  # Leave room for "..."
                break
            result.append(char)
            current_width += char_width
        
        return "".join(result) + "..."
    
    def _format_json_preview(
        self,
        value: Any,
        max_lines: int = 10,
        max_line_length: int = 80,
        indent: int = 2
    ) -> list[str]:
        """
        Format a JSON-like object as pretty-printed preview lines.
        
        Args:
            value: The value to format (dict, list, or other).
            max_lines: Maximum number of lines to return.
            max_line_length: Maximum length per line.
            indent: Indentation spaces for JSON.
        
        Returns:
            List of formatted lines.
        """
        import json as json_mod
        
        try:
            if isinstance(value, (dict, list)):
                formatted = json_mod.dumps(value, indent=indent, ensure_ascii=False)
            else:
                formatted = str(value)
        except (TypeError, ValueError):
            formatted = str(value)
        
        lines = formatted.split("\n")
        result = []
        
        for i, line in enumerate(lines):
            if i >= max_lines:
                remaining = len(lines) - max_lines
                result.append(f"... +{remaining} more lines")
                break
            
            if len(line) > max_line_length:
                line = line[:max_line_length - 3] + "..."
            result.append(line)
        
        return result
    
    def _format_todo_plan(
        self,
        todos: list[dict[str, Any]],
        indent: int = 6
    ) -> list[str]:
        """
        Format todos as a plan-like tree with:
        - Completed items dimmed
        - In-progress items bold
        - Pending items normal
        - Shows: previous completed, current, next 2, then <...>
        
        Args:
            todos: List of todo items with 'content' and 'status' fields.
            indent: Number of spaces for base indentation.
        
        Returns:
            List of formatted lines to print.
        """
        if not todos:
            return []
        
        lines: list[str] = []
        bar = self._symbol(Symbol.BOX_V, "|")
        branch = self._symbol(Symbol.BOX_L, "|-")
        last_branch = self._symbol(Symbol.BOX_BL, "`-")
        
        # Find current in-progress item index
        current_idx = -1
        for i, todo in enumerate(todos):
            status = todo.get("status", "pending")
            if status == "in_progress":
                current_idx = i
                break
        
        # Determine which items to show:
        # - One previous completed (if exists)
        # - Current in_progress
        # - Next 2 pending
        # - <...> if more exist
        
        if current_idx == -1:
            # No in_progress - show first few items
            start_idx = 0
            end_idx = min(4, len(todos))
            show_ellipsis = len(todos) > 4
        else:
            # Find the last completed item before current
            prev_completed_idx = -1
            for i in range(current_idx - 1, -1, -1):
                if todos[i].get("status") == "completed":
                    prev_completed_idx = i
                    break
            
            # Calculate visible range
            start_idx = prev_completed_idx if prev_completed_idx >= 0 else current_idx
            end_idx = min(current_idx + 3, len(todos))  # current + next 2
            
            # Check if there are hidden items before
            hidden_before = start_idx > 0
            show_ellipsis = end_idx < len(todos)
            
            if hidden_before:
                lines.append(
                    f"{' ' * indent}{self._color(bar, Color.DIM)} "
                    f"{self._color('<...>', Color.DIM)}"
                )
        
        # Status symbols
        status_symbols = {
            "completed": self._symbol(Symbol.CHECK, "v"),
            "in_progress": self._symbol(Symbol.POINTER, ">"),
            "pending": self._symbol(Symbol.CIRCLE, "o"),
            "cancelled": self._symbol(Symbol.CROSS, "x"),
        }
        
        for i in range(start_idx, end_idx):
            todo = todos[i]
            is_last = (i == end_idx - 1) and not show_ellipsis
            connector = last_branch if is_last else branch
            
            status = todo.get("status", "pending")
            content = todo.get("content", "")
            
            # Truncate long content
            max_content_len = 55
            if len(content) > max_content_len:
                content = content[:max_content_len - 3] + "..."
            
            # Format based on status
            sym = status_symbols.get(status, self._symbol(Symbol.CIRCLE, "o"))
            
            if status == "completed":
                # Dimmed for completed
                line = (
                    f"{' ' * indent}{self._color(bar, Color.DIM)} "
                    f"{self._color(connector, Color.DIM)} "
                    f"{self._color(sym, Color.DIM)} "
                    f"{self._color(content, Color.DIM)}"
                )
            elif status == "in_progress":
                # Bold for in-progress
                line = (
                    f"{' ' * indent}{self._color(bar, Color.DIM)} "
                    f"{self._color(connector, Color.BRIGHT_CYAN)} "
                    f"{self._color(sym, Color.BRIGHT_CYAN, Color.BOLD)} "
                    f"{self._color(content, Color.WHITE, Color.BOLD)}"
                )
            else:
                # Normal for pending
                line = (
                    f"{' ' * indent}{self._color(bar, Color.DIM)} "
                    f"{self._color(connector, Color.DIM)} "
                    f"{self._color(sym, Color.WHITE)} "
                    f"{self._color(content, Color.WHITE)}"
                )
            
            lines.append(line)
        
        # Add ellipsis if more items exist
        if show_ellipsis:
            remaining = len(todos) - end_idx
            lines.append(
                f"{' ' * indent}{self._color(bar, Color.DIM)} "
                f"{self._color(last_branch, Color.DIM)} "
                f"{self._color(f'<... {remaining} more>', Color.DIM)}"
            )
        
        return lines
    
    def _format_duration(self, ms: int) -> str:
        """Format duration in milliseconds to human readable."""
        if ms < 1000:
            return f"{ms}ms"
        elif ms < 60000:
            return f"{ms / 1000:.1f}s"
        else:
            minutes = ms // 60000
            seconds = (ms % 60000) / 1000
            return f"{minutes}m {seconds:.1f}s"
    
    def _format_cost(self, cost: float) -> str:
        """Format cost in USD."""
        if cost < 0.01:
            return f"${cost:.4f}"
        return f"${cost:.2f}"
    
    # ═══════════════════════════════════════════════════════════════
    # Output Methods
    # ═══════════════════════════════════════════════════════════════
    
    def _write(self, text: str, end: str = "\n") -> None:
        """Thread-safe write to stdout."""
        with self._lock:
            sys.stdout.write(text + end)
            sys.stdout.flush()
    
    def _clear_line(self) -> None:
        """Clear current line."""
        if self.use_colors:
            self._write(TerminalControl.CLEAR_LINE + TerminalControl.CURSOR_START, end="")
    
    def _print_box(
        self,
        lines: list[str],
        title: Optional[str] = None,
        color: Color = Color.DIM,
        title_color: Optional[Color] = None,
        border_color: Optional[Color] = None,
        width: Optional[int] = None,
        center_title: bool = False
    ) -> None:
        """
        Print content inside a box with single-line borders.
        
        Args:
            lines: List of content lines to display inside the box.
            title: Optional title for the box header.
            color: Color for default content.
            title_color: Color for the title (defaults to color).
            border_color: Color for all borders (defaults to color).
            width: Box width (defaults to console width - 4 for margins).
            center_title: Whether to center the title text.
        """
        width = width or (self._console_width - 4)
        title_color = title_color or color
        border_color = border_color or color
        inner_width = width - 2  # Subtract 2 for border chars
        
        # Box drawing chars (single line)
        tl = self._symbol(Symbol.BOX_TL, "+")
        tr = self._symbol(Symbol.BOX_TR, "+")
        bl = self._symbol(Symbol.BOX_BL, "+")
        br = self._symbol(Symbol.BOX_BR, "+")
        h = self._symbol(Symbol.BOX_H, "-")
        v = self._symbol(Symbol.BOX_V, "|")
        lt = self._symbol(Symbol.BOX_L, "+")
        rt = self._symbol(Symbol.BOX_R, "+")
        
        # Top border
        self._write(self._color(f"{tl}{h * inner_width}{tr}", border_color))
        
        # Title line (if provided)
        if title:
            title_visual_width = self._visual_width(title)
            if center_title:
                # Center the title, accounting for visual width
                left_pad = (inner_width - title_visual_width) // 2
                right_pad = inner_width - title_visual_width - left_pad
                title_padded = " " * left_pad + title + " " * right_pad
            else:
                title_content = f" {title}"
                title_visual = self._visual_width(title_content)
                title_padded = title_content + " " * (inner_width - title_visual)
            
            self._write(
                self._color(v, border_color) +
                self._color(title_padded, title_color, Color.BOLD) +
                self._color(v, border_color)
            )
            # Separator after title
            self._write(self._color(f"{lt}{h * inner_width}{rt}", border_color))
        
        # Content lines
        for line in lines:
            # Truncate if too long (accounting for wide characters)
            visual_len = self._visual_width(line)
            if visual_len > inner_width:
                line = self._truncate_to_visual_width(line, inner_width)
                visual_len = self._visual_width(line)
            
            # Pad to inner_width (accounting for visual width)
            padding_needed = inner_width - visual_len
            line_padded = line + " " * max(0, padding_needed)
            
            self._write(
                self._color(v, border_color) +
                line_padded +
                self._color(v, border_color)
            )
        
        # Bottom border
        self._write(self._color(f"{bl}{h * inner_width}{br}", border_color))
    
    def print_task(self, task: str) -> None:
        """
        Print the task description in a box (first 5 lines).
        
        Args:
            task: The task description text.
        """
        # Use full console width
        box_width = self._console_width - 2
        inner_width = box_width - 4  # Account for box borders and padding
        
        # Split into lines and wrap long lines
        task_lines: list[str] = []
        
        for line in task.strip().split("\n"):
            if len(line) <= inner_width:
                task_lines.append(f" {line}")
            else:
                # Wrap long lines
                wrapped = self._wrap_text(line, inner_width)
                task_lines.extend(f" {w}" for w in wrapped)
        
        # Limit to 5 lines
        display_lines = task_lines[:5]
        if len(task_lines) > 5:
            display_lines.append(f" ... +{len(task_lines) - 5} more lines")
        
        self._print_box(
            lines=display_lines,
            title="TASK",
            color=Color.WHITE,
            title_color=Color.BRIGHT_WHITE,
            border_color=Color.DIM,
            width=box_width,
            center_title=False
        )
        self._write("")
    
    def _print_header(self, title: str, color: Color = Color.BRIGHT_CYAN) -> None:
        """Print a decorated header with single-line box drawing."""
        width = self._console_width - 2  # Full console width with small margin
        inner_width = width - 2
        
        tl = self._symbol(Symbol.BOX_TL, "+")
        tr = self._symbol(Symbol.BOX_TR, "+")
        bl = self._symbol(Symbol.BOX_BL, "+")
        br = self._symbol(Symbol.BOX_BR, "+")
        h = self._symbol(Symbol.BOX_H, "-")
        v = self._symbol(Symbol.BOX_V, "|")
        
        # Build title content
        star = self._symbol(Symbol.STAR, "*")
        title_content = f" {star} {title}"
        title_padded = title_content.ljust(inner_width)
        
        self._write("")
        self._write(self._color(f"{tl}{h * inner_width}{tr}", color))
        self._write(
            self._color(v, color) +
            self._color(title_padded, color, Color.BOLD) +
            self._color(v, color)
        )
        self._write(self._color(f"{bl}{h * inner_width}{br}", color))
    
    def _print_footer(self, color: Color = Color.BRIGHT_CYAN) -> None:
        """Print a decorated footer."""
        width = 60
        border = self._symbol(Symbol.DBOX_H, "=") * width
        self._write(self._color(border, color))
        self._write("")
    
    def _print_line(
        self,
        prefix: str,
        message: str,
        color: Color = Color.WHITE,
        prefix_color: Optional[Color] = None,
        indent: int = 0
    ) -> None:
        """Print a formatted line with prefix."""
        prefix_color = prefix_color or color
        indent_str = "  " * indent
        bar = self._symbol(Symbol.BOX_V, "|")
        
        formatted = (
            f"{indent_str}"
            f"{self._color(bar, Color.DIM)} "
            f"{self._color(prefix, prefix_color, Color.BOLD)} "
            f"{self._color(message, color)}"
        )
        self._write(formatted)
    
    def _print_key_value(
        self,
        key: str,
        value: str,
        color: Color = Color.WHITE,
        indent: int = 1
    ) -> None:
        """Print a key-value pair."""
        indent_str = "  " * indent
        bar = self._symbol(Symbol.BOX_V, "|")
        bullet = self._symbol(Symbol.BULLET, "-")
        
        formatted = (
            f"{indent_str}"
            f"{self._color(bar, Color.DIM)} "
            f"{self._color(bullet, Color.DIM)} "
            f"{self._color(key + ':', Color.DIM)} "
            f"{self._color(value, color)}"
        )
        self._write(formatted)
    
    # ═══════════════════════════════════════════════════════════════
    # Spinner Control
    # ═══════════════════════════════════════════════════════════════
    
    def _start_spinner(
        self,
        message: str,
        frames: Optional[list[str]] = None
    ) -> None:
        """Start an animated spinner."""
        if not self.use_colors or not sys.stdout.isatty():
            self._write(f"  {self._symbol(Symbol.GEAR, '*')} {message}...")
            return
        
        self._stop_spinner()
        
        self._spinner.active = True
        self._spinner.message = message
        self._spinner.frames = frames or Symbol.SPINNER_DOTS
        self._spinner.frame_index = 0
        self._spinner.stop_event = threading.Event()
        
        def spin() -> None:
            while not self._spinner.stop_event.is_set():
                frame = self._spinner.frames[self._spinner.frame_index]
                self._spinner.frame_index = (
                    (self._spinner.frame_index + 1) % len(self._spinner.frames)
                )
                
                with self._lock:
                    sys.stdout.write(TerminalControl.CLEAR_LINE)
                    sys.stdout.write(TerminalControl.CURSOR_START)
                    sys.stdout.write(
                        f"  {self._color(frame, Color.CYAN)} "
                        f"{self._color(self._spinner.message, Color.DIM)}"
                    )
                    sys.stdout.flush()
                
                time.sleep(0.08)
        
        self._spinner.thread = threading.Thread(target=spin, daemon=True)
        self._spinner.thread.start()
    
    def _stop_spinner(self, final_message: str = "", success: bool = True) -> None:
        """Stop the spinner with optional final message."""
        if not self._spinner.active:
            return
        
        if self._spinner.stop_event:
            self._spinner.stop_event.set()
        if self._spinner.thread:
            self._spinner.thread.join(timeout=0.5)
        
        self._spinner.active = False
        
        if self.use_colors and sys.stdout.isatty():
            with self._lock:
                sys.stdout.write(TerminalControl.CLEAR_LINE)
                sys.stdout.write(TerminalControl.CURSOR_START)
                
                if final_message:
                    symbol = Symbol.CHECK if success else Symbol.CROSS
                    color = Color.GREEN if success else Color.RED
                    sys.stdout.write(
                        f"  {self._color(symbol, color)} "
                        f"{self._color(final_message, color)}\n"
                    )
                sys.stdout.flush()
    
    # ═══════════════════════════════════════════════════════════════
    # TracerBase Implementation
    # ═══════════════════════════════════════════════════════════════
    
    def _format_tools_grid(
        self,
        tools: list[str],
        columns: int = 4,
        col_width: int = 18
    ) -> list[str]:
        """Format tools into a neat grid layout."""
        lines = []
        for i in range(0, len(tools), columns):
            row_tools = tools[i:i + columns]
            row = "  ".join(tool.ljust(col_width)[:col_width] for tool in row_tools)
            lines.append(row)
        return lines
    
    def _wrap_text(self, text: str, width: int = 70) -> list[str]:
        """Wrap text to specified width, preserving words."""
        if len(text) <= width:
            return [text]
        
        words = text.split()
        lines = []
        current_line: list[str] = []
        current_len = 0
        
        for word in words:
            word_len = len(word)
            if current_len + word_len + (1 if current_line else 0) <= width:
                current_line.append(word)
                current_len += word_len + (1 if len(current_line) > 1 else 0)
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                current_line = [word]
                current_len = word_len
        
        if current_line:
            lines.append(" ".join(current_line))
        
        return lines
    
    def on_agent_start(
        self,
        session_id: str,
        model: str,
        tools: list[str],
        working_dir: str,
        skills: Optional[list[str]] = None,
        task: Optional[str] = None
    ) -> None:
        """Called when the agent starts execution."""
        self._start_time = time.time()
        self._turn_count = 0
        self._current_model = model
        self._agent_started = True
        
        self._print_header(
            "Agentum | Self-Improving Agent",
            Color.BRIGHT_CYAN
        )
        
        bar = self._symbol(Symbol.BOX_V, "|")
        
        # Session info with consistent indentation
        self._write(
            f"  {self._color(bar, Color.DIM)} "
            f"{self._color(self._symbol(Symbol.LIGHTNING, '*'), Color.BRIGHT_CYAN)} "
            f"{self._color('SESSION', Color.BRIGHT_CYAN, Color.BOLD)} "
            f"{self._color(session_id, Color.CYAN)}"
        )
        
        self._write(
            f"  {self._color(bar, Color.DIM)} "
            f"{self._color(self._symbol(Symbol.BULLET, '-'), Color.DIM)} "
            f"{self._color('Model:', Color.DIM)} "
            f"{self._color(model, Color.BRIGHT_WHITE)}"
        )
        
        self._write(
            f"  {self._color(bar, Color.DIM)} "
            f"{self._color(self._symbol(Symbol.BULLET, '-'), Color.DIM)} "
            f"{self._color('Working Dir:', Color.DIM)} "
            f"{self._color(self._truncate_path(working_dir, 60), Color.DIM)}"
        )
        
        self._write(
            f"  {self._color(bar, Color.DIM)} "
            f"{self._color(self._symbol(Symbol.BULLET, '-'), Color.DIM)} "
            f"{self._color('Started:', Color.DIM)} "
            f"{self._color(self._timestamp(), Color.DIM)}"
        )
        
        self._write(f"  {self._color(bar, Color.DIM)}")
        
        # Display permission profile info if available (from pending profile switch)
        if self._pending_profile:
            profile_type = self._pending_profile["profile_type"]
            profile_name = self._pending_profile["profile_name"]
            profile_tools = self._pending_profile["tools"]
            allow_count = self._pending_profile["allow_rules_count"]
            deny_count = self._pending_profile["deny_rules_count"]
            profile_path = self._pending_profile.get("profile_path")
            
            # Choose color based on profile type
            if profile_type.lower() == "system":
                profile_color = Color.BRIGHT_MAGENTA
            else:
                profile_color = Color.BRIGHT_GREEN
            
            # Profile line
            self._write(
                f"  {self._color(bar, Color.DIM)} "
                f"{self._color(self._symbol(Symbol.STAR, '*'), profile_color)} "
                f"{self._color('Profile:', Color.DIM)} "
                f"{self._color(profile_type.upper(), profile_color, Color.BOLD)} "
                f"{self._color(f'({profile_name})', Color.DIM)} "
                f"{self._color(f'[allow={allow_count}, deny={deny_count}]', Color.DIM)}"
            )
            
            # Profile path
            if profile_path:
                # Shorten the path for display
                path_display = profile_path
                if len(path_display) > 50:
                    path_display = "..." + path_display[-47:]
                self._write(
                    f"  {self._color(bar, Color.DIM)} "
                    f"{self._color(self._symbol(Symbol.BULLET, '-'), Color.DIM)} "
                    f"{self._color('Loaded:', Color.DIM)} "
                    f"{self._color(path_display, Color.DIM)}"
                )
            
            self._write(f"  {self._color(bar, Color.DIM)}")
            
            # Tools section with grid layout - use profile tools
            self._write(
                f"  {self._color(bar, Color.DIM)} "
                f"{self._color(self._symbol(Symbol.GEAR, '*'), Color.BRIGHT_WHITE)} "
                f"{self._color('Tools:', Color.DIM)} "
                f"{self._color(str(len(profile_tools)), Color.BRIGHT_WHITE)} "
                f"{self._color('available', Color.DIM)}"
            )
            
            tool_lines = self._format_tools_grid(profile_tools, columns=4, col_width=18)
            for line in tool_lines:
                self._write(
                    f"  {self._color(bar, Color.DIM)}   "
                    f"{self._color(line, Color.DIM)}"
                )
            
            # Clear pending profile
            self._pending_profile = None
        else:
            # No profile info - display tools from on_agent_start args
            self._write(
                f"  {self._color(bar, Color.DIM)} "
                f"{self._color(self._symbol(Symbol.GEAR, '*'), Color.BRIGHT_WHITE)} "
                f"{self._color('Tools:', Color.DIM)} "
                f"{self._color(str(len(tools)), Color.BRIGHT_WHITE)} "
                f"{self._color('available', Color.DIM)}"
            )
            
            tool_lines = self._format_tools_grid(tools, columns=4, col_width=18)
            for line in tool_lines:
                self._write(
                    f"  {self._color(bar, Color.DIM)}   "
                    f"{self._color(line, Color.DIM)}"
                )
        
        # Print loaded skills if any
        if skills:
            self._write(f"  {self._color(bar, Color.DIM)}")
            self._write(
                f"  {self._color(bar, Color.DIM)} "
                f"{self._color(self._symbol(Symbol.STAR, '*'), Color.BRIGHT_MAGENTA)} "
                f"{self._color('Skills:', Color.DIM)} "
                f"{self._color(str(len(skills)), Color.BRIGHT_MAGENTA)} "
                f"{self._color('loaded', Color.DIM)}"
            )
            skill_lines = self._format_tools_grid(skills, columns=4, col_width=16)
            for line in skill_lines:
                self._write(
                    f"  {self._color(bar, Color.DIM)}   "
                    f"{self._color(line, Color.MAGENTA)}"
                )
        
        # Bottom border
        bl = self._symbol(Symbol.BOX_BL, "+")
        h = self._symbol(Symbol.BOX_H, "-")
        self._write(self._color(f"  {bl}{h * 57}", Color.DIM))
        self._write("")
        
        # Print task if provided
        if task:
            self.print_task(task)
    
    def on_tool_start(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_id: str
    ) -> None:
        """Called before a tool/skill is executed."""
        self._turn_count += 1
        self._tool_start_times[tool_id] = time.time()
        
        # Build tool display
        tool_icon = self._symbol(Symbol.TOOL, ">")
        turn_badge = self._color(f"[{self._turn_count}]", Color.DIM)
        
        self._write(
            f"  {self._color(tool_icon, Color.CYAN)} "
            f"{turn_badge} "
            f"{self._color(tool_name, Color.BRIGHT_CYAN, Color.BOLD)}"
        )
        
        if self.verbose and tool_input:
            bar = self._color(Symbol.BOX_V, Color.DIM)
            
            # Special handling for TodoWrite - display as plan tree
            if tool_name == "TodoWrite" and "todos" in tool_input:
                todos = tool_input.get("todos", [])
                if isinstance(todos, str):
                    try:
                        import json as json_mod
                        todos = json_mod.loads(todos)
                    except (json_mod.JSONDecodeError, TypeError):
                        todos = []
                
                if isinstance(todos, list) and todos:
                    plan_lines = self._format_todo_plan(todos, indent=4)
                    for line in plan_lines:
                        self._write(line)
                # Skip the regular tool_input display for TodoWrite
            else:
                for key, value in tool_input.items():
                    # Check if value is a complex object (dict/list) or JSON-like string
                    is_complex = isinstance(value, (dict, list))
                    is_json_string = (
                        isinstance(value, str) and 
                        len(value) > 50 and 
                        (value.strip().startswith("{") or value.strip().startswith("["))
                    )
                    
                    if is_complex or is_json_string:
                        # Pretty print JSON objects
                        self._write(
                            f"      {bar} {self._color(Symbol.BULLET, Color.DIM)} "
                            f"{self._color(key + ':', Color.DIM)}"
                        )
                        
                        # Parse JSON string if needed
                        if is_json_string:
                            try:
                                import json as json_mod
                                value = json_mod.loads(value)
                            except (json_mod.JSONDecodeError, TypeError):
                                pass
                        
                        # Format and print each line
                        json_lines = self._format_json_preview(
                            value, max_lines=10, max_line_length=80
                        )
                        for json_line in json_lines:
                            self._write(
                                f"      {bar}     "
                                f"{self._color(json_line, Color.WHITE)}"
                            )
                    else:
                        # Simple value - single line
                        value_str = str(value)
                        max_display_len = 80
                        if len(value_str) > max_display_len:
                            # Use smart path truncation for path-like values
                            if self._is_path_like(key, value_str):
                                value_str = self._truncate_path(value_str, max_display_len)
                            else:
                                value_str = value_str[:max_display_len - 3] + "..."
                        value_str = value_str.replace("\n", "\\n")
                        self._print_key_value(
                            key,
                            self._color(value_str, Color.WHITE),
                            Color.DIM,
                            indent=2
                        )
        
        self._start_spinner(f"Executing {tool_name}...")
    
    def on_tool_complete(
        self,
        tool_name: str,
        tool_id: str,
        result: Any,
        duration_ms: int,
        is_error: bool
    ) -> None:
        """Called after a tool/skill completes."""
        # Calculate actual duration if we have start time
        if tool_id in self._tool_start_times:
            actual_ms = int((time.time() - self._tool_start_times[tool_id]) * 1000)
            duration_ms = actual_ms
            del self._tool_start_times[tool_id]
        
        duration_str = self._format_duration(duration_ms)
        
        if is_error:
            status_icon = self._symbol(Symbol.CROSS, "X")
            status_text = "FAILED"
            status_color = Color.BRIGHT_RED
        else:
            status_icon = self._symbol(Symbol.CHECK, "V")
            status_text = "OK"
            status_color = Color.BRIGHT_GREEN
        
        self._stop_spinner(
            f"{tool_name} {status_icon} {status_text} "
            f"{self._color(f'({duration_str})', Color.DIM)}",
            success=not is_error
        )
        
        # Show result preview for verbose mode
        if self.verbose and result:
            result_str = str(result)
            if len(result_str) > 100:
                result_str = result_str[:97] + "..."
            result_str = result_str.replace("\n", " ")
            
            output_color = Color.RED if is_error else Color.DIM
            self._write(
                f"      {self._color(Symbol.BOX_L, Color.DIM)}"
                f"{self._color(Symbol.BOX_H, Color.DIM)} "
                f"{self._color(result_str, output_color)}"
            )
        
        self._write("")
    
    def on_thinking(self, thinking_text: str) -> None:
        """Called when the agent is in thinking mode."""
        if not self.show_thinking:
            return
        
        brain = self._symbol(Symbol.BRAIN, "*")
        preview = self._truncate(thinking_text, 70)
        length = len(thinking_text)
        
        self._write(
            f"  {self._color(brain, Color.YELLOW)} "
            f"{self._color('Thinking:', Color.YELLOW, Color.BOLD)} "
            f"{self._color(preview, Color.DIM)} "
            f"{self._color(f'({length} chars)', Color.BRIGHT_BLACK)}"
        )
    
    def on_message(self, text: str, is_partial: bool = False) -> None:
        """Called when the agent generates a message."""
        if not text.strip():
            return
        
        preview = self._truncate(text, self.max_preview_length)
        length = len(text)
        
        pointer = self._symbol(Symbol.POINTER, ">")
        
        if is_partial:
            # Partial message - update in place
            self._clear_line()
            self._write(
                f"  {self._color(pointer, Color.GREEN)} "
                f"{self._color(preview, Color.WHITE)} "
                f"{self._color(f'[{length}]', Color.DIM)}",
                end=""
            )
        else:
            # Complete message
            self._write(
                f"  {self._color(pointer, Color.BRIGHT_GREEN)} "
                f"{self._color(preview, Color.WHITE)} "
                f"{self._color(f'({length} chars)', Color.DIM)}"
            )
    
    def on_error(self, error_message: str, error_type: str = "error") -> None:
        """Called when an error occurs."""
        self._stop_spinner()
        
        error_icon = self._symbol(Symbol.CROSS, "X")
        warn_icon = self._symbol(Symbol.WARN, "!")
        
        icon = error_icon if error_type == "error" else warn_icon
        color = Color.BRIGHT_RED if error_type == "error" else Color.BRIGHT_YELLOW
        
        self._write("")
        self._write(
            f"  {self._color(icon, color)} "
            f"{self._color(error_type.upper() + ':', color, Color.BOLD)} "
            f"{self._color(error_message, color)}"
        )
        self._write("")
    
    def on_agent_complete(
        self,
        status: str,
        num_turns: int,
        duration_ms: int,
        total_cost_usd: Optional[float],
        result: Optional[str],
        session_id: Optional[str] = None,
        usage: Optional[dict[str, Any]] = None,
        model: Optional[str] = None,
        cumulative_cost_usd: Optional[float] = None,
        cumulative_turns: Optional[int] = None,
        cumulative_tokens: Optional[int] = None
    ) -> None:
        """Called when the agent completes execution."""
        self._stop_spinner()
        
        status_upper = status.upper()
        is_complete = status_upper in ("COMPLETE", "OK", "COMPLETED")
        is_partial = status_upper == "PARTIAL"
        
        self._write("")
        
        if is_complete:
            header_color = Color.BRIGHT_GREEN
            status_icon = self._symbol(Symbol.CHECK, "OK")
            header_text = "COMPLETE"
        elif is_partial:
            header_color = Color.BRIGHT_YELLOW
            status_icon = self._symbol(Symbol.WARN, "!")
            header_text = "PARTIAL"
        else:
            header_color = Color.BRIGHT_RED
            status_icon = self._symbol(Symbol.CROSS, "X")
            header_text = "FAILED"
        
        width = self._console_width - 2  # Use full console width with small margin
        inner_width = width - 2
        
        # Build content lines
        content_lines: list[str] = []
        
        # Metrics line (current run)
        duration_str = self._format_duration(duration_ms)
        metrics_parts = [f"Duration: {duration_str}", f"Turns: {num_turns}"]
        if total_cost_usd is not None:
            cost_str = self._format_cost(total_cost_usd)
            metrics_parts.append(f"Cost: {cost_str}")
        content_lines.append(" " + " | ".join(metrics_parts))
        
        # Token usage and context load (this run)
        if usage:
            input_tokens = usage.get("input_tokens", 0)
            cache_creation = usage.get("cache_creation_input_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            total_input = input_tokens + cache_creation + cache_read
            total_tokens = total_input + output_tokens
            
            # Build token usage line
            token_parts = [f"Tokens: {total_tokens:,}"]
            token_parts.append(f"(in: {total_input:,}, out: {output_tokens:,})")
            
            # Add context load if model is known
            if model:
                from schemas import get_model_context_size
                context_size = get_model_context_size(model)
                context_percent = (total_input / context_size) * 100
                token_parts.append(
                    f"Context: {total_input:,}/{context_size:,} ({context_percent:.1f}%)"
                )
            
            content_lines.append(" " + " | ".join(token_parts))
            
            # Cache info if relevant
            if cache_creation > 0 or cache_read > 0:
                cache_parts = []
                if cache_creation > 0:
                    cache_parts.append(f"cache_write: {cache_creation:,}")
                if cache_read > 0:
                    cache_parts.append(f"cache_read: {cache_read:,}")
                content_lines.append(
                    f" {self._symbol(Symbol.BULLET, '-')} " + " | ".join(cache_parts)
                )
        
        # Cumulative stats (if this is a resumed session)
        has_cumulative = (
            cumulative_cost_usd is not None and
            cumulative_turns is not None and
            (cumulative_turns > num_turns or cumulative_cost_usd > (total_cost_usd or 0))
        )
        if has_cumulative:
            content_lines.append("")  # Empty line separator
            cumul_parts = [
                f"Session Total: {cumulative_turns} turns"
            ]
            if cumulative_cost_usd is not None:
                cumul_parts.append(self._format_cost(cumulative_cost_usd))
            if cumulative_tokens is not None:
                cumul_parts.append(f"{cumulative_tokens:,} tokens")
            content_lines.append(
                f" {self._symbol(Symbol.STAR, '*')} " + " | ".join(cumul_parts)
            )
        
        # Session ID
        if session_id:
            content_lines.append(f" Session: {session_id}")
        
        # Parse and display result output
        if result:
            # Try to parse as YAML first, then JSON
            parsed = None
            try:
                import yaml
                parsed = yaml.safe_load(result)
            except Exception:
                try:
                    import json
                    parsed = json.loads(result)
                except Exception:
                    pass
            
            if isinstance(parsed, dict):
                content_lines.append("")  # Empty line separator
                
                # Show error if present
                if parsed.get("error"):
                    error_text = str(parsed.get("error"))
                    content_lines.append(f" Error: {error_text[:inner_width - 10]}")
                
                # Show comments if present
                if parsed.get("comments"):
                    comments_text = str(parsed.get("comments"))
                    content_lines.append(f" Comments: {comments_text[:inner_width - 13]}")
                
                # Show output text
                output_text = parsed.get("output") or parsed.get("result")
                if output_text:
                    raw_lines = str(output_text).strip().split("\n")
                    output_lines: list[str] = []
                    for raw_line in raw_lines:
                        raw_line = raw_line.strip()
                        if not raw_line:
                            output_lines.append("")
                        elif len(raw_line) <= inner_width - 3:
                            output_lines.append(raw_line)
                        else:
                            output_lines.extend(
                                self._wrap_text(raw_line, inner_width - 3)
                            )
                    
                    max_lines = 8
                    for i, line in enumerate(output_lines[:max_lines]):
                        prefix = " > " if i == 0 else "   "
                        content_lines.append(prefix + line)
                    
                    if len(output_lines) > max_lines:
                        content_lines.append(
                            f"   ... +{len(output_lines) - max_lines} more lines"
                        )
                
                # Show result_files if present
                result_files = parsed.get("result_files", [])
                if result_files and isinstance(result_files, list):
                    content_lines.append("")
                    content_lines.append(" Files:")
                    max_files = 5
                    for i, filepath in enumerate(result_files[:max_files]):
                        content_lines.append(f"   - {filepath}")
                    if len(result_files) > max_files:
                        content_lines.append(
                            f"   ... +{len(result_files) - max_files} more files"
                        )
            elif result:
                # Fallback: display raw result
                content_lines.append("")
                output_lines = str(result).strip().split("\n")[:5]
                for i, line in enumerate(output_lines):
                    prefix = " > " if i == 0 else "   "
                    content_lines.append(prefix + line[:inner_width - 3])
        
        # Use _print_box for the completion summary - entire box in status color
        title = f"{status_icon} {header_text}"
        self._print_box(
            lines=content_lines,
            title=title,
            color=Color.WHITE,
            title_color=header_color,
            border_color=header_color,
            width=width,
            center_title=True
        )
        self._write("")
    
    # ═══════════════════════════════════════════════════════════════
    # Additional Utility Methods
    # ═══════════════════════════════════════════════════════════════
    
    def on_system_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Handle system events (init, status changes, etc.)."""
        if event_type == "init":
            self.on_agent_start(
                session_id=data.get("session_id", "unknown"),
                model=data.get("model", "unknown"),
                tools=data.get("tools", []),
                working_dir=data.get("cwd", ".")
            )
        else:
            info_icon = self._symbol(Symbol.INFO, "i")
            self._write(
                f"  {self._color(info_icon, Color.BLUE)} "
                f"{self._color(event_type, Color.BLUE)}: "
                f"{self._color(str(data)[:80], Color.DIM)}"
            )
    
    def on_permission_check(
        self,
        tool_name: str,
        decision: str,
        reason: Optional[str] = None
    ) -> None:
        """Called when a permission check is made."""
        if decision == "allow":
            icon = self._symbol(Symbol.CHECK, "V")
            color = Color.GREEN
        elif decision == "deny":
            icon = self._symbol(Symbol.CROSS, "X")
            color = Color.RED
        else:
            icon = self._symbol(Symbol.WARN, "?")
            color = Color.YELLOW
        
        msg = f"{tool_name} {self._symbol(Symbol.ARROW_RIGHT, '->')} {decision}"
        if reason:
            msg += f" ({reason})"
        
        self._write(
            f"    {self._color(icon, color)} "
            f"{self._color('Permission:', Color.DIM)} "
            f"{self._color(msg, color)}"
        )
    
    def on_profile_switch(
        self,
        profile_type: str,
        profile_name: str,
        tools: list[str],
        allow_rules_count: int = 0,
        deny_rules_count: int = 0,
        profile_path: Optional[str] = None
    ) -> None:
        """
        Display profile switch notification with tools info.
        
        If called before on_agent_start, stores the profile info to be
        included in the agent start header. If called after, prints
        a standalone profile switch notification.
        
        Args:
            profile_type: Type of profile ("system" or "user").
            profile_name: Name of the profile.
            tools: List of available tools in this profile.
            allow_rules_count: Number of allow rules in the profile.
            deny_rules_count: Number of deny rules in the profile.
            profile_path: Path to the loaded profile file.
        """
        profile_info = {
            "profile_type": profile_type,
            "profile_name": profile_name,
            "tools": tools,
            "allow_rules_count": allow_rules_count,
            "deny_rules_count": deny_rules_count,
            "profile_path": profile_path,
        }
        
        # If agent hasn't started yet, store profile info for header
        if not self._agent_started:
            self._pending_profile = profile_info
            return
        
        # Agent has started, print standalone profile switch notification
        self._print_profile_switch(profile_info)
    
    def _print_profile_switch(self, profile_info: dict[str, Any]) -> None:
        """
        Print a standalone profile switch notification.
        
        Args:
            profile_info: Dictionary with profile details.
        """
        profile_type = profile_info["profile_type"]
        profile_name = profile_info["profile_name"]
        tools = profile_info["tools"]
        allow_rules_count = profile_info["allow_rules_count"]
        deny_rules_count = profile_info["deny_rules_count"]
        profile_path = profile_info.get("profile_path")
        
        bar = self._symbol(Symbol.BOX_V, "|")
        
        # Choose color based on profile type
        if profile_type.lower() == "system":
            profile_color = Color.BRIGHT_MAGENTA
            icon = self._symbol(Symbol.GEAR, "*")
        else:
            profile_color = Color.BRIGHT_GREEN
            icon = self._symbol(Symbol.STAR, "*")
        
        # Print separator before profile switch
        self._write("")
        
        # Profile header
        self._write(
            f"  {self._color(bar, Color.DIM)} "
            f"{self._color(icon, profile_color)} "
            f"{self._color('PROFILE:', Color.DIM)} "
            f"{self._color(profile_type.upper(), profile_color, Color.BOLD)} "
            f"{self._color(f'({profile_name})', Color.DIM)}"
        )
        
        # Profile path
        if profile_path:
            # Shorten the path for display
            path_display = profile_path
            if len(path_display) > 50:
                path_display = "..." + path_display[-47:]
            self._write(
                f"  {self._color(bar, Color.DIM)} "
                f"{self._color(self._symbol(Symbol.BULLET, '-'), Color.DIM)} "
                f"{self._color('Loaded:', Color.DIM)} "
                f"{self._color(path_display, Color.DIM)}"
            )
        
        # Rules count
        rules_info = f"allow={allow_rules_count}, deny={deny_rules_count}"
        self._write(
            f"  {self._color(bar, Color.DIM)} "
            f"{self._color(self._symbol(Symbol.BULLET, '-'), Color.DIM)} "
            f"{self._color('Rules:', Color.DIM)} "
            f"{self._color(rules_info, Color.DIM)}"
        )
        
        # Tools section
        self._write(
            f"  {self._color(bar, Color.DIM)} "
            f"{self._color(self._symbol(Symbol.TOOL, '*'), Color.BRIGHT_WHITE)} "
            f"{self._color('Tools:', Color.DIM)} "
            f"{self._color(str(len(tools)), Color.BRIGHT_WHITE)} "
            f"{self._color('available', Color.DIM)}"
        )
        
        # Display tools in grid
        tool_lines = self._format_tools_grid(tools, columns=4, col_width=18)
        for line in tool_lines:
            self._write(
                f"  {self._color(bar, Color.DIM)}   "
                f"{self._color(line, Color.DIM)}"
            )
        
        # Bottom separator
        bl = self._symbol(Symbol.BOX_BL, "+")
        h = self._symbol(Symbol.BOX_H, "-")
        self._write(self._color(f"  {bl}{h * 57}", Color.DIM))
        self._write("")
    
    def print_separator(self, char: str = "─", width: int = 60) -> None:
        """Print a separator line."""
        sep_char = self._symbol(char, "-")
        self._write(self._color(f"  {sep_char * width}", Color.DIM))
    
    def print_status(self, message: str, status: str = "info") -> None:
        """Print a status message."""
        icons = {
            "info": (Symbol.INFO, Color.BLUE),
            "success": (Symbol.CHECK, Color.GREEN),
            "warning": (Symbol.WARN, Color.YELLOW),
            "error": (Symbol.CROSS, Color.RED),
        }
        
        icon, color = icons.get(status, icons["info"])
        self._write(
            f"  {self._color(self._symbol(icon, '*'), color)} "
            f"{self._color(message, color)}"
        )


class QuietTracer(TracerBase):
    """
    Minimal tracer that only logs errors and completion.
    
    Use this when you want minimal console output.
    """
    
    def on_agent_start(
        self,
        session_id: str,
        model: str,
        tools: list[str],
        working_dir: str,
        skills: Optional[list[str]] = None,
        task: Optional[str] = None
    ) -> None:
        """Silent start."""
        pass
    
    def on_tool_start(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_id: str
    ) -> None:
        """Silent tool start."""
        pass
    
    def on_tool_complete(
        self,
        tool_name: str,
        tool_id: str,
        result: Any,
        duration_ms: int,
        is_error: bool
    ) -> None:
        """Only report errors."""
        if is_error:
            print(f"[ERROR] {tool_name}: {result}")
    
    def on_thinking(self, thinking_text: str) -> None:
        """Silent thinking."""
        pass
    
    def on_message(self, text: str, is_partial: bool = False) -> None:
        """Silent message."""
        pass
    
    def on_error(self, error_message: str, error_type: str = "error") -> None:
        """Report errors."""
        print(f"[{error_type.upper()}] {error_message}")
    
    def on_agent_complete(
        self,
        status: str,
        num_turns: int,
        duration_ms: int,
        total_cost_usd: Optional[float],
        result: Optional[str],
        session_id: Optional[str] = None,
        usage: Optional[dict[str, Any]] = None,
        model: Optional[str] = None,
        cumulative_cost_usd: Optional[float] = None,
        cumulative_turns: Optional[int] = None,
        cumulative_tokens: Optional[int] = None
    ) -> None:
        """Report completion with token usage."""
        cost_str = f" (${total_cost_usd:.4f})" if total_cost_usd else ""
        tokens_str = ""
        if usage:
            total_tokens = (
                usage.get("input_tokens", 0) +
                usage.get("cache_creation_input_tokens", 0) +
                usage.get("cache_read_input_tokens", 0) +
                usage.get("output_tokens", 0)
            )
            tokens_str = f", {total_tokens:,} tokens"
        print(f"[{status}] Completed in {duration_ms}ms, {num_turns} turns{cost_str}{tokens_str}")
        
        # Show cumulative stats if this was a resumed session
        if cumulative_turns and cumulative_turns > num_turns:
            cumul_cost = f" ${cumulative_cost_usd:.4f}" if cumulative_cost_usd else ""
            cumul_tokens = f", {cumulative_tokens:,} tokens" if cumulative_tokens else ""
            print(f"[SESSION TOTAL] {cumulative_turns} turns{cumul_cost}{cumul_tokens}")

    def on_profile_switch(
        self,
        profile_type: str,
        profile_name: str,
        tools: list[str],
        allow_rules_count: int = 0,
        deny_rules_count: int = 0,
        profile_path: Optional[str] = None
    ) -> None:
        """Report profile switch."""
        path_str = f" from {profile_path}" if profile_path else ""
        print(f"[PROFILE] {profile_type.upper()}: {profile_name} ({len(tools)} tools){path_str}")


class NullTracer(TracerBase):
    """
    No-op tracer that does nothing.
    
    Use this when you want to completely disable tracing.
    """
    
    def on_agent_start(
        self,
        session_id: str,
        model: str,
        tools: list[str],
        working_dir: str,
        skills: Optional[list[str]] = None,
        task: Optional[str] = None
    ) -> None:
        pass
    
    def on_tool_start(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_id: str
    ) -> None:
        pass
    
    def on_tool_complete(
        self,
        tool_name: str,
        tool_id: str,
        result: Any,
        duration_ms: int,
        is_error: bool
    ) -> None:
        pass
    
    def on_thinking(self, thinking_text: str) -> None:
        pass
    
    def on_message(self, text: str, is_partial: bool = False) -> None:
        pass
    
    def on_error(self, error_message: str, error_type: str = "error") -> None:
        pass
    
    def on_agent_complete(
        self,
        status: str,
        num_turns: int,
        duration_ms: int,
        total_cost_usd: Optional[float],
        result: Optional[str],
        session_id: Optional[str] = None,
        usage: Optional[dict[str, Any]] = None,
        model: Optional[str] = None,
        cumulative_cost_usd: Optional[float] = None,
        cumulative_turns: Optional[int] = None,
        cumulative_tokens: Optional[int] = None
    ) -> None:
        pass

    def on_profile_switch(
        self,
        profile_type: str,
        profile_name: str,
        tools: list[str],
        allow_rules_count: int = 0,
        deny_rules_count: int = 0,
        profile_path: Optional[str] = None
    ) -> None:
        pass
