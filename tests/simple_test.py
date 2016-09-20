from asset import asset_from_path
import sys

if sys.platform == 'darwin':
    asset = asset_from_path('/EEP/Tools/Settings/shotgun/bolden_kkdev/install/core/python/asset/tests/sample_files/slated_video.mov')
elif sys.platform == 'win32':
    asset = asset_from_path('//gin.eep.com/Tools/Settings/shotgun/bolden_kkdev/install/core/python/asset/tests/sample_files/slated_video.mov')

print asset.generate_thumbnail()
asset.remove_tmp_files()
