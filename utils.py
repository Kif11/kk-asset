import subprocess
import sys
import shutil
import logging

from pathlib import Path

log = logging.getLogger(__name__)


def system_copy(src, dst):
    """
    Perform standart system copy of a file by using
    'xcopy' on Windows and 'cp' on Mac

    NOTE(Kirill): Copy tests of 105 frame sequence (30 MB a frame)
    show that 'xcopy' perform 10 times faster then shutil although
    'cp' gain only 0.2 times performance.
    """

    src = Path(src)
    dst = Path(dst)

    if sys.platform.startswith("darwin"):
        cmd = ['cp', str(src), str(dst)]
    elif sys.platform.startswith("win"):
        cmd = ['xcopy', str(src), str(dst) + '*', '/Y']
    else:
        log.warning('Unknown platform. Using shutil copy.')
        shutil.copy(str(src), str(dst))
        return

    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p.communicate() #now wait
