"""Action verifier for VEGA AgentOS (Phase 1).

Core principle:
Each action follows plan -> execute -> verify -> report.
Only report success after checking the resulting state.
A receipt must NEVER assert unverified success.

Truthful verification boundaries:
- `launch_accepted`: The OS launcher accepted the URL request (e.g. webbrowser.open_new_tab returned True).
  This confirms OS process initiation, but NOT that a page rendered, window exists, or tab is focused.
- `desktop_verified`: Requires real UI/DOM observation (unobserved in Phase 1).
- `failed`: Launcher raised an exception or failed immediately.
- `unverified`: Launcher returned False or None.
"""

from typing import Any, Dict, Optional


class VerificationResult:
    """Outcome of verifying an executed action."""
    def __init__(self, verified_initiation: bool, status: str, evidence: Dict[str, Any],
                 message: str, desktop_verified: bool = False):
        self.verified = verified_initiation  # backward compatibility flag for initiation check
        self.verified_initiation = verified_initiation
        self.status = status  # "launch_accepted" | "failed" | "unverified" | "desktop_verified"
        self.evidence = evidence
        self.message = message
        self.desktop_verified = desktop_verified

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verified": self.verified,
            "verified_initiation": self.verified_initiation,
            "status": self.status,
            "desktop_verified": self.desktop_verified,
            "evidence": self.evidence,
            "message": self.message,
        }

    def __repr__(self) -> str:
        return (f"VerificationResult(status='{self.status}', verified_initiation={self.verified_initiation}, "
                f"desktop_verified={self.desktop_verified}, message='{self.message}')")


def verify_url_launch(target_url: str, launcher_return: Optional[bool],
                      error: Optional[Exception] = None) -> VerificationResult:
    """Verify that a URL or website launch request was accepted by the OS browser launcher.

    Truthful boundaries:
    - If launcher raised an Exception: status is 'failed', verified is False.
    - If launcher returned True: OS browser confirmed request acceptance; status is 'launch_accepted',
      verified_initiation is True, desktop_verified is False (window/render unobserved).
    - If launcher returned False or None: status is 'unverified', verified is False.
      Crucial guarantee: A receipt must NEVER assert unverified success.
    """
    if error is not None:
        return VerificationResult(
            verified_initiation=False,
            status="failed",
            desktop_verified=False,
            evidence={
                "target_url": target_url,
                "launch_accepted": False,
                "desktop_observation": "unobserved",
                "error": str(error),
                "error_type": type(error).__name__,
            },
            message=f"Browser launch failed with error: {error}"
        )

    if launcher_return is True:
        return VerificationResult(
            verified_initiation=True,
            status="launch_accepted",
            desktop_verified=False,
            evidence={
                "target_url": target_url,
                "launch_accepted": True,
                "desktop_observation": "unobserved",
                "limitation": "OS launcher accepted URL; window focus and page DOM render are unobserved without desktop automation."
            },
            message=f"Browser launch accepted for '{target_url}' (window/page render unobserved)."
        )

    return VerificationResult(
        verified_initiation=False,
        status="unverified",
        desktop_verified=False,
        evidence={
            "target_url": target_url,
            "launch_accepted": False,
            "launcher_return": launcher_return,
            "desktop_observation": "unobserved",
        },
        message=f"Browser launch was not accepted by OS launcher (returned {launcher_return})."
    )


def verify_app_launch(target_name: str, launcher_return: Any,
                      error: Optional[Exception] = None) -> VerificationResult:
    """Verify launch initiation only; process/window existence is not observed."""
    if error is not None:
        return VerificationResult(False, "failed",
            {"target": target_name, "desktop_observation": "unobserved",
             "error_type": type(error).__name__},
            f"Application launch failed: {error}")
    accepted = launcher_return is True or (
        hasattr(launcher_return, "pid") and isinstance(launcher_return.pid, int)
        and launcher_return.pid > 0)
    if accepted:
        return VerificationResult(True, "launch_accepted",
            {"target": target_name, "launch_accepted": True,
             "desktop_observation": "unobserved"},
            f"Launch request accepted for {target_name}; window unobserved.")
    return VerificationResult(False, "unverified",
        {"target": target_name, "launch_accepted": False,
         "desktop_observation": "unobserved"},
        f"Application launch was not accepted for {target_name}.")
