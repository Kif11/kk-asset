from fileseq import FileSequence
from pathlib import Path
# from logger import Logger
from errors import InvalidSequenceError, BrokenSequenceError
import utils

import subprocess
import logging
import yaml
import shutil
import json
import sys
import os
import re

################################################################################
# Configuration
################################################################################
debug = False

global config  # Module configuration from config.yml

script_dir = os.path.dirname(os.path.realpath(__file__))
config_file = Path(script_dir, 'config.yml')

# Read the module configuration
with config_file.open('r') as f:
    config = yaml.load(f)

# Cache some configuration variables
video_files_formats = config['video_files_formats']
image_files_formats = config['image_files_formats']

log = logging.getLogger(__name__)
def set_logger(logger):
    global log
    log = logger

################################################################################
# Factory functions
################################################################################

def asset_from_path(path):
    """
    Factory method
    """
    path = Path(path)

    # If path represent an image sequence
    # use its parent folder instead
    if '%' in str(path.name):
        path = path.parent

    if not path.exists():
        log.error('Path %s does not exists' % path)
        raise Exception

    if path.is_dir():
        asset = ImageSequence(path)
    elif path.suffix.lstrip('.') in video_files_formats:
        asset = VideoFile(path)
    elif path.suffix.lstrip('.') in image_files_formats:
        asset = ImageFile(path)
    elif path.is_file():
        asset = LocalFile(path)
    else:
        log.error('Path is not a file or directory')
        raise Exception

    return asset

################################################################################
# Classes
################################################################################


