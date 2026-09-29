import sys
import subprocess
import webbrowser
import re
import os
import shlex
from pathlib import Path
from rapidfuzz import process, fuzz

try:
    import policy
    import verifier
    import idempotency
    from errors import ToolError
    from db import ActionReceipt, utcnow_naive
except ImportError:
    from . import policy
    from . import verifier
    from . import idempotency
    from .errors import ToolError
    from .db import ActionReceipt, utcnow_naive

# Hardcoded fallback aliases for well-known utilities
APP_ALIASES_FALLBACK = {
    "calculator": {
        "win32": "calc.exe",
        "darwin": "open -a Calculator",
        "linux": "gnome-calculator"
    },
    "notepad": {
        "win32": "notepad.exe",
        "darwin": "open -a TextEdit",
        "linux": "gedit"
    },
    "terminal": {
        "win32": "cmd.exe",
        "darwin": "open -a Terminal",
        "linux": "gnome-terminal"
    },
    "explorer": {
        "win32": "explorer.exe",
        "darwin": "open .",
        "linux": "xdg-open ."
    },
    "settings": {
        "win32": "start ms-settings:",
        "darwin": "open x-apple.systempreferences:",
        "linux": "gnome-control-center"
    },
    "word": {
        "win32": "start winword",
        "darwin": "open -a 'Microsoft Word'",
        "linux": "libreoffice --writer"
    },
    "excel": {
        "win32": "start excel",
        "darwin": "open -a 'Microsoft Excel'",
        "linux": "libreoffice --calc"
    },
    "powerpoint": {
        "win32": "start powerpnt",
        "darwin": "open -a 'Microsoft PowerPoint'",
        "linux": "libreoffice --impress"
    },
    "camera": {
        "win32": "start microsoft.windows.camera:",
        "darwin": "open -a 'Photo Booth'",
        "linux": "cheese"
    },
    "paint": {
        "win32": "mspaint",
        "darwin": "open -a Preview",
        "linux": "gimp"
    },
    "task manager": {
        "win32": "taskmgr",
        "darwin": "open -a 'Activity Monitor'",
        "linux": "gnome-system-monitor"
    },
    "photos": {
        "win32": "start ms-photos:",
        "darwin": "open -a Photos",
        "linux": "eog"
    },
    "mail": {
        "win32": "start outlookmail:",
        "darwin": "open -a Mail",
        "linux": "thunderbird"
    },
    "vscode": {
        "win32": "code",
        "darwin": "open -a 'Visual Studio Code'",
        "linux": "code"
    },
    "chrome": {
        "win32": "start chrome",
        "darwin": "open -a 'Google Chrome'",
        "linux": "google-chrome"
    },
    "spotify": {
        "win32": "start spotify",
        "darwin": "open -a Spotify",
        "linux": "spotify"
    },
}

SITE_ALIASES = {
    # Essentials
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "google calendar": "https://calendar.google.com",
    "calendar": "https://calendar.google.com",
    "reddit": "https://www.reddit.com",
    "twitter": "https://twitter.com",
    "instagram": "https://www.instagram.com",
    "linkedin": "https://www.linkedin.com",
    "whatsapp": "https://web.whatsapp.com",
    # AI Tools
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "gemini": "https://gemini.google.com",
    "open router": "https://openrouter.ai",
    "openrouter": "https://openrouter.ai",
    "hugging face": "https://huggingface.co",
    "huggingface": "https://huggingface.co",
    "perplexity": "https://www.perplexity.ai",
    # Computer Science & Development
    "github": "https://github.com",
    "gitlab": "https://gitlab.com",
    "stack overflow": "https://stackoverflow.com",
    "stackoverflow": "https://stackoverflow.com",
    "leetcode": "https://leetcode.com",
    "hacker rank": "https://www.hackerrank.com",
    "hackerrank": "https://www.hackerrank.com",
    "geeks for geeks": "https://www.geeksforgeeks.org",
    "geeksforgeeks": "https://www.geeksforgeeks.org",
    "free code camp": "https://www.freecodecamp.org",
    "freecodecamp": "https://www.freecodecamp.org",
    "mdn": "https://developer.mozilla.org",
    "w3schools": "https://www.w3schools.com",
    "aws": "https://console.aws.amazon.com",
    "vercel": "https://vercel.com",
    "netlify": "https://www.netlify.com",
    "firebase": "https://console.firebase.google.com",
    "mongodb": "https://www.mongodb.com",
    "figma": "https://www.figma.com",
    "coursera": "https://www.coursera.org",
    "edx": "https://www.edx.org",
    "mit open courseware": "https://ocw.mit.edu",
    "exercism": "https://exercism.org"
}

