"""SMB Download plugin entry point."""
import os
import sys

_modules = os.path.join(os.path.dirname(__file__), "py_modules")
sys.path.insert(0, _modules)
_vendor = os.path.join(_modules, "smb_download_vendor")
if os.path.isdir(_vendor):
    sys.path.insert(0, _vendor)

from smb_download_plugin import SmbDownloadPluginMixin


class Plugin(SmbDownloadPluginMixin):
    pass