class Asset(object):
    """
    Base class for all of the local assets
    Do not instanciate directly. Use factory methods such as asset_from_path.
    """
    def __init__(self, path):

        path = Path(path)
        platform = {'linux2': 'linux', 'darwin': 'mac', 'win32': 'win'}[sys.platform]

        if not path.exists():
            raise Exception('Specified path does not exist: %s' % path)
        self._path = path

        # Regular expression pattern to use
        # for retrieving version number from a file name
        self.version_patterns = config['versions_regex']

        # Determine ffmpeg and ffprobe paths
        ffmpeg_dir = os.environ.get('FFMPEG_DIR')

        if os.environ.get('FFMPEG_DIR') is not None:
            # From the environmental variable
            ffmpeg_dir = os.environ.get('FFMPEG_DIR')
        elif config['ffmpeg_dir'][platform] is not None:
            # From configuration file
            ffmpeg_dir = config['ffmpeg_dir'][platform]
        else:
            ffmpeg_dir = ''
            log.warning(
                'Can not determine ffmpeg path.'
                'Please set FFMPEG_DIR environmental variable '
                'or specify path in the config.yml'
            )

        self._ffmpeg = Path(ffmpeg_dir, 'ffmpeg')
        self._ffprobe = Path(ffmpeg_dir, 'ffprobe')

        # Corresponding shotgun metadata for this asset
        self.sg_data = {}
        # Dictionary of extra attributes to pass along with asset
        self.extra_attrs = {}

        # Determine the difference between the first and the second frame when
        # detecting a slate. The lower the value the more sensitive it to changes
        self.slate_threshold = 0.2

        self.tmp_files = []

    @property
    def type(self):
        return self.__class__.__name__

    @property
    def path(self):
        return Path(self._path)

    @property
    def version(self):
        """
        Try to determine file version from its name base on different regex patterns

        :returns: None or integer
        """
        # Return firs matched pattern in self.version_patterns list
        for p in self.version_patterns:
            result = re.finditer(p, str(self.base_name))
            if result is not None:
                # log.debug('Regex version result: %s', result.group('version_number'))
                version = None
                # We alway want to use version found at the and of the name
                # e.g. for name like 'rvb300_match_30mlCamZv03_v006.fbx' we should return 6 not 3
                for v in result:
                    version = v.group('version_number')

                return int(version)

        return None

    @property
    def name(self):
        return self.path.name

    @property
    def base_name(self):
        return self.path.stem

    @property
    def core_name(self):
        """ Base name without version """
        core_name = re.sub(r'[_.]v[0-9]+', '', self.base_name)
        return core_name

    @property
    def extension(self):
        ext = str(self.path.suffix).lstrip('.')
        return ext

    @property
    def start(self):
        frame_number = re.findall(re.compile('\.(\d+)\.\w+'), str(self.name))
        if frame_number:
            return int(frame_number[0])
        else:
            return 1

    @property
    def end(self):
        frame_number = re.findall(re.compile('\.(\d+)\.\w+'), str(self.name))
        if frame_number:
            return int(frame_number[0])
        else:
            return 1

    @property
    def frame_range(self):
        return '1-1'

    @property
    def frame_count(self):
        return 1

    def _get_tmp_dir(self):
        tmp = None
        if sys.platform == 'darwin' or 'linux' in sys.platform:
            tmp = os.path.abspath(os.environ.get('TMPDIR'))
        elif sys.platform == 'win32':
            tmp = os.path.abspath(os.environ.get('TEMP')).replace(os.sep, '/')
            # On Windows, some usernames are shortened with a tilde.
            # Example: EVILEY~1 instead of evileye_52
            import getpass
            tmp = tmp.split('/')
            for item in tmp:
                if '~1' in item:
                    tmp[tmp.index(item)] = getpass.getuser()
            tmp = '/'.join(tmp)
        return Path(tmp)

    def _get_tmp_file(self, name):
        tmp_dir = self._get_tmp_dir()
        temp_file_path = Path(tmp_dir, name)
        self.tmp_files.append(temp_file_path)
        return temp_file_path

    def has_slate(self):
        return False

    def remove_tmp_files(self):
        for f in self.tmp_files:
            if f.exists():
                f.unlink()

    def fields_from_name(self, name_template):
        """
        Given a template like {shot}_{task} try to extract values from
        asset core_name
        returns: Dictionary of template name and name values
        """

        if not name_template:
            return None

        # Find non token delimiters such as '_', '.', '_v'
        # Everything in between }...{
        delimeters = re.findall(r'}([a-zA-Z-_.]+){', name_template)
        # Find all {...} tokens
        tokens = re.findall(r'{([a-z]+)}', name_template)

        if delimeters:
            # Split name base on the template separator
            name_values = re.split(delimeters[0], self.core_name)
        else:
            name_values = [self.core_name]

        # Check if number of template fields and name values match
        if len(tokens) != len(name_values):
            log.warning(
                'Template %s does not match the name %s'
                % (name_template, self.core_name)
            )
            return None

        # Zip template names and values together
        fields = dict(zip(tokens, name_values))

        return fields

    def get_media_info(self, path):
        cmd = [
            str(self._ffprobe), '-v', 'quiet', '-select_streams', 'v',
            '-show_streams', '-print_format', 'json', str(path)
        ]

        try:
            result = subprocess.check_output(cmd)
            output = json.loads(result)
        except subprocess.CalledProcessError as e:
            log.error('ffprobe failed to extract information about the asset. %s' % e)
            log.debug('Test this command: %s' % ' '.join(cmd))
            raise
        except Exception:
            log.error('Error happened while excuting CMD command.')
            log.debug('Test this command: %s' % ' '.join(cmd))
            raise

        if not output:
            log.warning('No media streams are found in %s' % path)
            stream = {}
            return stream

        if len(output.get('streams')) == 1:
            stream = output['streams'][0]
        elif len(output.get('streams')) > 1:
            log.warning(
                'Media file %s contains more then one streams. '
                'Using the first one. '
            )
            stream = output['streams'][0]

        return stream

    def copy(self, dst, dry_run=False):

        dst = Path(dst)

        if dry_run:
            log.info('Dry run mode is active!')
            log.info('Copy %s to %s' % (self.path, dst))
            return

        if dst.exists():
            log.warning('Local file %s already exists' % dst.name)
            new_file_asset = asset_from_path(dst)
            return new_file_asset

        if not dst.parent.exists():
            dst.parent.mkdir(parents=True)

        log.info('Copy %s to %s' % (self.path, dst))
        shutil.copy(str(self.path), str(dst))

        new_file_asset = asset_from_path(dst)

        return new_file_asset