# In-memory dynamic catalog: { normalized_name : launch_command }
DYNAMIC_APP_CATALOG = {}
_CATALOG_READY = False

# Dangerous shell metacharacters — reject if LLM tries to inject
_SHELL_INJECTION_RE = re.compile(r'[;&|`$(){}<>\\"\']')


def _discover_apps():
    """Scan OS for installed applications and build the dynamic catalog."""
    global DYNAMIC_APP_CATALOG, _CATALOG_READY
    if _CATALOG_READY:
        return
    DYNAMIC_APP_CATALOG.clear()

    print("[SYSTEM ACTIONS] Starting OS-level app discovery...")

    if sys.platform == "win32":
        paths = [
            Path(os.environ.get('ProgramData', 'C:/ProgramData')) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs',
            Path(os.environ.get('APPDATA', '')) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs'
        ]
        for p in paths:
            if p.exists():
                try:
                    for lnk in p.rglob('*.lnk'):
                        if 'uninstall' not in lnk.name.lower():
                            DYNAMIC_APP_CATALOG[lnk.stem.lower()] = str(lnk)
                except Exception:
                    continue

        # Also scan Desktop for shortcuts
        for env_key in ('USERPROFILE', 'PUBLIC'):
            base = os.environ.get(env_key)
            if base:
                dp = Path(base) / 'Desktop'
                if dp.exists():
                    try:
                        for lnk in dp.glob('*.lnk'):
                            DYNAMIC_APP_CATALOG[lnk.stem.lower()] = str(lnk)
                    except Exception:
                        pass

    elif sys.platform == "darwin":
        paths = [Path('/Applications'), Path('~/Applications').expanduser()]
        for p in paths:
            if p.exists():
                try:
                    for app in p.glob('*.app'):
                        DYNAMIC_APP_CATALOG[app.stem.lower()] = f'open -a "{app.name}"'
                except Exception:
                    continue

    elif sys.platform == "linux":
        paths = [Path('/usr/share/applications'), Path('~/.local/share/applications').expanduser()]
        for p in paths:
            if p.exists():
                try:
                    for desktop_file in p.rglob('*.desktop'):
                        try:
                            with open(desktop_file, 'r', encoding='utf-8', errors='ignore') as f:
                                name = None
                                exec_cmd = None
                                for line in f:
                                    if line.startswith('Name=') and not name:
                                        name = line.strip().split('=', 1)[1].lower()
                                    if line.startswith('Exec=') and not exec_cmd:
                                        exec_cmd = line.strip().split('=', 1)[1]
                                        exec_cmd = re.sub(r'\%[a-zA-Z]', '', exec_cmd).strip()
                                if name and exec_cmd:
                                    # Only keep the binary, strip args with placeholders for safety
                                    DYNAMIC_APP_CATALOG[name] = exec_cmd
                        except Exception:
                            continue
                except Exception:
                    continue

    # Merge hardcoded fallbacks for the current OS
    for name, cmds in APP_ALIASES_FALLBACK.items():
        if sys.platform in cmds and name not in DYNAMIC_APP_CATALOG:
            DYNAMIC_APP_CATALOG[name] = cmds[sys.platform]

    _CATALOG_READY = True
    print(f"[SYSTEM ACTIONS] App discovery complete. Catalog size: {len(DYNAMIC_APP_CATALOG)}")


# Lazy discovery — don't block import; discover on first use in background
def _ensure_catalog():
    if not _CATALOG_READY:
        _discover_apps()


def _normalize_name(name: str) -> str:
    """Lowercase and strip filler words/phrases."""
    if not name or not isinstance(name, str):
        return ""
    # Limit input length
    name = name[:200]
    name_clean = name.lower().strip()
    # Remove filler phrases first (longer phrases before single words)
    filler_phrases = ["pull up", "show me", "get me", "for me", "please", "my", "the", "a", "an", "open", "launch", "start"]
    for phrase in filler_phrases:
        # Escape regex special chars in phrase
        pattern = r'\b' + re.escape(phrase) + r'\b'
        name_clean = re.sub(pattern, ' ', name_clean)
    # Collapse whitespace
    name_clean = re.sub(r'\s+', ' ', name_clean).strip()
    return name_clean


