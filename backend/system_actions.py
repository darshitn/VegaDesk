import sys
import subprocess
import webbrowser
import re
import os
import shlex
from pathlib import Path
from rapidfuzz import process, fuzz

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


def _is_safe_command(cmd: str) -> bool:
    """Basic injection guard: reject commands with shell metacharacters outside expected patterns."""
    # Allow known safe patterns like `start ...` and `open -a "..."` — check the target part
    # For fallback commands we trust them; for dynamic .lnk paths we check existence instead of shell
    if os.path.exists(cmd):
        return True
    # For shell commands, ensure no unexpected metachars in the app name portion
    # We already normalize, so this catches `calc; rm -rf /` etc.
    if _SHELL_INJECTION_RE.search(cmd):
        # Allow the specific safe wrappers we generate
        if cmd.startswith('start ') or cmd.startswith('open -a '):
            # Extract the quoted app name and check it
            inner = re.sub(r'^(start|open -a)\s+', '', cmd).strip().strip('"').strip("'")
            if _SHELL_INJECTION_RE.search(inner):
                return False
            return True
        return False
    return True


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


def open_app(name: str) -> str:
    """
    Opens a local application by name.

    Args:
        name: The name of the application (e.g., 'calculator', 'notepad')
    """
    _ensure_catalog()
    print(f"[SYSTEM ACTIONS] open_app called with argument: '{name}'")

    if not isinstance(name, str) or not name.strip():
        return "I couldn't figure out which application you want to open."

    name_clean = _normalize_name(name)

    if not name_clean or len(name_clean) < 2:
        print(f"[SYSTEM ACTIONS] App matching failed: normalized name '{name_clean}' is too short/empty.")
        return "I couldn't figure out which application you want to open."

    if _SHELL_INJECTION_RE.search(name_clean):
        return "That application name contains invalid characters."

    print(f"[SYSTEM ACTIONS] Normalized app name: '{name_clean}'")

    # Fuzzy match against dynamic catalog
    choices = list(DYNAMIC_APP_CATALOG.keys())
    if not choices:
        return f"I couldn't find an application matching '{name}' on your system."

    match = process.extractOne(name_clean, choices, scorer=fuzz.WRatio)

    if match and match[1] >= 75:
        matched_key = match[0]
        score = match[1]
        cmd_template = DYNAMIC_APP_CATALOG[matched_key]
        print(f"[SYSTEM ACTIONS] Resolver matched app '{matched_key}' with score {score:.1f}% -> Command: {cmd_template}")

        if not _is_safe_command(cmd_template):
            return f"Matched {matched_key.title()} but the launch command was flagged as unsafe. Please open it manually."

        try:
            if sys.platform == "win32" and os.path.exists(cmd_template):
                os.startfile(cmd_template)  # type: ignore
            elif "start " in cmd_template or "open " in cmd_template:
                # Shell commands from fallback list — safe because we control them
                subprocess.Popen(cmd_template, shell=True)
            else:
                # Standard execution — split safely
                subprocess.Popen(shlex.split(cmd_template), shell=False)

            print(f"[SYSTEM ACTIONS] Successfully launched '{matched_key}'.")
            return f"Opening {matched_key.title()}..."
        except Exception as e:
            print(f"[SYSTEM ACTIONS] Failed to launch '{matched_key}': {e}")
            return f"I tried to open {matched_key.title()} but encountered an error: {e}"
    else:
        best_match = f" (Best match was '{match[0]}' at {match[1]:.1f}%)" if match else ""
        print(f"[SYSTEM ACTIONS] App matching failed for '{name_clean}'{best_match}")
        return f"I couldn't find an application matching '{name}' on your system."


