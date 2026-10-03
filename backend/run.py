import os
import sys
import multiprocessing

# Ensure backend dir is on sys.path so `import main` works both as script and as PyInstaller bundle
backend_dir = os.path.dirname(__file__)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import uvicorn

# Support both `from main import app` and `from backend.main import app`
try:
    from main import app
except ImportError:
    from backend.main import app

if __name__ == '__main__':
    multiprocessing.freeze_support()
    port = int(os.getenv("VEGA_PORT", "8000"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