_app_launcher = None


def _launch_app_command(command):
    """Launch a resolved catalog entry without a command shell."""
    if sys.platform == "win32" and os.path.isfile(command):
        os.startfile(command)  # type: ignore[attr-defined]
        return True  # startfile has no return value
    args = shlex.split(command, posix=sys.platform != "win32")
    if not args or args[0].casefold() == "start":
        raise ValueError("Legacy shell launch command is unsupported.")
    subprocess.Popen(args, shell=False)
    return True


def get_app_launcher():
    return _app_launcher or _launch_app_command


def set_app_launcher(fn):
    global _app_launcher
    _app_launcher = fn


def reset_app_launcher():
    global _app_launcher
    _app_launcher = None


def _resolve_app(name):
    """Return a registered name and command, rejecting ambiguous matches."""
    _ensure_catalog()
    clean = _normalize_name(name)
    if not clean or len(clean) < 2:
        return None, None, "Application name is too short."
    if clean in DYNAMIC_APP_CATALOG:
        key = clean
    else:
        matches = process.extract(clean, list(DYNAMIC_APP_CATALOG), scorer=fuzz.WRatio, limit=2)
        if not matches or matches[0][1] < 85:
            return None, None, f"I couldn't find an application matching '{name}'."
        if len(matches) > 1 and matches[0][1] - matches[1][1] < 10:
            return None, None, "Several applications match that name. Please use the exact name."
        key = matches[0][0]
    if not policy.evaluate_app_policy(key).allowed:
        return None, None, "Registered application is a forbidden interpreter."
    command = DYNAMIC_APP_CATALOG[key]
    if not isinstance(command, str) or not command.strip():
        return None, None, "Registered application has no launch target."
    if not policy.evaluate_app_policy(os.path.basename(command)).allowed:
        return None, None, "Registered launch target is a forbidden interpreter."
    if sys.platform == "win32" and os.path.isfile(command):
        return key, command, None
    if _SHELL_INJECTION_RE.search(command):
        return None, None, "Registered application requires an unsupported shell command."
    try:
        args = shlex.split(command, posix=sys.platform != "win32")
    except ValueError:
        return None, None, "Registered application has an invalid launch command."
    executable = os.path.basename(args[0]).casefold() if args else ""
    if executable in ("start", "cmd", "cmd.exe", "powershell", "powershell.exe",
                      "bash", "sh", "python", "python.exe", "wscript", "wscript.exe",
                      "cscript", "cscript.exe"):
        return None, None, "Registered application requires a shell or interpreter. Open it manually."
    return key, command, None


def execute_open_app(name, db=None, clock=None, source="system", idempotency_key=None,
                     command_text=None, write_receipt=True, app_launcher=None):
    """Policy -> target -> launcher -> verifier -> receipt -> response."""
    now_utc = clock.now_utc() if clock else utcnow_naive()
    target_key = idempotency.launch_target_key(name)

    if idempotency_key and db:
        existing = db.query(ActionReceipt).filter(ActionReceipt.idempotency_key == idempotency_key).first()
        if existing:
            if idempotency.receipt_conflict(existing, "open_app", target_key):
                raise ToolError("Idempotency key already belongs to another action or target.")
            rec = existing.to_dict()
            rec["replayed"] = True
            return {"success": bool(existing.success), "opened": bool(existing.success),
                    "verified": bool(existing.success), "response": existing.message,
                    "message": existing.message, "receipt": rec}

    def outcome(success, message, verification=None, decision=None):
        receipt_dict = None
        if write_receipt and db:
            receipt = ActionReceipt(
                idempotency_key=idempotency_key, action="open_app", target_key=target_key,
                success=success, entity_type="app", message=message,
                command_text=(command_text or name or "")[:600], source=source,
                created_utc=now_utc)
            db.add(receipt)
            db.commit()
            receipt_dict = receipt.to_dict()
        return {"success": success, "opened": success, "verified": success,
                "response": message, "message": message, "receipt": receipt_dict,
                "verification": verification.to_dict() if verification else None,
                "policy_decision": decision.to_dict() if decision else None}

    decision = policy.evaluate_policy("open_app", {"name": name})
    if not decision.allowed:
        return outcome(False, decision.reason, decision=decision)
    key, command, error = _resolve_app(name)
    if error:
        return outcome(False, error, decision=decision)
    try:
        launch_result = (app_launcher or get_app_launcher())(command)
        verification = verifier.verify_app_launch(key, launch_result)
    except Exception as exc:
        verification = verifier.verify_app_launch(key, None, exc)
    if verification.verified_initiation:
        return outcome(True, f"Opening {key.title()}...", verification, decision)
    return outcome(False, verification.message, verification, decision)


