from fileseq import FileSequence
from pathlib import Path
from logger import Logger
from errors import InvalidSequenceError, BrokenSequenceError
import utils

import subprocess
import yaml
import shutil
import json
import sys
import os
import re

log = Logger(debug=True)
debug = False

################################################################################
# Configuration
################################################################################

global config # Module configuration from config.yml

script_dir = os.path.dirname(os.path.realpath(__file__))
config_file = Path(script_dir, 'config.yml')

# Read the module configuration
with config_file.open('r') as f:
    config = yaml.load(f)

# Cache some configuration variables
video_files_formats = config['video_files_formats']

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

        # Regular expression partern to use
        # for retriving version number from a file name
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
        # Dicionary of extra attributes to pass allong with asset
        self.extra_attrs = {}

        # Determine the difference between the first and the second frame when
        # detecting a slate. The lover the value the more sensitive it to changes
        self.slate_threshold = 0.2

    @property
    def type(self):
        return self.__class__.__name__

    @property
    def path(self):
        return Path(self._path)

    @property
    def version(self):
        """
        Try to determine file version base on differnt regex paterns
        """
        for p in self.version_patterns:
            result = re.search(p, str(self.base_name))
            if result is not None:
                # log.debug('Regex version result: %s', result.group('version_number'))
                version = int(result.group('version_number'))
                return version

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

    def fields_from_name(self, name_template):
        """
        Given a template like {shot}_{task} try to extract values from
        asset core_name
        returns: Dictionary of template name and name values
        """
        # Find non token delimeters such as '_', '.', '_v'
        # Everythin in between }...{
        delimeters = re.findall(r'}([a-zA-Z-_.]+){', name_template)
        # Find all {...} tokens
        tokens = re.findall(r'{([a-z]+)}', name_template)
        # Split name base on the template separator
        name_values = re.split(delimeters[0], self.core_name)

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
        cmd = [str(self._ffprobe), '-v', 'quiet', '-select_streams', 'v', '-show_streams', '-print_format', 'json', str(path)]

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

    def copy(self, dst):

        dst = Path(dst)

        if dst.exists():
            log.warning('Local file %s already exists' % dst.name)
            return

        if not dst.parent.exists():
            dst.parent.mkdir(parents=True)

        log.info('Copy %s to %s' % (self.path, dst))
        shutil.copy(str(self.path), str(dst))


class ImageSequence(Asset):
    """
    Repreresent any image or file sequence
    Do not instanciate directly. Use factory methods such as asset_from_path.
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
                raise BrokenSequenceError (
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
        first_frame=str(self.path) % self.start
        second_frame=str(self.path) % (self.start + 1)
        first_frame = first_frame.replace('\\', '/')
        second_frame = second_frame.replace('\\', '/')
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

        if debug: cmd.pop(2); cmd.pop(2)

        result = subprocess.check_output(cmd)
        frames = json.loads(result).get('frames', '')

        if frames and float(frames[0]['pkt_dts_time']) < 1:
            return True
        else:
            return False

    def copy(self, dst, start_offset=0, new_start_frame=None, override=False):
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
        # Copy sequence to the publish folder frame by frame
        # Alway start from frame 1001
        for i in range(0, dst_frame_count):
            new_frame = new_start_frame + i

            old_path = str(self.path) % old_frame
            new_path = str(dst) % new_frame

            new_path_p = Path(new_path)
            # Sckip frane if already exists
            if new_path_p.exists() and not override:
                skipped_frames.append(new_path_p)
                continue

            # Attemt to copy frame with system specific command
            # such as cp and xcopy. Fall back to shutil if fails
            try:
                utils.system_copy(old_path, new_path)
            except Exception as e:
                msg = (
                    'Unable to execute fast system copy. %s. '
                    'Default python shutil copy were used.' % e
                )
                warnings.add(msg)

                shutil.copy(old_path, new_path)

            # Print feedback to the console
            sys.stdout.write("\r[+] Done %d out of %d frames" % (i+1, dst_frame_count))
            sys.stdout.flush()

            old_frame += 1

        print # Empty line

        # Check all of the events happened during copying
        #
        if skipped_frames:
            log.warning(
                'Copy of some of the frames were skipped because they '
                'were alredy present in the target destination.'
            )
            # log.debug([i.name for i in skipped_frames])

        if warnings:
            log.warning('Some warning were raised during copying: ')
            for i, w in enumerate(warnings):
                log.warning('\t%02d: %s' % (i+1, w))

        new_sequence_asset = asset_from_path(new_path_p.parent)

        return new_sequence_asset

class LocalFile(Asset):
    """
    Represent a local file that wont encompassed by other
    more specific classes
    """

    def __init__(self, path):
        super(self.__class__, self).__init__(path)

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

    def has_slate(self):
        """
        Detect a slate on the first frame of the video
        by using scene change analysis ffprobe filter

        :returns: (bool) True is video has a slate
        """
        mov_path = str(self.path).replace('\\', '/')

        image_filter = (
            'movie={mov_path}, '
            'select=gt(scene\,{slate_threshold})'
        ).format(mov_path=mov_path, slate_threshold=self.slate_threshold)

        cmd = [
            str(self._ffprobe), '-show_frames', '-v', 'quiet', '-read_intervals',
            '%+#3', '-print_format', 'json', '-f', 'lavfi', image_filter
        ]

        if debug: cmd.pop(2); cmd.pop(2)

        result = subprocess.check_output(cmd)
        frames = json.loads(result).get('frames', '')

        if frames and float(frames[0]['pkt_dts_time']) < 1:
            return True
        else:
            return False
