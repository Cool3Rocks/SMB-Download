# Frozen-runtime standard-library fallback

These files are unmodified copies from CPython tag `v3.11.7`:

- https://github.com/python/cpython/blob/v3.11.7/Lib/logging/config.py
- https://github.com/python/cpython/blob/v3.11.7/Lib/socketserver.py
- https://github.com/python/cpython/blob/v3.11.7/Lib/configparser.py

The Python Software Foundation license is included in `LICENSE`.
Only missing modules are loaded from this directory. No replacement logging
package is provided; `logging.__path__` is extended to locate `logging.config`.