def resolve_website_url(site_or_url: str):
    """Resolve a site name, alias, or direct URL.
    Returns (matched_url, display_name) or (None, None)."""
    if not isinstance(site_or_url, str) or not site_or_url.strip():
        return None, None

    site_clean = _normalize_name(site_or_url)
    if not site_clean or len(site_clean) < 2:
        return None, None

    # Check explicit alias match first
    choices = list(SITE_ALIASES.keys())
    match = process.extractOne(site_clean, choices, scorer=fuzz.WRatio)
    if match and match[1] >= 75:
        matched_key = match[0]
        return SITE_ALIASES[matched_key], matched_key.title()

    # Check direct URL
    if "." in site_clean and " " not in site_clean:
        if re.match(r'^[a-z0-9.-]+\.[a-z]{2,}(/.*)?$', site_clean):
            url = site_clean if site_clean.startswith("http") else f"https://{site_clean}"
            return url, url

    # Fallback: check if original was already a full valid URL
    orig = site_or_url.strip()
    if orig.lower().startswith(("http://", "https://")):
        return orig, orig

    return None, None


_browser_launcher = None


def get_browser_launcher():
    """Return the active browser launcher callable (webbrowser.open_new_tab by default)."""
    return _browser_launcher or webbrowser.open_new_tab


def set_browser_launcher(fn):
    """Set custom browser launcher callable (for tests or desktop automation)."""
    global _browser_launcher
    _browser_launcher = fn


def reset_browser_launcher():
    """Reset custom browser launcher callable to default."""
    global _browser_launcher
    _browser_launcher = None


