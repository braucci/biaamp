"""
setup.py — Costruzione di BiaAmp.app con py2app (nessun Xcode necessario).

Non va invocato a mano: usa ./build.sh, che esegue prima gli autotest
del motore e garantisce una costruzione da zero.

© 2026 B. Raucci — Licenza MIT
"""
from setuptools import setup

from core import __version__, ID_BUNDLE, NOME_APP, URL_PROGETTO

OPZIONI = {
    "argv_emulation": False,
    "iconfile": "risorse/BiaAmp.icns",
    # Ogni file caricato con _risorsa() in app.py deve comparire qui
    # (lo verifica test/verifica_pyobjc.py).
    "resources": ["risorse/presa.js", "risorse/ponte.js"],
    "includes": ["core"],
    # Nessuna esclusione della libreria standard: i componenti interni di
    # PyObjC importano moduli (es. xml) che app.py non usa direttamente.
    "plist": {
        "CFBundleName": NOME_APP,
        "CFBundleDisplayName": NOME_APP,
        "CFBundleIdentifier": ID_BUNDLE,
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "LSMinimumSystemVersion": "14.0",
        "LSApplicationCategoryType": "public.app-category.music",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "© 2026 B. Raucci — Licenza MIT",
    },
}

setup(
    name=NOME_APP,
    version=__version__,
    description="Lettore per YouTube Music con interfaccia a deck in stile anni Novanta",
    author="B. Raucci",
    license="MIT",
    url=URL_PROGETTO,
    app=["app.py"],
    options={"py2app": OPZIONI},
    setup_requires=["py2app"],
)
