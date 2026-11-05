"""AEP Tools - Extension QGIS.

Auteur : Kouidri Sofiane - Copyright (c) 2026 Kouidri Sofiane
"""
__author__ = "Kouidri Sofiane"


def classFactory(iface):
    from .aep_tools import AEPToolsPlugin
    return AEPToolsPlugin(iface)