def execute_open_url(site_or_url: str, db=None, clock=None, source="system",
                     idempotency_key=None, command_text=None,
                     browser_launcher=None, write_receipt=True) -> dict:
    """Execute open_url / open_website through:
    registry/alias resolution -> policy decision -> executor -> verifier -> audit -> response.

    Guarantees:
    - Never asserts unverified success: success=True only if the launcher confirms launch.
    - Persists an ActionReceipt in db for success, denial, and failure (when write_receipt=True).
    - Respects idempotency_key: returns stored receipt on replay without re-executing.
    """
    now_utc = clock.now_utc() if clock else utcnow_naive()
    target_key = idempotency.launch_target_key(site_or_url)

    # 1. Idempotency check
    if idempotency_key and db:
        existing = db.query(ActionReceipt).filter(ActionReceipt.idempotency_key == idempotency_key).first()
        if existing:
            if idempotency.receipt_conflict(existing, "open_website", target_key):
                raise ToolError("Idempotency key already belongs to another action or target.")
            rec = existing.to_dict()
            rec["replayed"] = True
            return {
                "success": bool(existing.success),
                "response": existing.message,
                "message": existing.message,
                "receipt": rec,
                "opened": bool(existing.success),
                "verified": bool(existing.success),
                "policy_decision": {"allowed": True, "reason": "Idempotent replay"},
            }

    # 2. Policy evaluation (Risk 1 check)
    pol = policy.evaluate_policy("open_website", {"site_or_url": site_or_url})
    if not pol.allowed:
        receipt_dict = None
        if write_receipt and db:
            receipt = ActionReceipt(
                idempotency_key=idempotency_key,
                action="open_website", target_key=target_key,
                success=False,
                entity_type="url",
                message=pol.reason,
                command_text=(command_text or site_or_url)[:600] if (command_text or site_or_url) else None,
                source=source,
                created_utc=now_utc,
            )
            db.add(receipt)
            db.commit()
            receipt_dict = receipt.to_dict()
        return {
            "success": False,
            "response": pol.reason,
            "message": pol.reason,
            "receipt": receipt_dict,
            "opened": False,
            "verified": False,
            "policy_decision": pol.to_dict(),
        }

    # 3. Target resolution
    matched_url, display_name = resolve_website_url(site_or_url)
    if not matched_url:
        fail_msg = f"I couldn't find a website matching '{site_or_url}', and it doesn't look like a valid URL."
        receipt_dict = None
        if write_receipt and db:
            receipt = ActionReceipt(
                idempotency_key=idempotency_key,
                action="open_website", target_key=target_key,
                success=False,
                entity_type="url",
                message=fail_msg,
                command_text=(command_text or site_or_url)[:600] if (command_text or site_or_url) else None,
                source=source,
                created_utc=now_utc,
            )
            db.add(receipt)
            db.commit()
            receipt_dict = receipt.to_dict()
        return {
            "success": False,
            "response": fail_msg,
            "message": fail_msg,
            "receipt": receipt_dict,
            "opened": False,
            "verified": False,
            "policy_decision": pol.to_dict(),
        }

    # Final protocol check on resolved URL
    if not (matched_url.startswith("https://") or matched_url.startswith("http://")):
        invalid_msg = "Invalid URL format."
        receipt_dict = None
        if write_receipt and db:
            receipt = ActionReceipt(
                idempotency_key=idempotency_key,
                action="open_website", target_key=target_key,
                success=False,
                entity_type="url",
                message=invalid_msg,
                command_text=(command_text or site_or_url)[:600] if (command_text or site_or_url) else None,
                source=source,
                created_utc=now_utc,
            )
            db.add(receipt)
            db.commit()
            receipt_dict = receipt.to_dict()
        return {
            "success": False,
            "response": invalid_msg,
            "message": invalid_msg,
            "receipt": receipt_dict,
            "opened": False,
            "verified": False,
            "policy_decision": pol.to_dict(),
        }

    # 4. Executor
    launcher = browser_launcher or get_browser_launcher()
    launch_res = None
    launch_err = None
    try:
        launch_res = launcher(matched_url)
    except Exception as e:
        launch_err = e

    # 5. Verifier
    ver = verifier.verify_url_launch(matched_url, launch_res, error=launch_err)

    # 6. Audit & Response
    if ver.verified:
        msg = f"Opening {display_name}..."
        receipt_dict = None
        if write_receipt and db:
            receipt = ActionReceipt(
                idempotency_key=idempotency_key,
                action="open_website", target_key=target_key,
                success=True,
                entity_type="url",
                message=msg,
                command_text=(command_text or site_or_url)[:600] if (command_text or site_or_url) else None,
                source=source,
                created_utc=now_utc,
            )
            db.add(receipt)
            db.commit()
            receipt_dict = receipt.to_dict()
        return {
            "success": True,
            "response": msg,
            "message": msg,
            "receipt": receipt_dict,
            "opened": True,
            "verified": True,
            "policy_decision": pol.to_dict(),
            "verification": ver.to_dict(),
        }
    else:
        msg = f"Failed to open website: {ver.message}"
        receipt_dict = None
        if write_receipt and db:
            receipt = ActionReceipt(
                idempotency_key=idempotency_key,
                action="open_website", target_key=target_key,
                success=False,
                entity_type="url",
                message=msg,
                command_text=(command_text or site_or_url)[:600] if (command_text or site_or_url) else None,
                source=source,
                created_utc=now_utc,
            )
            db.add(receipt)
            db.commit()
            receipt_dict = receipt.to_dict()
        return {
            "success": False,
            "response": msg,
            "message": msg,
            "receipt": receipt_dict,
            "opened": False,
            "verified": False,
            "policy_decision": pol.to_dict(),
            "verification": ver.to_dict(),
        }


def open_website(site_or_url: str, db=None, clock=None, source="system",
                 idempotency_key=None, command_text=None,
                 browser_launcher=None) -> str:
    """
    Opens a website by name or URL in the default browser.
    Reconciled through policy, executor, verifier, and audit.
    Returns the user-facing response string.
    """
    res = execute_open_url(
        site_or_url, db=db, clock=clock, source=source,
        idempotency_key=idempotency_key, command_text=command_text,
        browser_launcher=browser_launcher, write_receipt=bool(db)
    )
    return res["response"]