class ImageSequence(Asset):
    """
    Represent any image or file sequence
    Do not instantiate directly. Use factory methods such as asset_from_path.
    """

    def __init__(self, path):
        super(self.__class__, self).__init__(path)
        path = Path(path)
        seqs = FileSequence.findSequencesOnDisk(str(path))

        self.sequence_data = None

        if len(seqs) == 1:
            self.seq = seqs[0]

            # Check for broken sequnces
            if len(self.seq.frameSet().__str__().split(',')) != 1:
                raise BrokenSequenceError(
                    'Sequence broken and has missing frames: %s'
                    % self.seq.frameSet()
                )
        elif len(seqs) > 1:
            # Case where the folder contains two
            # sequences with two different names
            seq_paths = '\n'
            for i in seqs:
                seq_paths = seq_paths + '\n' + i.path()
            raise InvalidSequenceError('Multiple file sequences error!')
        elif len(seqs) == 0:
            raise InvalidSequenceError('No sequences found in the folder %s' % path)

    @property
    def base_name(self):
        # On windows fileseq module return base name as a full path
        # which is not expected in this case. We only need file base name
        # without sequence number
        if sys.platform == 'win32':
            base_name = str(Path(self.seq.basename()).name).rstrip('._')
        else:
            base_name = self.seq.basename().rstrip('._')
        return base_name

    def frame_path(self, number):
        return Path(self.seq.frame(number))

    @property
    def extension(self):
        ext = self.seq.extension().lstrip('.')
        return ext

    @property
    def path(self):
        return Path(self.seq.path())

    @property
    def width(self):
        return self.resolution()[0]

    @property
    def height(self):
        return self.resolution()[1]

    def resolution(self):
        if self.sequence_data is None:
            data = self.get_media_info(str(self.frame_path(self.start)))
            self.sequence_data = data
        else:
            data = self.sequence_data
        resolution = (int(data['width']), int(data['height']))
        return resolution

    @property
    def frame_count(self):
        frame_count = int(self.seq.end() - self.seq.start()) + 1
        return frame_count

    @property
    def frame_range(self):
        return self.seq.frameRange()

    @property
    def start(self):
        return self.seq.start()

    @property
    def end(self):
        return self.seq.end()

    @property
    def directory(self):
        directory = Path(self.seq.dirname())
        return directory

    @property
    def directory_name(self):
        directory_name = Path(self.seq.dirname()).name
        return directory_name

    @property
    def thumbnail(self):
        thumbnail_path = self.frame_path(self.start)
        return thumbnail_path

    def has_slate(self):
        """
        Detect a slate on the first frame of the image sequence
        by using scene change analysis ffprobe filter

        :returns: (bool) True is image sequence has a slate
        """
        first_frame = str(self.path) % self.start
        second_frame = str(self.path) % (self.start + 1)

        # Alway pass forward slashed path to ffmpeg also replace ':' with
        # double escaped //: for windows paths
        first_frame = first_frame.replace('\\', '/').replace(':', '\\\\:')
        second_frame = second_frame.replace('\\', '/').replace(':', '\\\\:')

        # Because I did not find a way to probe an image sequence that starts
        # with arbitrary frame number I concatenate the first two frames
        # together to form a video stream. Then I pass this video stream
        # to scene change detect filter
        image_filter = (
            'movie={first_frame} [img1]; '
            'movie={second_frame} [img2]; '
            '[img1] [img2] concat [out]; '
            '[out] select=gt(scene\,{slate_threshold})'
        ).format(
            first_frame=first_frame,
            second_frame=second_frame,
            slate_threshold=self.slate_threshold
        )

        cmd = [
            str(self._ffprobe), '-show_frames', '-v', 'quiet', '-read_intervals',
            '%+#3', '-print_format', 'json', '-f', 'lavfi', image_filter
        ]

        if debug:
            cmd.pop(2)
            cmd.pop(2)

        result = subprocess.check_output(cmd)
        frames = json.loads(result).get('frames', '')

        if frames and float(frames[0]['pkt_dts_time']) < 1:
            return True
        else:
            return False

    def generate_thumbnail(self, x_size=320, y_size=-1):
        middle_frame = str(self.path) % (self.start + (self.frame_count / 2))
        middle_frame = middle_frame.replace('\\', '/').replace(':', '\\\\:')
        tmp_thumb = self._get_tmp_file('%s_tmp_thumb.png' % self.base_name)
        filters = 'scale=%s:%s' % (x_size, y_size)
        cmd = [
            str(self._ffmpeg), '-v', 'quiet', '-i', str(middle_frame), '-y', '-vf', filters, str(tmp_thumb)
        ]
        if debug:
            cmd.pop(1)
            cmd.pop(1)
        try:
            exit_status = subprocess.call(cmd)
        except Exception as e:
            log.error('Failed to generate thumbnail. %s' % e)
            return None

        if exit_status != 0:
            log.error('Failed to generate thumbnail.')
            return None

        return tmp_thumb

    def copy(self, dst, start_offset=0, new_start_frame=None, override=False, dry_run=False):
        """
        Copy ImageSequence to the target destination frame by frame

        :return: New FileSequnce asset object
        """

        dst = Path(dst)

        # Create parent folder if not exists
        if not dst.parent.exists():
            dst.parent.mkdir(parents=True)

        log.info('Copy %s to %s' % (self.path, dst))

        skipped_frames = []
        warnings = set()

        if new_start_frame is None:
            new_start_frame = self.start

        old_frame = self.start + start_offset
        dst_frame_count = self.frame_count - start_offset
        copied = False
        # Copy sequence to the publish folder frame by frame
        # Alway start from frame 1001
        log.info('Starting copy for %s frames total' % dst_frame_count)
        for i in range(0, dst_frame_count):
            new_frame = new_start_frame + i

            old_path = str(self.path) % old_frame
            new_path = str(dst) % new_frame

            new_path_p = Path(new_path)

            # Skip frame if already exists
            if new_path_p.exists() and not override:
                # skipped_frames.append(new_path_p)
                log.info('Frame %d already exists' % (i+1))
                continue

            if dry_run:
                log.info('Dry run mode is active')
                log.info('Copy %s to %s' % (old_path, new_path))
                old_frame += 1
                continue

            # Attempt to copy frame with system specific command
            # such as cp and xcopy. Fall back to shutil if fails
            try:
                utils.system_copy(old_path, new_path)
                copied = True
            except Exception as e:
                msg = (
                    'Unable to execute fast system copy. %s. '
                    'Default python shutil copy were used.' % e
                )
                warnings.add(msg)

                shutil.copy(old_path, new_path)
                copied = True
            log.info('Frame %d copied' % (i+1))
            # Print feedback to the console
            # log.info("Copied %d out of %d frames" % (i+1, dst_frame_count))

            old_frame += 1

        # if copied:
        #     print  # Empty line

        # Check all of the events happened during copying
        #
        # if skipped_frames:
        #     log.warning(
        #         'Sequence %s. Copy of some of the frames were skipped because they '
        #         'were already present in the target destination.'
        #         % self.name
        #     )
            # log.debug([i.name for i in skipped_frames])

        if warnings:
            log.warning('Some warning were raised during copying: ')
            for i, w in enumerate(warnings):
                log.warning('\t%02d: %s' % (i+1, w))

        new_sequence_asset = asset_from_path(new_path_p.parent)

        return new_sequence_asset


