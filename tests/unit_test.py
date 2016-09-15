import os
import unittest
from pathlib import Path
from asset import asset_from_path
import shutil

test_dir = os.path.dirname(os.path.realpath(__file__))
samples_dir = Path(test_dir, 'sample_files')
tmp_dir = Path(test_dir, 'tmp')

class AssetTests(unittest.TestCase):

    def test_sequence_copy_basic(self):
        """
        Copy a sequence and make sure that
        copy succeded by checking its exit status
        """
        test_seq = Path(samples_dir, 'dpx_seq')
        asset = asset_from_path(test_seq)
        dst_seq = Path(tmp_dir, 'test_seq', 'lpk0000_edit_v0001.%04d.dpx')

        # Make sure that the target distanation doesn't exist
        if dst_seq.parent.exists():
            shutil.rmtree(str(dst_seq.parent))

        assert(asset.copy(dst_seq)==0)

    def test_sequence_missing_frames(self):
        test_seq = Path(samples_dir, 'dpx_seq_missing_frame')
        try:
            asset_from_path(test_seq)
        except Exception as e:
            assert(str(e) == 'Sequence broken and has missing frames: 1-3,5-10')

    def test_sequence_copy_trim_slate(self):
        """
        //
        """
        test_seq = Path(samples_dir, 'dpx_seq_with_slate')
        asset = asset_from_path(test_seq)
        dst_seq = Path(tmp_dir, 'test_seq_trimed_slate', 'lpk0000_edit_v0001.%04d.dpx')

        # Make sure that the target distanation doesn't exist
        if dst_seq.parent.exists():
            shutil.rmtree(str(dst_seq.parent))

        assert(asset.copy(dst_seq, start_offset=1, new_start_frame=1001)==0)

    def test_sequence_copy_start_with_1001(self):
        """
        //
        """
        test_seq = Path(samples_dir, 'dpx_seq_start_with_1001')
        asset = asset_from_path(test_seq)
        dst_seq = Path(tmp_dir, 'dpx_seq_start_with_1001', 'lpk0000_edit_v0001.%04d.dpx')

        # Make sure that the target distanation doesn't exist
        if dst_seq.parent.exists():
            shutil.rmtree(str(dst_seq.parent))

        assert(asset.copy(dst_seq, start_offset=0)==0)

if __name__ == '__main__':
    unittest.main(verbosity=2)
