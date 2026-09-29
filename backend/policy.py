"""Risk classification and policy engine for VEGA AgentOS (Phase 1).

Risk levels:
  0: Read-only (system info, task/workspace views, status inspection)
  1: Bounded safe action (open allowlisted app/URL, timer/reminder/task lifecycle)
  2: Modification that may need confirmation per policy (file writes, project edits)
  3: Critical action requiring explicit user confirmation (deletion, credentials, shell, messaging)

Every model proposal and command target passes through policy before execution.
Untrusted model/external output is data, never authorization.
"""

import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse

RISK_LEVEL_0_READ_ONLY = 0
RISK_LEVEL_1_BOUNDED_SAFE = 1
RISK_LEVEL_2_MODIFICATION = 2
RISK_LEVEL_3_CRITICAL = 3

# Permitted schemes for web URL navigation
ALLOWED_WEB_SCHEMES = {"http", "https"}

# Forbidden dangerous schemes that must fail closed immediately
FORBIDDEN_SCHEMES = {
    "file", "javascript", "data", "vbscript", "about", "chrome",
    "ms-settings", "shell", "powershell", "cmd", "ws", "wss"
}

# Forbidden shell executables/interpreters
FORBIDDEN_INTERPRETERS = {
    "powershell", "powershell.exe", "cmd", "cmd.exe", "bash", "sh",
    "wscript", "cscript", "python", "python.exe"
}

# Shell metacharacters guard
_SHELL_META_RE = re.compile(r'[;&|`$(){}<>\\"\']')


class PolicyDecision:
    """Outcome of a policy evaluation."""
    def __init__(self, allowed: bool, risk_level: int, reason: str,
                 requires_confirmation: bool = False, metadata: Optional[Dict[str, Any]] = None):
        self.allowed = allowed
        self.risk_level = risk_level
        self.reason = reason
        self.requires_confirmation = requires_confirmation
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "risk_level": self.risk_level,
            "reason": self.reason,
            "requires_confirmation": self.requires_confirmation,
            "metadata": self.metadata,
        }

    def __repr__(self) -> str:
        return (f"PolicyDecision(allowed={self.allowed}, risk_level={self.risk_level}, "
                f"reason='{self.reason}', requires_confirmation={self.requires_confirmation})")


def evaluate_url_policy(target: str) -> PolicyDecision:
    """Strict policy evaluation for URL / website navigation (Risk 1).

    Rules:
    - Target must be non-empty string under 500 characters.
    - Metacharacters rejected outside safe query parameters.
    - If a URI scheme is present, ONLY 'http' and 'https' are allowed.
    - Dangerous schemes ('file:', 'javascript:', 'data:', etc.) are rejected fail-closed.
    - Scheme-less aliases are permitted for subsequent alias resolution.
    """
    if not isinstance(target, str) or not target.strip():
        return PolicyDecision(
            allowed=False,
            risk_level=RISK_LEVEL_1_BOUNDED_SAFE,
            reason="Policy denied: Target URL or website name is empty."
        )

    clean_target = target.strip()
    if len(clean_target) > 500:
        return PolicyDecision(
            allowed=False,
            risk_level=RISK_LEVEL_1_BOUNDED_SAFE,
            reason="Policy denied: Target URL exceeds maximum length (500 characters)."
        )

    # Check for scheme
    parsed = urlparse(clean_target)
    scheme = parsed.scheme.lower() if parsed.scheme else ""

    if scheme:
        if scheme in FORBIDDEN_SCHEMES:
            return PolicyDecision(
                allowed=False,
                risk_level=RISK_LEVEL_1_BOUNDED_SAFE,
                reason=f"Policy denied: Scheme '{scheme}' is forbidden. Only http and https URLs are permitted.",
                metadata={"scheme": scheme, "violation": "forbidden_scheme"}
            )
        if scheme not in ALLOWED_WEB_SCHEMES:
            return PolicyDecision(
                allowed=False,
                risk_level=RISK_LEVEL_1_BOUNDED_SAFE,
                reason=f"Policy denied: Scheme '{scheme}' is not permitted. Only http and https URLs are allowed.",
                metadata={"scheme": scheme, "violation": "unsupported_scheme"}
            )
        # Scheme is http or https — check for injection characters in hostname/path
        if not parsed.netloc:
            return PolicyDecision(
                allowed=False,
                risk_level=RISK_LEVEL_1_BOUNDED_SAFE,
                reason="Policy denied: Invalid URL format; host is missing.",
                metadata={"violation": "missing_host"}
            )
        # Injection check on the URL authority
        if _SHELL_META_RE.search(parsed.netloc):
            return PolicyDecision(
                allowed=False,
                risk_level=RISK_LEVEL_1_BOUNDED_SAFE,
                reason="Policy denied: URL authority contains disallowed characters.",
                metadata={"violation": "shell_meta_in_host"}
            )
        return PolicyDecision(
            allowed=True,
            risk_level=RISK_LEVEL_1_BOUNDED_SAFE,
            reason="Permitted safe web URL navigation.",
            metadata={"scheme": scheme, "netloc": parsed.netloc}
        )

    # No scheme present: clean alias or domain (e.g., 'youtube', 'github.com')
    first_word = clean_target.split()[0].lower() if clean_target.split() else ""
    if first_word in FORBIDDEN_INTERPRETERS:
        return PolicyDecision(
            allowed=False,
            risk_level=RISK_LEVEL_1_BOUNDED_SAFE,
            reason="Policy denied: Shell executable commands are forbidden.",
            metadata={"violation": "forbidden_interpreter"}
        )

    # Reject shell metacharacters
    if _SHELL_META_RE.search(clean_target):
        return PolicyDecision(
            allowed=False,
            risk_level=RISK_LEVEL_1_BOUNDED_SAFE,
            reason="Policy denied: Target contains disallowed shell characters.",
            metadata={"violation": "shell_meta_in_target"}
        )

    return PolicyDecision(
        allowed=True,
        risk_level=RISK_LEVEL_1_BOUNDED_SAFE,
        reason="Permitted website alias or domain resolution.",
        metadata={"target": clean_target}
    )


