"""Keep beta startup tests from replacing other tests' imported app and ORM objects."""

import os
import subprocess
import sys

import pytest


_CHILD_NODE = "VEGA_PYTEST_ISOLATED_BETA_NODE"


def isolated_bootstrap(test):
    # A child collects the original test, including its actual beta fixtures.
    # The parent collects the wrapper, so those fixtures never reload its modules.
    if os.environ.get(_CHILD_NODE, "").endswith(f"::{test.__name__}"):
        return test

    def run_in_child(request, tmp_path):
        env = os.environ.copy()
        for key in ("VEGA_PROFILE", "VEGA_PROFILE_ROOT", "VEGA_RUN_ID",
                    "VEGA_BETA_OLLAMA", "VEGA_BETA_VOICE", "VEGA_ENABLE_VOICE",
                    "PYTEST_ADDOPTS"):
            env.pop(key, None)
        env.update({
            _CHILD_NODE: request.node.nodeid,
            "TEMP": str(tmp_path),
            "TMP": str(tmp_path),
            "TMPDIR": str(tmp_path),
            "PYTHON_DOTENV_DISABLED": "1",
        })
        cmd = [sys.executable, "-m", "pytest", request.node.nodeid,
               "-q", "--tb=short", "-p", "no:cacheprovider",
               "--basetemp", str(tmp_path / "child-pytest"),
               "-o", "faulthandler_timeout=20"]
        try:
            result = subprocess.run(cmd, cwd=request.config.rootpath, env=env,
                                    capture_output=True, text=True, errors="replace", timeout=60)
        except subprocess.TimeoutExpired as exc:
            pytest.fail(f"Isolated beta test timed out after {exc.timeout}s:\n{exc.stdout or ''}\n{exc.stderr or ''}",
                        pytrace=False)
        if result.returncode != 0:
            pytest.fail(f"Isolated beta test exited {result.returncode}:\n{result.stdout}\n{result.stderr}",
                        pytrace=False)

    # Preserve the pytest node name without exposing the original fixture signature.
    run_in_child.__name__ = test.__name__
    run_in_child.__doc__ = test.__doc__
    return run_in_child
