import os
import sys

script_dir = os.path.dirname(os.path.realpath(__file__))

sys.path.append(os.path.join(script_dir, 'modules', 'fileseq-1.2.1', 'src'))

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
