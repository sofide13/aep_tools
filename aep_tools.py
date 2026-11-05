import os

__author__ = "Kouidri Sofiane"
__copyright__ = "Copyright (c) 2026 Kouidri Sofiane"

from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.PyQt.QtGui import QIcon


class AEPToolsPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.action_about = None
        self.dialog = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
        self.action = QAction(icon, "AEP Tools", self.iface.mainWindow())
        self.action.setToolTip("Outils de traitement réseau AEP")
        self.action.triggered.connect(self.show_dialog)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&AEP Tools", self.action)
        self.action_about = QAction("À propos - Kouidri Sofiane", self.iface.mainWindow())
        self.action_about.triggered.connect(self.show_about)
        self.iface.addPluginToMenu("&AEP Tools", self.action_about)

    def unload(self):
        self.iface.removePluginMenu("&AEP Tools", self.action)
        self.iface.removePluginMenu("&AEP Tools", self.action_about)
        self.iface.removeToolBarIcon(self.action)
        if self.dialog:
            self.dialog.close()

    def show_about(self):
        QMessageBox.information(
            self.iface.mainWindow(), "À propos - AEP Tools",
            "<h3>AEP Tools v2.1.5</h3>"
            "<p>Outils QGIS pour les réseaux d'eau potable : DEM, altitudes, "
            "classification de fiabilité, découpage par dénivelé.</p>"
            "<p><b>Développé par : Kouidri Sofiane</b><br>"
            "© 2026 Kouidri Sofiane<br>Contact : sofihydro@gmail.com</p>"
            "<p>Toute redistribution doit conserver cette mention d'auteur.</p>")

    def show_dialog(self):
        if self.dialog is None:
            from .aep_dialog import AEPDialog
            self.dialog = AEPDialog(self.iface, self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