def evaluate_app_policy(name: str) -> PolicyDecision:
    """Permit a bounded application name, never a command line or interpreter."""
    deny = lambda reason: PolicyDecision(False, RISK_LEVEL_1_BOUNDED_SAFE, reason)
    if not isinstance(name, str) or not name.strip():
        return deny("Policy denied: Application name is empty.")
    clean = name.strip()
    if len(clean) > 200:
        return deny("Policy denied: Application name exceeds 200 characters.")
    if _SHELL_META_RE.search(clean):
        return deny("Policy denied: Application name contains shell characters.")
    if re.search(r"(?i)\b(?:powershell|cmd|bash|python|wscript|cscript|sh)(?:\.exe)?\b", clean):
        return deny("Policy denied: Shell interpreters cannot be launched as apps.")
    return PolicyDecision(True, RISK_LEVEL_1_BOUNDED_SAFE,
                          "Permitted registered application name.")


# Authoritative Risk Classifications for all registered VEGA AgentOS tools
RISK_0_READ_ONLY_TOOLS = {
    "list_tasks",
    "get_ai_radar_digest",
    "list_workspaces",
    "resume_workspace",
    "build_session_draft",
    "get_today",
    "list_coursework",
    "suggest_study",
}

RISK_1_MUTATING_TOOLS = {
    "create_task",
    "set_task_completed",
    "start_timer",
    "cancel_timer",
    "create_reminder",
    "snooze_reminder",
    "start_focus_session",
    "end_focus_session",
    "register_workspace",
    "update_workspace",
    "add_session_note",
    "link_task_to_workspace",
    "add_coursework",
    "complete_coursework",
    "refresh_ai_radar",
}

RISK_1_LAUNCH_TOOLS = {
    "open_website",
    "system.open_url",
    "open_app",
}

# Future risk tiers (currently empty — unknown or high-risk actions fail closed)
RISK_2_MODIFICATION_TOOLS = set()
RISK_3_CRITICAL_TOOLS = set()


def get_tool_risk_level(tool_name: str) -> int:
    """Helper to query the static risk level of any tool."""
    tool = (tool_name or "").strip()
    if tool in RISK_0_READ_ONLY_TOOLS:
        return RISK_LEVEL_0_READ_ONLY
    if tool in RISK_1_MUTATING_TOOLS or tool in RISK_1_LAUNCH_TOOLS:
        return RISK_LEVEL_1_BOUNDED_SAFE
    if tool in RISK_2_MODIFICATION_TOOLS:
        return RISK_LEVEL_2_MODIFICATION
    return RISK_LEVEL_3_CRITICAL


def evaluate_policy(tool_name: str, params: Optional[Dict[str, Any]] = None,
                    context: Optional[Dict[str, Any]] = None) -> PolicyDecision:
    """Central entry point for policy evaluation across all tools.

    Guarantees:
    - Risk 0: Read-only operations allowed without receipt or mutation.
    - Risk 1: Bounded safe actions permitted subject to target validation.
    - Risk 2/3 / Unknown: Fails closed immediately without side effects.
    """
    params = params or {}
    tool = (tool_name or "").strip()

    if tool in ("open_website", "system.open_url"):
        target = params.get("site_or_url") or params.get("url") or ""
        return evaluate_url_policy(target)

    if tool == "open_app":
        return evaluate_app_policy(params.get("name"))

    if tool in RISK_0_READ_ONLY_TOOLS:
        return PolicyDecision(
            allowed=True,
            risk_level=RISK_LEVEL_0_READ_ONLY,
            reason="Permitted read-only operation.",
            requires_confirmation=False,
            metadata={"tool": tool}
        )

    if tool in RISK_1_MUTATING_TOOLS:
        return PolicyDecision(
            allowed=True,
            risk_level=RISK_LEVEL_1_BOUNDED_SAFE,
            reason="Permitted bounded productivity action.",
            requires_confirmation=False,
            metadata={"tool": tool}
        )

    if tool in RISK_2_MODIFICATION_TOOLS:
        return PolicyDecision(
            allowed=False,
            risk_level=RISK_LEVEL_2_MODIFICATION,
            reason=f"Policy denied: Action '{tool}' requires Risk 2 user confirmation.",
            requires_confirmation=True,
            metadata={"tool": tool}
        )

    if tool in RISK_3_CRITICAL_TOOLS:
        return PolicyDecision(
            allowed=False,
            risk_level=RISK_LEVEL_3_CRITICAL,
            reason=f"Policy denied: Action '{tool}' requires explicit Risk 3 user confirmation.",
            requires_confirmation=True,
            metadata={"tool": tool}
        )

    # Unknown or unregistered tool fails closed as Risk 3 Critical
    return PolicyDecision(
        allowed=False,
        risk_level=RISK_LEVEL_3_CRITICAL,
        reason=f"Policy denied: Unknown or unregistered action '{tool}'.",
        requires_confirmation=False,
        metadata={"tool": tool, "violation": "unregistered_tool"}
    )

