from asset import asset_from_path

asset = asset_from_path('/EEP/Tools/Settings/shotgun/bolden_kkdev/install/core/python/asset/tests/sample_files/lpk0000_plate_v001.abc')
print asset.fields_from_name('{shot}_{task}')
