import os

# Set default logging handler to avoid "No handler found" warnings.
import logging
try:  # Python 2.7+
    from logging import NullHandler
except ImportError:
    class NullHandler(logging.Handler):
        def emit(self, record):
            pass

logging.getLogger().addHandler(NullHandler())

if os.environ.get('DEBUG', False):
    import asset
    reload(asset)

from asset import Asset
from asset import asset_from_path
from asset import set_logger