class ImageFile(Asset):
    """
    Represent a local video single image file
    """
    def __init__(self, path):
        super(self.__class__, self).__init__(path)
        self.file_data = None

    @property
    def width(self):
        return self.resolution()[0]

    @property
    def height(self):
        return self.resolution()[1]

    def resolution(self):
        if self.file_data is None:
            data = self.get_media_info(str(self.path))
            self.file_data = data
        else:
            data = self.file_data
        resolution = (int(data['width']), int(data['height']))

        return resolution

    def generate_thumbnail(self, x_size=320, y_size=-1):
        tmp_thumb = self._get_tmp_file('%s_tmp_thumb.png' % self.base_name)
        filters = 'scale=%s:%s' % (x_size, y_size)
        cmd = [
            str(self._ffmpeg), '-v', 'quiet', '-i', str(self.path), '-y', '-vf', filters, str(tmp_thumb)
        ]
        if debug:
            cmd.pop(1)
            cmd.pop(1)
        try:
            exit_status = subprocess.call(cmd)
        except Exception as e:
            log.error('Failed to generate thumbnail. %s' % e)
            return None

        if exit_status != 0:
            log.error('Failed to generate thumbnail.')
            return None

        return tmp_thumb


class VideoFile(Asset):
    """
    Represent a local video file
    """

    def __init__(self, path):
        super(self.__class__, self).__init__(path)

        self.mov_data = None

    @property
    def start(self):
        return 1

    @property
    def end(self):
        return self.frame_count

    @property
    def frame_range(self):
        frame_range = '%s-%s' % (1, self.frame_count)
        return frame_range

    @property
    def frame_count(self):
        data = self.get_media_info(str(self.path))
        frame_count = int(data.get('nb_frames'))
        return frame_count

    @property
    def thumbnail(self):
        return None

    @property
    def width(self):
        return self.resolution()[0]

    @property
    def height(self):
        return self.resolution()[1]

    def resolution(self):
        if self.mov_data is None:
            data = self.get_media_info(str(self.path))
            self.mov_data = data
        else:
            data = self.mov_data
        resolution = (int(data['width']), int(data['height']))
        return resolution

    def generate_thumbnail(self, x_size=320, y_size=-1):

        tmp_thumb = self._get_tmp_file('%s_tmp_thumb.png' % self.base_name)
        filters = 'scale=%s:%s' % (x_size, y_size)
        cmd = [
            str(self._ffmpeg), '-v', 'quiet', '-i', str(self.path), '-vframes', '1', '-vf', filters, str(tmp_thumb)
        ]
        if debug:
            cmd.pop(1)
            cmd.pop(1)
        try:
            exit_status = subprocess.call(cmd)
        except Exception as e:
            log.error('Failed to generate thumbnail. %s' % e)
            return None

        if exit_status != 0:
            log.error('Failed to generate thumbnail.')
            return None

        return tmp_thumb

    def has_slate(self):
        """
        Detect a slate on the first frame of the video
        by using scene change analysis ffprobe filter

        :returns: (bool) True is video has a slate
        """
        mov_path = str(self.path).replace('\\', '/')
        mov_path = mov_path.replace(':', '\\\\:')

        image_filter = (
            'movie={mov_path}, '
            'select=gt(scene\,{slate_threshold})'
        ).format(mov_path=mov_path, slate_threshold=self.slate_threshold)

        cmd = [
            str(self._ffprobe), '-show_frames', '-v', 'quiet', '-read_intervals',
            '%+#3', '-print_format', 'json', '-f', 'lavfi', image_filter
        ]

        if debug:
            cmd.pop(2)
            cmd.pop(2)

        result = subprocess.check_output(cmd)
        frames = json.loads(result).get('frames', '')

        if frames and float(frames[0]['pkt_dts_time']) < 1:
            return True
        else:
            return False


class LocalFile(Asset):
    """
    Represent a local file that wont encompassed by other
    more specific classes. Can be a single image
    """

    def __init__(self, path):
        super(self.__class__, self).__init__(path)
