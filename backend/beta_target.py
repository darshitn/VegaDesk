"""Strict validation and path resolution for VEGA Beta Acceptance Profile database.

Guarantees:
1. Dedicated beta target with expected filename 'jarvis-beta.db'.
2. Fail-closed on conflicting overrides, production paths, path traversals, or symlink escapes.
3. Test-only custom roots must be explicit and validated.
4. Normal-mode behavior remains completely intact.
"""

import os
import sys


def get_default_beta_root() -> str:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "Jarvis_Dashboard_Beta")
    return os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "Jarvis_Dashboard_Beta")


def get_production_paths() -> list[str]:
    """Returns canonical paths known to belong to production/personal data."""
    prods = []
    base_dir = os.path.dirname(__file__)
    prods.append(os.path.realpath(os.path.abspath(os.path.join(base_dir, "jarvis.db"))))

    appdata = os.environ.get("APPDATA")
    if appdata:
        prod_root = os.path.join(appdata, "Jarvis_Dashboard")
        prods.append(os.path.realpath(os.path.abspath(prod_root)))
        prods.append(os.path.realpath(os.path.abspath(os.path.join(prod_root, "jarvis.db"))))
        prods.append(os.path.realpath(os.path.abspath(os.path.join(prod_root, "userData", "jarvis.db"))))
        prods.append(os.path.realpath(os.path.abspath(os.path.join(prod_root, "db", "jarvis.db"))))

    return prods


def validate_beta_db_path(db_path: str, custom_root: str = None) -> str:
    """Validates that db_path points strictly to a safe, approved beta target.

    Raises ValueError on:
    - Non-beta profile environment
    - Filename other than 'jarvis-beta.db'
    - Conflict with production jarvis.db paths or roots
    - Path traversal or symlink escapes
    - Substring-only pseudo-beta filenames like 'not-really-beta.db' or 'jarvis.db'
    """
    if not db_path or not isinstance(db_path, str):
        raise ValueError("Beta DB path must be a non-empty string.")

    # 1. Profile check
    if os.getenv("VEGA_PROFILE") != "beta":
        raise ValueError("Cannot validate beta target when VEGA_PROFILE is not 'beta'.")

    # 2. Canonical resolution
    abs_path = os.path.abspath(db_path)
    real_path = os.path.realpath(abs_path)

    # 3. Filename check: MUST be exactly 'jarvis-beta.db'
    basename = os.path.basename(real_path)
    if basename.lower() != "jarvis-beta.db":
        raise ValueError(
            f"Invalid beta database filename '{basename}'. "
            "Beta target must be strictly named 'jarvis-beta.db'."
        )

    # 4. Conflict check against known production paths
    prods = get_production_paths()
    for prod in prods:
        if real_path.lower() == prod.lower() or real_path.lower().startswith(prod.lower() + os.sep):
            raise ValueError(
                f"Conflicting production target '{real_path}'. "
                "Beta profile is strictly forbidden from targeting production data paths."
            )

    # 5. Root containment check
    expected_root = custom_root or os.getenv("VEGA_PROFILE_ROOT") or get_default_beta_root()
    real_expected_root = os.path.realpath(os.path.abspath(expected_root))

    # Guard against using production root or filesystem root as custom root
    for prod in prods:
        if real_expected_root.lower() == prod.lower():
            raise ValueError(f"Custom beta root matches production root '{real_expected_root}'.")

    # If drive root like C:\ or \
    if os.path.dirname(real_expected_root) == real_expected_root:
        raise ValueError(f"Custom beta root cannot be filesystem root '{real_expected_root}'.")

    # Verify that real_path is within real_expected_root (or in test temp dir if custom_root)
    norm_real_path = os.path.normcase(real_path)
    norm_root = os.path.normcase(real_expected_root)

    if not (norm_real_path.startswith(norm_root + os.sep) or norm_real_path == os.path.normcase(os.path.join(real_expected_root, "jarvis-beta.db"))):
        # Check if custom_root was given or tmp_path test fixture
        if custom_root:
            raise ValueError(
                f"Beta DB path '{real_path}' escaped the designated root '{real_expected_root}'."
            )
        else:
            raise ValueError(
                f"Beta DB path '{real_path}' is outside approved beta root '{real_expected_root}'."
            )

    return real_path


def resolve_beta_db_path() -> str:
    """Resolves and validates the DB_PATH to use under VEGA_PROFILE=beta.

    If JARVIS_DB_PATH is in env, validates it strictly.
    If not, defaults to %APPDATA%/Jarvis_Dashboard_Beta/db/jarvis-beta.db.
    """
    custom_root = os.getenv("VEGA_PROFILE_ROOT")
    jarvis_db_path = os.getenv("JARVIS_DB_PATH")

    if jarvis_db_path:
        # Strict validation: fails closed if invalid or pointing to production
        return validate_beta_db_path(jarvis_db_path, custom_root=custom_root)

    beta_root = custom_root or get_default_beta_root()
    real_root = os.path.realpath(os.path.abspath(beta_root))

    # Guard against invalid root
    if os.path.dirname(real_root) == real_root:
        raise ValueError(f"Invalid beta root '{real_root}'.")

    beta_db_dir = os.path.join(real_root, "db")
    os.makedirs(beta_db_dir, exist_ok=True)
    target = os.path.join(beta_db_dir, "jarvis-beta.db")
    return validate_beta_db_path(target, custom_root=real_root)