def open_website(site_or_url: str) -> str:
    """
    Opens a website by name or URL in the default browser.

    Args:
        site_or_url: The name of the site (e.g., 'youtube') or a full URL.
    """
    print(f"[SYSTEM ACTIONS] open_website called with argument: '{site_or_url}'")

    if not isinstance(site_or_url, str) or not site_or_url.strip():
        return "I couldn't figure out which website you want to open."

    site_clean = _normalize_name(site_or_url)

    if not site_clean or len(site_clean) < 2:
        print(f"[SYSTEM ACTIONS] Website matching failed: normalized name '{site_clean}' is too short/empty.")
        return "I couldn't figure out which website you want to open."

    # Reject obvious injection
    if _SHELL_INJECTION_RE.search(site_or_url):
        # But allow https:// — check only the alias part
        if not site_or_url.strip().lower().startswith("http"):
            return "That website name contains invalid characters."

    print(f"[SYSTEM ACTIONS] Normalized site name: '{site_clean}'")

    matched_url = None
    matched_key = None

    choices = list(SITE_ALIASES.keys())
    match = process.extractOne(site_clean, choices, scorer=fuzz.WRatio)

    if match and match[1] >= 75:
        matched_key = match[0]
        matched_url = SITE_ALIASES[matched_key]
        print(f"[SYSTEM ACTIONS] Resolver matched site alias '{matched_key}' with score {match[1]:.1f}% -> URL: {matched_url}")
    else:
        # Check if it looks like a URL directly
        if "." in site_clean and " " not in site_clean:
            # Basic URL validation
            if re.match(r'^[a-z0-9.-]+\.[a-z]{2,}(/.*)?$', site_clean):
                matched_url = site_clean if site_clean.startswith("http") else f"https://{site_clean}"
                print(f"[SYSTEM ACTIONS] Resolver interpreted as direct URL -> {matched_url}")

    if not matched_url:
        print(f"[SYSTEM ACTIONS] Website matching failed for '{site_clean}'.")
        return f"I couldn't find a website matching '{site_or_url}', and it doesn't look like a valid URL."

    # Final URL safety check
    if not matched_url.startswith("https://") and not matched_url.startswith("http://"):
        return "Invalid URL format."

    try:
        webbrowser.open_new_tab(matched_url)
        display_name = matched_key.title() if matched_key else matched_url
        print(f"[SYSTEM ACTIONS] Successfully opened website '{display_name}'.")
        return f"Opening {display_name}..."
    except Exception as e:
        print(f"[SYSTEM ACTIONS] Failed to open website: {e}")
        return f"Failed to open website: {e}"


def check_fast_path(message: str):
    """
    Checks if a message matches a fast-path action (open/launch/start/show me/pull up X).
    Returns (True, response) if handled, (False, None) if it should fall through to LLM.
    """
    if not isinstance(message, str) or not message.strip():
        return False, None

    # Truncate to prevent ReDoS on very long input
    message = message.strip()[:500]
    print(f"\n[SYSTEM ACTIONS] --- Fast path attempt for: '{message}' ---")

    pattern = r"^(?:can you please\s+|can you\s+|could you\s+|please\s+|hey jarvis\s+|jarvis\s+|i want to\s+|i need to\s+|let's\s+)?(?:open|launch|start|pull\s+up|show\s+me|get\s+me)\s+(.+)$"
    match = re.match(pattern, message, re.IGNORECASE)
    if not match:
        print(f"[SYSTEM ACTIONS] Message did not match fast-path regex.")
        return False, None

    target = match.group(1).strip()
    # Strip trailing punctuation that users often add
    target = re.sub(r'[.!?]+$', '', target).strip()
    target_clean = _normalize_name(target)
    print(f"[SYSTEM ACTIONS] Fast path regex matched. Target extracted: '{target}', normalized: '{target_clean}'")

    if not target_clean or len(target_clean) < 2:
        print(f"[SYSTEM ACTIONS] Normalized target '{target_clean}' is too short/empty. Falling back to LLM.")
        return False, None

    if _SHELL_INJECTION_RE.search(target_clean) and "." not in target_clean:
        print(f"[SYSTEM ACTIONS] Target contains injection chars, falling back to LLM.")
        return False, None

    _ensure_catalog()

    # 1. Try website aliases (fuzzy match) — higher threshold to avoid false positives
    site_choices = list(SITE_ALIASES.keys())
    site_match = process.extractOne(target_clean, site_choices, scorer=fuzz.WRatio)
    if site_match and site_match[1] >= 85:
        print(f"[SYSTEM ACTIONS] Fast path routing to open_website for '{site_match[0]}'")
        return True, open_website(target_clean)

    # 2. Try direct URL
    if "." in target_clean and " " not in target_clean:
        if re.match(r'^[a-z0-9.-]+\.[a-z]{2,}(/.*)?$', target_clean):
            print(f"[SYSTEM ACTIONS] Fast path routing to open_website for direct URL '{target_clean}'")
            return True, open_website(target_clean)

    # 3. Try app catalog (fuzzy match)
    app_choices = list(DYNAMIC_APP_CATALOG.keys())
    if app_choices:
        app_match = process.extractOne(target_clean, app_choices, scorer=fuzz.WRatio)
        if app_match and app_match[1] >= 85:
            print(f"[SYSTEM ACTIONS] Fast path routing to open_app for '{app_match[0]}'")
            return True, open_app(target_clean)

        print(f"[SYSTEM ACTIONS] Fast path failed to find high-confidence match (Site best: {site_match[1] if site_match else 0:.1f}%, App best: {app_match[1] if app_match else 0:.1f}%). Falling back to LLM.")
    return False, None
