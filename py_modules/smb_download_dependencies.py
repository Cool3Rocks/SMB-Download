"""SMB import diagnostics. No SMB configuration or credentials are inspected here."""
import importlib
import importlib.machinery
import logging
import os
import platform
import sys
import traceback


def prepare_stdlib():
    """Add missing pure-Python stdlib modules to Decky's frozen interpreter.

    Keep the existing logging package and all existing stdlib modules. Only
    modules absent from the frozen application are resolved from the fallback.
    """
    fallback = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smb_download_stdlib")
    if os.path.isdir(fallback):
        if fallback not in sys.path:
            sys.path.append(fallback)
        logging_path = os.path.join(fallback, "logging")
        if logging_path not in logging.__path__:
            logging.__path__.append(logging_path)


def load_smbclient():
    prepare_stdlib()
    vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smb_download_vendor")
    if os.path.isdir(vendor) and vendor not in sys.path:
        sys.path.insert(0, vendor)
    return importlib.import_module("smbclient")


def dependency_status():
    vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smb_download_vendor")
    result = dict(smb_available=False, smb_error="", python_version=platform.python_version(),
                  platform=platform.system() + " " + platform.machine(), vendor_path=vendor,
                  vendor_exists=os.path.isdir(vendor), extension_suffixes=importlib.machinery.EXTENSION_SUFFIXES)
    result["stdlib_fallback"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smb_download_stdlib")
    try:
        client = load_smbclient()
        result["smb_available"] = True
        result["smb_module"] = str(getattr(client, "__file__", ""))
    except Exception as exc:
        result["smb_error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
    return result


def require_smbclient():
    try:
        return load_smbclient()
    except Exception as exc:
        logging.getLogger("SMB-Download.SMB").exception("SMB dependency import failed")
        raise RuntimeError(f"SMB 依赖加载失败（Python {platform.python_version()}）：{type(exc).__name__}: {exc}") from exc