def _fast_launch(execute, target, db, clock, source, idempotency_key, message):
    try:
        res = execute(target, db=db, clock=clock, source=source,
                      idempotency_key=idempotency_key, command_text=message)
    except ToolError as exc:
        return True, str(exc), None
    return True, res["response"], res.get("receipt")


def check_fast_path(message: str, db=None, clock=None, source="fast_path",
                    idempotency_key=None):
    """
    Checks if a message matches a fast-path action (open/launch/start/show me/pull up X).
    Returns (True, response, receipt) if handled, (False, None, None) if it should fall through to LLM.
    """
    if not isinstance(message, str) or not message.strip():
        return False, None, None

    # Truncate to prevent ReDoS on very long input
    message = message.strip()[:500]
    print(f"\n[SYSTEM ACTIONS] --- Fast path attempt for: '{message}' ---")

    pattern = r"^(?:can you please\s+|can you\s+|could you\s+|please\s+|hey jarvis\s+|jarvis\s+|i want to\s+|i need to\s+|let's\s+)?(?:open|launch|start|pull\s+up|show\s+me|get\s+me)\s+(.+)$"
    match = re.match(pattern, message, re.IGNORECASE)
    if not match:
        print(f"[SYSTEM ACTIONS] Message did not match fast-path regex.")
        return False, None, None

    target = match.group(1).strip()
    # Strip trailing punctuation that users often add
    target = re.sub(r'[.!?]+$', '', target).strip()
    target_clean = _normalize_name(target)
    print(f"[SYSTEM ACTIONS] Fast path regex matched. Target extracted: '{target}', normalized: '{target_clean}'")

    if not target_clean or len(target_clean) < 2:
        print(f"[SYSTEM ACTIONS] Normalized target '{target_clean}' is too short/empty. Falling back to LLM.")
        return False, None, None

    if _SHELL_INJECTION_RE.search(target_clean) and "." not in target_clean:
        print(f"[SYSTEM ACTIONS] Target contains injection chars, falling back to LLM.")
        return False, None, None

    _ensure_catalog()

    # Check if target explicitly has a URI scheme (e.g. file://, javascript:, http://, https://)
    target_raw = target.strip()
    if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', target_raw) or target_raw.lower().startswith(("javascript:", "data:", "about:")):
        print(f"[SYSTEM ACTIONS] Fast path routing URI scheme to execute_open_url for '{target_raw}'")
        return _fast_launch(execute_open_url, target_raw, db, clock, source, idempotency_key, message)

    # 1. Try website aliases (fuzzy match) — higher threshold to avoid false positives
    site_choices = list(SITE_ALIASES.keys())
    site_match = process.extractOne(target_clean, site_choices, scorer=fuzz.WRatio)
    if site_match and site_match[1] >= 85:
        print(f"[SYSTEM ACTIONS] Fast path routing to open_website for '{site_match[0]}'")
        return _fast_launch(execute_open_url, target_clean, db, clock, source, idempotency_key, message)

    # 2. Try direct URL
    if "." in target_clean and " " not in target_clean:
        if re.match(r'^[a-z0-9.-]+\.[a-z]{2,}(/.*)?$', target_clean):
            print(f"[SYSTEM ACTIONS] Fast path routing to open_website for direct URL '{target_clean}'")
            return _fast_launch(execute_open_url, target_clean, db, clock, source, idempotency_key, message)

    # 3. Try app catalog (fuzzy match)
    app_choices = list(DYNAMIC_APP_CATALOG.keys())
    if app_choices:
        app_match = process.extractOne(target_clean, app_choices, scorer=fuzz.WRatio)
        if app_match and app_match[1] >= 85:
            print(f"[SYSTEM ACTIONS] Fast path routing to open_app for '{app_match[0]}'")
            return _fast_launch(execute_open_app, target_raw, db, clock, source, idempotency_key, message)

        print(f"[SYSTEM ACTIONS] Fast path failed to find high-confidence match (Site best: {site_match[1] if site_match else 0:.1f}%, App best: {app_match[1] if app_match else 0:.1f}%). Falling back to LLM.")
    return False, None, None
