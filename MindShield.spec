# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets/mindshield.ico', 'assets'),
        ('assets/mindshield-logo.png', 'assets'),
        ('assets/fonts/Orbitron-Bold.ttf', 'assets/fonts'),
        ('assets/fonts/OFL.txt', 'assets/fonts'),
    ],
    hiddenimports=[
        'sqlcipher3.dbapi2',
        'win32crypt',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MindShield',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    icon='assets/mindshield.ico',
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MindShield',
)
