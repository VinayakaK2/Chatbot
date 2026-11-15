"""Loop detection subsystem: detects consecutive identical tool calls and oscillating ping-pong cycles."""

import hashlib
import json
import logging
from typing import Any, List, Optional, Tuple

logger = logging.getLogger("agent_runtime.core.loop_detector")


class LoopDetector:
    """Deterministic analyzer identifying repeating tool failure loops and oscillating execution cycles."""

    def __init__(
        self,
        max_identical_consecutive: int = 3,
        max_cycle_repetitions: int = 2,
        history_window: int = 12,
    ) -> None:
        self.max_identical_consecutive = max(2, max_identical_consecutive)
        self.max_cycle_repetitions = max(2, max_cycle_repetitions)
        self.history_window = max(6, history_window)
        # List of tuples: (tool_name, args_hash, error_summary, args_repr)
        self._history: List[Tuple[str, str, Optional[str], str]] = []

    def _hash_arguments(self, arguments: Any) -> Tuple[str, str]:
        """Produce deterministic SHA-256 hash and truncated representation of arguments."""
        try:
            if isinstance(arguments, dict):
                norm_str = json.dumps(arguments, sort_keys=True, default=str)
            elif isinstance(arguments, (list, tuple)):
                norm_str = json.dumps(arguments, default=str)
            else:
                norm_str = str(arguments)
        except Exception:
            norm_str = str(arguments)

        h = hashlib.sha256(norm_str.encode("utf-8")).hexdigest()[:16]
        repr_str = norm_str[:120] + ("..." if len(norm_str) > 120 else "")
        return h, repr_str

    def record_call(
        self,
        tool_name: str,
        arguments: Any,
        error: Optional[str] = None,
    ) -> Optional[str]:
        """Record a tool invocation and immediately evaluate for loop conditions.

        Returns:
            Optional[str]: Diagnostic failure message if a loop is detected, None otherwise.
        """
        args_hash, args_repr = self._hash_arguments(arguments)
        err_summary = error[:100] if error else None
        entry = (tool_name, args_hash, err_summary, args_repr)

        self._history.append(entry)
        if len(self._history) > self.history_window:
            self._history.pop(0)

        return self.detect_loop()

    def detect_loop(self) -> Optional[str]:
        """Inspect recorded history for consecutive identical calls or repeating cycle patterns."""
        if len(self._history) < 2:
            return None

        # 1. Check for N consecutive identical (tool_name, args_hash) calls
        current = self._history[-1]
        consecutive_count = 1
        for i in range(len(self._history) - 2, -1, -1):
            prev = self._history[i]
            if prev[0] == current[0] and prev[1] == current[1]:
                consecutive_count += 1
            else:
                break

        if consecutive_count >= self.max_identical_consecutive:
            tool_name = current[0]
            args_preview = current[3]
            msg = (
                f"Loop detected: tool '{tool_name}' was invoked {consecutive_count} consecutive times "
                f"with identical arguments ({args_preview}). Execution terminated to prevent infinite loop."
            )
            logger.warning(msg)
            return msg

        # 2. Check for oscillating ping-pong cycles of period length 2 or 3
        # e.g., period=2: [A, B, A, B], period=3: [A, B, C, A, B, C]
        for period in (2, 3):
            cycle_len = period * self.max_cycle_repetitions
            if len(self._history) < cycle_len:
                continue

            slice_window = self._history[-cycle_len:]
            # Key tuple for matching: (tool_name, args_hash)
            keys = [(item[0], item[1]) for item in slice_window]
            base_cycle = keys[:period]

            is_repeating = True
            for rep in range(1, self.max_cycle_repetitions):
                sub_cycle = keys[rep * period : (rep + 1) * period]
                if sub_cycle != base_cycle:
                    is_repeating = False
                    break

            if is_repeating:
                cycle_names = [item[0] for item in slice_window[:period]]
                msg = (
                    f"Oscillating loop detected: pattern {' -> '.join(cycle_names)} repeated "
                    f"{self.max_cycle_repetitions} consecutive cycles with identical arguments. "
                    f"Execution terminated to prevent infinite ping-pong."
                )
                logger.warning(msg)
                return msg

        return None

    def reset(self) -> None:
        """Clear recorded history."""
        self._history.clear()
