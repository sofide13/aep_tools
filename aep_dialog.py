"""
AEP Tools — Boîte de dialogue principale (5 onglets)
"""
import os
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget,
    QLabel, QLineEdit, QPushButton, QTextEdit, QProgressBar,
    QGroupBox, QFormLayout, QSpinBox, QDoubleSpinBox,
    QComboBox, QFileDialog, QSizePolicy, QFrame,
    QScrollArea, QStackedWidget,
)
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QSize
from qgis.PyQt.QtGui import QFont, QColor, QTextCursor


# ═══════════════════════════════════════════════════════════════════════
#  WORKER BASE — exécution des scripts dans un thread séparé
# ═══════════════════════════════════════════════════════════════════════

class Worker(QThread):
    log_signal      = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, script_module, params):
        super().__init__()
        self.script_module = script_module
        self.params = params
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            self.script_module.run(
                self.params,
                log_fn=lambda msg: self.log_signal.emit(str(msg)),
                progress_fn=lambda pct: self.progress_signal.emit(int(pct)),
                stop_fn=lambda: self._stop,
            )
            if not self._stop:
                self.finished_signal.emit(True, "Terminé avec succès ✅")
        except Exception as e:
            self.finished_signal.emit(False, f"❌ Erreur : {e}")


# ═══════════════════════════════════════════════════════════════════════
#  FEUILLE DE STYLE — corrigée
# ═══════════════════════════════════════════════════════════════════════

STYLE_SHEET = """
QDialog {
    background-color: #f5f5f5;
}
QTabWidget::pane {
    border: 1px solid #c0c0c0;
    background: #ffffff;
    border-radius: 4px;
}
QTabBar {
    alignment: left;
}
QTabBar::tab {
    background: #e0e0e0;
    color: #333333;
    padding: 5px 6px;
    margin-right: 1px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    font-weight: bold;
    font-size: 9px;
    min-width: 0px;
    max-width: 200px;
    width: auto;
}
QTabBar::tab:selected {
    background: #1565C0;
    color: white;
}
QTabBar::tab:hover:!selected {
    background: #BBDEFB;
    color: #1565C0;
}
QGroupBox {
    font-weight: bold;
    font-size: 12px;
    color: #1565C0;
    border: 1px solid #90CAF9;
    border-radius: 5px;
    margin-top: 14px;
    padding-top: 10px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    top: 0px;
    padding: 0 5px;
    background: white;
}
QPushButton#run_btn {
    background-color: #1565C0;
    color: white;
    border: none;
    border-radius: 5px;
    padding: 8px 20px;
    font-weight: bold;
    font-size: 12px;
    min-width: 130px;
}
QPushButton#run_btn:hover    { background-color: #1976D2; }
QPushButton#run_btn:pressed  { background-color: #0D47A1; }
QPushButton#run_btn:disabled { background-color: #90A4AE; }
QPushButton#stop_btn {
    background-color: #C62828;
    color: white;
    border: none;
    border-radius: 5px;
    padding: 8px 16px;
    font-weight: bold;
    font-size: 12px;
    min-width: 100px;
}
QPushButton#stop_btn:hover    { background-color: #D32F2F; }
QPushButton#stop_btn:disabled { background-color: #90A4AE; }
QPushButton#browse_btn {
    background-color: #E3F2FD;
    color: #1565C0;
    border: 1px solid #90CAF9;
    border-radius: 4px;
    padding: 4px 10px;
    font-size: 11px;
    min-width: 105px;
}
QPushButton#browse_btn:hover { background-color: #BBDEFB; }
QLineEdit {
    border: 1px solid #c0c0c0;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 11px;
    background: white;
    min-height: 22px;
}
QLineEdit:focus { border: 1.5px solid #1565C0; }
QProgressBar {
    border: 1px solid #c0c0c0;
    border-radius: 4px;
    text-align: center;
    background: #f0f0f0;
    height: 18px;
    font-weight: bold;
    font-size: 11px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #1565C0, stop:1 #42A5F5);
    border-radius: 3px;
}
QTextEdit#log_box {
    background: #1a1a2e;
    color: #e0e0e0;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 11px;
    border: 1px solid #444;
    border-radius: 4px;
}
QLabel#step_label {
    font-size: 13px;
    font-weight: bold;
    color: #1565C0;
}
QLabel#desc_label {
    font-size: 11px;
    color: #555;
    padding: 2px 0;
}
QComboBox {
    font-size: 11px;
    padding: 3px 6px;
    border: 1px solid #c0c0c0;
    border-radius: 4px;
    background: white;
    min-height: 22px;
}
QSpinBox, QDoubleSpinBox {
    font-size: 11px;
    padding: 3px 6px;
    border: 1px solid #c0c0c0;
    border-radius: 4px;
    background: white;
    min-height: 22px;
}
"""


# ═══════════════════════════════════════════════════════════════════════
#  WIDGETS UTILITAIRES
# ═══════════════════════════════════════════════════════════════════════

def _file_row(label_text, placeholder="", is_dir=False, filter_str=""):
    row_widget = QWidget()
    layout = QHBoxLayout(row_widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    btn = QPushButton("📂  Parcourir")
    btn.setObjectName("browse_btn")
    btn.setFixedWidth(115)
    if is_dir:
        btn.clicked.connect(lambda: _browse_dir(edit))
    else:
        btn.clicked.connect(lambda: _browse_file(edit, filter_str))
    layout.addWidget(edit)
    layout.addWidget(btn)
    return row_widget, edit


def _browse_file(edit, filter_str=""):
    path, _ = QFileDialog.getOpenFileName(
        None, "Sélectionner un fichier", edit.text() or "", filter_str or "Tous (*)"
    )
    if path:
        edit.setText(path)


def _browse_dir(edit):
    path = QFileDialog.getExistingDirectory(
        None, "Sélectionner un dossier", edit.text() or ""
    )
    if path:
        edit.setText(path)


def _save_file_row(label_text, placeholder="", filter_str=""):
    row_widget = QWidget()
    layout = QHBoxLayout(row_widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    btn = QPushButton("💾  Enregistrer")
    btn.setObjectName("browse_btn")
    btn.setFixedWidth(115)
    btn.clicked.connect(lambda: _save_file(edit, filter_str))
    layout.addWidget(edit)
    layout.addWidget(btn)
    return row_widget, edit


def _save_file(edit, filter_str=""):
    path, _ = QFileDialog.getSaveFileName(
        None, "Enregistrer sous", edit.text() or "", filter_str or "Tous (*)"
    )
    if path:
        edit.setText(path)


def _make_log_box():
    log = QTextEdit()
    log.setObjectName("log_box")
    log.setReadOnly(True)
    log.setMinimumHeight(130)
    return log


def _append_log(log_box, msg, color=None):
    cursor = log_box.textCursor()
    cursor.movePosition(QTextCursor.End)
    log_box.setTextCursor(cursor)
    if color:
        log_box.setTextColor(QColor(color))
    log_box.insertPlainText(msg + "\n")
    log_box.setTextColor(QColor("#e0e0e0"))
    log_box.ensureCursorVisible()


def _make_progress_bar():
    pb = QProgressBar()
    pb.setRange(0, 100)
    pb.setValue(0)
    pb.setFormat("%p%")
    return pb


def _make_run_stop_row(run_label="▶  Lancer"):
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 4, 0, 4)
    layout.setSpacing(8)
    run_btn   = QPushButton(run_label)
    run_btn.setObjectName("run_btn")
    stop_btn  = QPushButton("⬛  Arrêter")
    stop_btn.setObjectName("stop_btn")
    stop_btn.setEnabled(False)
    clear_btn = QPushButton("🗑  Effacer log")
    clear_btn.setObjectName("browse_btn")
    layout.addStretch()
    layout.addWidget(clear_btn)
    layout.addWidget(stop_btn)
    layout.addWidget(run_btn)
    return row, run_btn, stop_btn, clear_btn


# ═══════════════════════════════════════════════════════════════════════
#  CLASSE DE BASE DES ONGLETS
# ═══════════════════════════════════════════════════════════════════════

class TabBase(QWidget):
    """Onglet générique avec ScrollArea pour éviter tout débordement."""

    def __init__(self, title, description, icon="🔧"):
        super().__init__()
        self.worker = None
        self._build_ui(title, description, icon)

    def _build_ui(self, title, description, icon):
        # Layout extérieur (pas de marges, contient uniquement le scroll)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        inner = QWidget()
        inner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        main_layout = QVBoxLayout(inner)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(10)

        # ── En-tête coloré ──────────────────────────────────────────
        header = QWidget()
        header.setStyleSheet("background:#E3F2FD; border-radius:5px;")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(10, 8, 10, 8)
        hl.setSpacing(10)
        icon_lbl = QLabel(icon)
        icon_lbl.setFont(QFont("", 20))
        icon_lbl.setFixedWidth(36)
        icon_lbl.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("step_label")
        title_lbl.setWordWrap(True)
        desc_lbl = QLabel(description)
        desc_lbl.setObjectName("desc_label")
        desc_lbl.setWordWrap(True)
        text_col.addWidget(title_lbl)
        text_col.addWidget(desc_lbl)
        hl.addWidget(icon_lbl)
        hl.addLayout(text_col, 1)
        main_layout.addWidget(header)

        # ── Séparateur ───────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#c0c0c0;")
        main_layout.addWidget(sep)

        # ── Paramètres ───────────────────────────────────────────────
        self.params_group = QGroupBox("Paramètres")
        self.form = QFormLayout(self.params_group)
        self.form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.form.setSpacing(10)
        self.form.setContentsMargins(10, 8, 10, 10)
        main_layout.addWidget(self.params_group)

        self._fill_params()

        # ── Progression ───────────────────────────────────────────────
        self.progress = _make_progress_bar()
        main_layout.addWidget(self.progress)

        # ── Boutons ───────────────────────────────────────────────────
        short_title = title.split("—")[-1].strip()
        btn_row, self.run_btn, self.stop_btn, self.clear_btn = \
            _make_run_stop_row(f"▶  {short_title}")
        main_layout.addWidget(btn_row)

        # ── Journal ───────────────────────────────────────────────────
        log_group = QGroupBox("Journal d'exécution")
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(8, 8, 8, 8)
        self.log_box = _make_log_box()
        log_layout.addWidget(self.log_box)
        main_layout.addWidget(log_group, 1)

        # ── Connexions ────────────────────────────────────────────────
        self.run_btn.clicked.connect(self._on_run)
        self.stop_btn.clicked.connect(self._on_stop)
        self.clear_btn.clicked.connect(self.log_box.clear)

        scroll.setWidget(inner)
        outer.addWidget(scroll)

    def _fill_params(self):
        pass

    def _get_params(self):
        return {}

    def _get_script_module(self):
        raise NotImplementedError

    # Clés dont la valeur peut être vide (champs optionnels)
    _OPTIONAL_KEYS = {"gps_csv", "col_x", "col_y"}

    def _on_run(self):
        params = self._get_params()
        for key, val in params.items():
            if key in self._OPTIONAL_KEYS:
                continue
            if key.endswith(("_csv", "_tif", "_shp")) or key == "shp_path":
                if not val:
                    _append_log(self.log_box,
                                f"⚠  Veuillez renseigner : {key}", "#FF9800")
                    return
        module = self._get_script_module()
        self.worker = Worker(module, params)
        self.worker.log_signal.connect(lambda m: _append_log(self.log_box, m))
        self.worker.progress_signal.connect(self.progress.setValue)
        self.worker.finished_signal.connect(self._on_finished)
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress.setValue(0)
        self.worker.start()

    def _on_stop(self):
        if self.worker:
            self.worker.stop()
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def _on_finished(self, success, msg):
        color = "#4CAF50" if success else "#F44336"
        _append_log(self.log_box, f"\n{msg}", color)
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if not success:
            self.progress.setValue(0)


# ═══════════════════════════════════════════════════════════════════════
#  ONGLETS INDIVIDUELS
# ═══════════════════════════════════════════════════════════════════════

class Tab00_DEM(TabBase):
    def __init__(self):
        super().__init__(
            title='Etape 2 - Telecharger DEM',
            description=(
                'Telecharge le DEM Copernicus GLO-30 (30m). '
                'Source officielle ESA, gratuite, sans inscription. '
                'Prerequis de l etape 3 (Altitudes) et etape 6 (Extraction CSV).'
            ),
            icon=chr(0x1f30d),
        )


    def _fill_params(self):
        lbl_source = QLabel('Copernicus GLO-30  —  30 m  —  gratuit, sans clé')
        lbl_source.setStyleSheet(
            'font-size:11px; font-weight:bold; color:#1B5E20;'
            'background:#E8F5E9; border-radius:4px; padding:4px 8px;'
        )
        self.form.addRow('Source DEM :', lbl_source)

        self.combo_mode = QComboBox()
        self.combo_mode.addItem('preconfigure - Region Ouest-Algerien', 'preconfigure')
        self.combo_mode.addItem('auto         - Depuis le CSV AEP',     'auto')
        self.combo_mode.addItem('manuel       - Coordonnees manuelles', 'manuel')
        self.combo_mode.currentIndexChanged.connect(self._on_mode_changed)
        self.form.addRow('Zone bbox :', self.combo_mode)

        row, self.e_input_csv = _file_row('', 'CSV vertices', filter_str='CSV (*.csv)')
        self.form.addRow('CSV AEP :', row)

        self.spin_margin = QDoubleSpinBox()
        self.spin_margin.setRange(0.01, 2.0)
        self.spin_margin.setValue(0.1)
        self.spin_margin.setSuffix(' deg')
        self.spin_margin.setFixedWidth(120)
        self.form.addRow('Marge bbox :', self.spin_margin)

        row2, self.e_output_dem = _save_file_row('', 'dem.tif', filter_str='GeoTIFF (*.tif *.tiff)')
        self.form.addRow('DEM sortie :', row2)

        row3, self.e_output_dir = _file_row('', 'Dossier temporaire', is_dir=True)
        self.form.addRow('Dossier temp :', row3)

        self._on_mode_changed(0)

    def _set_section_visible(self, widgets, visible):
        for w in widgets:
            w.setVisible(visible)
            lf = self.form.labelForField(w)
            if lf:
                lf.setVisible(visible)

    def _on_mode_changed(self, idx):
        is_auto = (self.combo_mode.currentData() == 'auto')
        csv_w = self.e_input_csv.parent()
        self._set_section_visible([csv_w, self.spin_margin], is_auto)

    def _get_params(self):
        output_dem = self.e_output_dem.text().strip()
        output_dir = self.e_output_dir.text().strip() or os.path.dirname(output_dem) or '.'
        return {
            'input_csv':   self.e_input_csv.text().strip(),
            'output_dem':  output_dem,
            'output_dir':  output_dir,
            'dem_source':  'copernicus',
            'mode_bbox':   self.combo_mode.currentData(),
            'bbox_margin': self.spin_margin.value(),
        }

    def _get_script_module(self):
        from .scripts import dem_download
        return dem_download

class Tab01_Export(TabBase):
    def __init__(self):
        super().__init__(
            title="Étape 1 — Exporter CSV",
            description=(
                "Extrait les vertices du shapefile AEP et les exporte en CSV "
                "(lat, lon, name) avec densification optionnelle. "
                "Résultat utilisable par GPS Visualizer ou directement à l'étape 2."
            ),
            icon="📤",
        )

    def _fill_params(self):
        row, self.e_shp = _file_row(
            "", "Chemin vers AEP.shp", filter_str="Shapefile (*.shp)"
        )
        # Quand le SHP change, remplir automatiquement le CSV de sortie
        self.e_shp.textChanged.connect(self._auto_output_csv)
        self.form.addRow("Shapefile AEP :", row)

        row2, self.e_output_csv = _save_file_row(
            "", "vertices_gps.csv  (rempli automatiquement)",
            filter_str="CSV (*.csv)"
        )
        self.form.addRow("CSV sortie :", row2)

        self.spin_densify = QSpinBox()
        self.spin_densify.setRange(0, 100)
        self.spin_densify.setValue(5)
        self.spin_densify.setSuffix(" m  (0 = désactivé)")
        self.form.addRow("Densification :", self.spin_densify)

    def _auto_output_csv(self, shp_path):
        """Propose automatiquement vertices_gps.csv dans le même dossier que le SHP."""
        shp_path = shp_path.strip()
        if shp_path and not self.e_output_csv.text().strip():
            folder = os.path.dirname(shp_path)
            self.e_output_csv.setText(os.path.join(folder, "vertices_gps.csv"))

    def _build_ui(self, title, description, icon):
        super()._build_ui(title, description, icon)
        # Bouton "Lancer" plus large pour cet onglet
        self.run_btn.setMinimumWidth(220)

    def _get_params(self):
        return {
            "shp_path":           self.e_shp.text().strip(),
            "output_csv":         self.e_output_csv.text().strip(),
            "densify_interval_m": self.spin_densify.value(),
        }

    def _get_script_module(self):
        from .scripts import export_csv
        return export_csv


class Tab02_Altitudes(TabBase):
    def __init__(self):
        super().__init__(
            title="Étape 3 — Altitudes DEM",
            description=(
                "Extrait l'altitude de chaque vertex depuis un DEM local."
                " V2.1 : validé sur 4 203 pts GPS (Algérie) — RMSE 1.43 m, LE90 2.25 m,"
                " 96.6 % dans ±3 m (APS). Classification automatique de fiabilité par pente"
                " (FIABLE/ACCEPTABLE/INCERTAINE/NON_FIABLE)."
            ),
            icon="\u26f0\ufe0f",
        )

    def _fill_params(self):
        # CSV vertices
        row, self.e_input_csv = _file_row(
            "", "CSV vertices (sortie etape 1)", filter_str="CSV (*.csv)"
        )
        self.e_input_csv.textChanged.connect(self._auto_output_csv)
        self.form.addRow("CSV entree :", row)

        # DEM
        row2, self.e_dem = _file_row(
            "", "DEM GeoTIFF — Copernicus GLO-30, SRTM, CopDEM-GLO-10...",
            filter_str="GeoTIFF (*.tif *.tiff)"
        )
        self.form.addRow("DEM raster :", row2)

        # CSV sortie
        row3, self.e_output_csv = _save_file_row(
            "", "vertices_avec_altitude.csv  (rempli automatiquement)",
            filter_str="CSV (*.csv)"
        )
        self.form.addRow("CSV sortie :", row3)

        # Methode
        self.combo_methode = QComboBox()
        self.combo_methode.addItem(
            "bilinear — Interpolation bilineaire (precis, recommande)", "bilinear"
        )
        self.combo_methode.addItem(
            "nearest  — Pixel le plus proche (rapide)", "nearest"
        )
        self.form.addRow("Methode :", self.combo_methode)

        # Separateur optionnel
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#c0c0c0;")
        self.form.addRow("", sep)

        # GPS calibration (optionnel)
        row4, self.e_gps_csv = _file_row(
            "", "CSV leves GPS terrain (optionnel — laisser vide pour ignorer)",
            filter_str="CSV (*.csv) ;; Excel (*.xlsx)"
        )
        self.e_gps_csv.textChanged.connect(self._on_gps_changed)
        self.form.addRow("GPS calibration :", row4)

        # Méthode de calibration GPS
        self.combo_cal_methode = QComboBox()
        self.combo_cal_methode.addItem(
            "IDW        — Pondération inverse distance  (recommandé)", "idw"
        )
        self.combo_cal_methode.addItem(
            "Regression — Correction biais global par régression linéaire", "regression"
        )
        self.combo_cal_methode.addItem(
            "Biais      — Décalage constant (moyenne des résidus GPS)", "biais"
        )
        self.lbl_cal_methode = self.form.labelForField   # placeholder
        self.form.addRow("Méthode calibration :", self.combo_cal_methode)
        # Label stocké pour le masquer si pas de GPS
        self._cal_widgets = [self.combo_cal_methode]

        # EPSG GPS
        self.spin_epsg = QSpinBox()
        self.spin_epsg.setRange(1, 99999)
        self.spin_epsg.setValue(32630)
        self.spin_epsg.setFixedWidth(100)
        self.form.addRow("EPSG GPS :", self.spin_epsg)
        self._cal_widgets += [self.spin_epsg]

        # Rayon IDW
        self.spin_radius = QDoubleSpinBox()
        self.spin_radius.setRange(50, 5000)
        self.spin_radius.setSingleStep(50)
        self.spin_radius.setValue(500)
        self.spin_radius.setSuffix(" m")
        self.spin_radius.setFixedWidth(120)
        self.form.addRow("Rayon GPS :", self.spin_radius)
        self._cal_widgets += [self.spin_radius]

        # Masquer par défaut (champ GPS vide)
        self._set_cal_visible(False)

        # Info methode
        info = QLabel(
            "── Résultats validés V2.1 (N=4 203 pts GPS, Algérie) ──\n"
            "  ME = +0.29 m  |  RMSE = 1.43 m  |  LE90 = 2.25 m\n"
            "  96.6 % dans ±3 m ✅  |  86.9 % dans ±2 m\n"
            "  Biais normalísé ME/σ = 0.21  (non-Gaussien : LE90 recommandé)"
        )
        info.setWordWrap(True)
        info.setStyleSheet(
            "color:#1565C0; font-size:10px; font-family:monospace;"
            "background:#E3F2FD; border-radius:4px; padding:6px;"
        )
        self.form.addRow("", info)

    def _set_cal_visible(self, visible):
        for w in self._cal_widgets:
            w.setVisible(visible)
            lbl = self.form.labelForField(w)
            if lbl:
                lbl.setVisible(visible)

    def _on_gps_changed(self, text):
        self._set_cal_visible(bool(text.strip()))

    def _auto_output_csv(self, csv_path):
        csv_path = csv_path.strip()
        if csv_path and not self.e_output_csv.text().strip():
            folder = os.path.dirname(csv_path)
            self.e_output_csv.setText(
                os.path.join(folder, "vertices_avec_altitude.csv")
            )

    def _get_params(self):
        return {
            "input_csv":     self.e_input_csv.text().strip(),
            "dem_tif":       self.e_dem.text().strip(),
            "output_csv":    self.e_output_csv.text().strip(),
            "methode":       self.combo_methode.currentData(),
            "gps_csv":       self.e_gps_csv.text().strip(),
            "methode_cal":   self.combo_cal_methode.currentData(),
            "gps_epsg":      self.spin_epsg.value(),
            "radius_gps_m":  self.spin_radius.value(),
        }

    def _on_run(self):
        p = self._get_params()
        if not p["input_csv"]:
            _append_log(self.log_box, "[!] Renseigner le CSV d'entree.", "#FF9800"); return
        if not p["dem_tif"]:
            _append_log(self.log_box, "[!] Renseigner le DEM raster.", "#FF9800"); return
        if not p["output_csv"]:
            _append_log(self.log_box, "[!] Renseigner le CSV de sortie.", "#FF9800"); return
        super()._on_run()

    def _get_script_module(self):
        from .scripts import altitudes
        return altitudes



class Tab03_Decoupage(TabBase):
    def __init__(self):
        super().__init__(
            title="Étape 4 — Découpage dénivelé",
            description=(
                "Découpe chaque entité AEP chaque fois que l'altitude varie du seuil "
                "choisi (défaut 10 m). Entrée : CSV avec altitudes (sortie étape 3) "
                "+ fichier source à découper (SHP ou GeoJSON). "
                "Produit un GeoJSON de segments et un GeoJSON de points d'accrochage interpolés."
            ),
            icon="✂️",
        )

    def _fill_params(self):
        row, self.e_input_csv = _file_row(
            "", "vertices_avec_altitudes.csv (sortie étape 3)", filter_str="CSV (*.csv)"
        )
        self.e_input_csv.textChanged.connect(self._auto_outputs)
        self.form.addRow("CSV altitudes :", row)
        row2, self.e_geojson = _file_row(
            "", "Fichier source à découper (SHP ou GeoJSON)",
            filter_str="GeoJSON (*.geojson *.json);;Shapefile (*.shp)"
        )
        self.form.addRow("Fichier source :", row2)
        row3, self.e_out_lines = _save_file_row(
            "", "<nom_couche>_segments_10m.geojson  (rempli automatiquement)",
            filter_str="GeoJSON (*.geojson)"
        )
        self.form.addRow("Segments sortie :", row3)
        row4, self.e_out_points = _save_file_row(
            "", "<nom_couche>_points_accrochage.geojson  (rempli automatiquement)",
            filter_str="GeoJSON (*.geojson)"
        )
        self.form.addRow("Points sortie :", row4)
        self.spin_denivele = QDoubleSpinBox()
        self.spin_denivele.setRange(0.5, 100.0)
        self.spin_denivele.setSingleStep(0.5)
        self.spin_denivele.setValue(10.0)
        self.spin_denivele.setSuffix(" m")
        self.form.addRow("Seuil dénivelé :", self.spin_denivele)

    def _auto_outputs(self, csv_path):
        """Déduit les noms de sortie depuis le nom du CSV source."""
        csv_path = csv_path.strip()
        if not csv_path:
            return
        folder = os.path.dirname(csv_path)
        base = os.path.splitext(os.path.basename(csv_path))[0]
        # Supprime le suffixe _avec_altitude s'il est présent
        base = base.replace("_avec_altitude", "").replace("vertices_", "").strip("_") or base
        if not self.e_out_lines.text().strip():
            self.e_out_lines.setText(
                os.path.join(folder, f"{base}_segments_10m.geojson")
            )
        if not self.e_out_points.text().strip():
            self.e_out_points.setText(
                os.path.join(folder, f"{base}_points_accrochage.geojson")
            )

    def _get_params(self):
        return {
            "input_csv":     self.e_input_csv.text().strip(),
            "input_geojson": self.e_geojson.text().strip(),
            "output_lines":  self.e_out_lines.text().strip(),
            "output_points": self.e_out_points.text().strip(),
            "denivele_m":    self.spin_denivele.value(),
        }

    def _on_run(self):
        p = self._get_params()
        if not p["input_csv"]:
            _append_log(self.log_box, "⚠  Renseigner le CSV altitudes.", "#FF9800"); return
        if not p["output_lines"]:
            _append_log(self.log_box, "⚠  Renseigner le fichier segments.", "#FF9800"); return
        if not p["output_points"]:
            _append_log(self.log_box, "⚠  Renseigner le fichier points.", "#FF9800"); return
        super()._on_run()

    def _build_ui(self, title, description, icon):
        super()._build_ui(title, description, icon)
        # Bouton "Lancer" plus large pour cet onglet
        self.run_btn.setMinimumWidth(240)

    def _get_script_module(self):
        from .scripts import decoupage
        return decoupage


class Tab04_ExtracterAlt(TabBase):
    def __init__(self):
        super().__init__(
            title="Étape 5 — Altitude → Couches",
            description=(
                "Ajoute l'attribut altitude à toutes les couches vecteur SHP du projet "
                "QGIS en cherchant le point CSV le plus proche. "
                "Entrée : CSV avec altitudes (sortie étape 3). "
                "Crée de nouveaux SHP avec les champs altitude et dist_m."
            ),
            icon="📍",
        )

    def _fill_params(self):
        row, self.e_csv_alt = _file_row(
            "", "vertices_avec_altitudes.csv (sortie étape 3)", filter_str="CSV (*.csv)"
        )
        self.form.addRow("CSV altitudes :", row)
        row2, self.e_out_dir = _file_row(
            "", "Dossier de sortie pour les SHP enrichis", is_dir=True
        )
        self.form.addRow("Dossier sortie :", row2)
        info = QLabel(
            "ℹ  Toutes les couches SHP du projet QGIS actif seront traitées.\n"
            "   Chaque couche produit un nouveau SHP avec les champs altitude et dist_m."
        )
        info.setWordWrap(True)
        info.setStyleSheet(
            "color: #1565C0; font-size: 11px; "
            "background: #E3F2FD; border-radius: 4px; padding: 6px;"
        )
        self.form.addRow("", info)

    def _get_params(self):
        return {
            "csv_alt": self.e_csv_alt.text().strip(),
            "out_dir": self.e_out_dir.text().strip(),
        }

    def _on_run(self):
        p = self._get_params()
        if not p["csv_alt"]:
            _append_log(self.log_box, "⚠  Renseigner le CSV altitudes.", "#FF9800"); return
        if not p["out_dir"]:
            _append_log(self.log_box, "⚠  Renseigner le dossier de sortie.", "#FF9800"); return
        super()._on_run()

    def _get_script_module(self):
        from .scripts import extracter_alt
        return extracter_alt


# ═══════════════════════════════════════════════════════════════════════
#  ONGLET ACCUEIL
# ═══════════════════════════════════════════════════════════════════════


class Tab05_ExtractCSV(TabBase):
    """
    Onglet 6 : Extraction d'altitudes depuis DEM pour un CSV quelconque.
    L'utilisateur fournit un CSV avec colonnes X/Y ou Lon/Lat.
    Le script retourne le meme CSV avec une colonne altitude_dem ajoutee.
    """
    def __init__(self):
        super().__init__(
            title='Etape 6 - Altitudes depuis CSV',
            description=(
                'Ajoutez des altitudes a n\'importe quel fichier CSV contenant des coordonnees. '
                'Detection automatique des colonnes X/Y ou Longitude/Latitude. '
                'Supporte les systemes UTM, WGS84 et tout EPSG valide.'
            ),
            icon='\U0001f4cd',
        )

    def _fill_params(self):
        # ── CSV entree ─────────────────────────────────────────────────────
        lbl_in = QLabel('Fichier CSV avec coordonnees (entree) :')
        lbl_in.setStyleSheet('font-weight:bold; font-size:10px;')
        self.form.addRow(lbl_in)

        row, self.e_input_csv = _file_row(
            '', 'Choisir le fichier CSV...', filter_str='CSV (*.csv *.txt)')
        self.form.addRow('CSV entree :', row)

        # Info colonnes
        lbl_hint = QLabel(
            'Colonnes detectees automatiquement : X, Y, LON, LAT, LONGITUDE, LATITUDE\n'
            'Ou specifiez manuellement ci-dessous (laisser vide = auto)')
        lbl_hint.setStyleSheet('color:#555; font-size:9px;')
        self.form.addRow('', lbl_hint)

        # Colonnes manuelles
        row_cols = QHBoxLayout()
        self.e_col_x = QLineEdit()
        self.e_col_x.setPlaceholderText('Colonne X (auto)')
        self.e_col_x.setFixedWidth(140)
        self.e_col_y = QLineEdit()
        self.e_col_y.setPlaceholderText('Colonne Y (auto)')
        self.e_col_y.setFixedWidth(140)
        row_cols.addWidget(QLabel('X :'))
        row_cols.addWidget(self.e_col_x)
        row_cols.addWidget(QLabel('  Y :'))
        row_cols.addWidget(self.e_col_y)
        row_cols.addStretch()
        col_widget = QWidget()
        col_widget.setLayout(row_cols)
        self.form.addRow('Colonnes :', col_widget)

        # ── EPSG ──────────────────────────────────────────────────────────
        self.spin_epsg = QSpinBox()
        self.spin_epsg.setRange(1024, 99999)
        self.spin_epsg.setValue(32630)
        self.spin_epsg.setFixedWidth(120)
        self.spin_epsg.setToolTip(
            'Code EPSG du systeme de coordonnees du CSV\n'
            '  32630 = UTM Zone 30N (Algerie Ouest, recommande)\n'
            '  32631 = UTM Zone 31N (Algerie Est)\n'
            '   4326 = WGS84 Longitude/Latitude (GPS)')
        self.form.addRow('EPSG CSV :', self.spin_epsg)

        # Liens EPSG rapides
        lbl_epsg = QLabel(
            '32630=UTM-30N(Oran/Tlemcen)   32631=UTM-31N(Alger/Constantine)   4326=WGS84(GPS)')
        lbl_epsg.setStyleSheet('color:#1565C0; font-size:9px;')
        self.form.addRow('', lbl_epsg)

        # ── DEM ───────────────────────────────────────────────────────────
        lbl_dem = QLabel('DEM (GeoTIFF telecharge a l\'etape 2) :')
        lbl_dem.setStyleSheet('font-weight:bold; font-size:10px;')
        self.form.addRow(lbl_dem)

        row2, self.e_dem = _file_row(
            '', 'Choisir le fichier DEM .tif...', filter_str='GeoTIFF (*.tif *.tiff)')
        self.form.addRow('Fichier DEM :', row2)

        # ── CSV sortie ────────────────────────────────────────────────────
        lbl_out = QLabel('Fichier CSV de sortie (avec colonne altitude_dem) :')
        lbl_out.setStyleSheet('font-weight:bold; font-size:10px;')
        self.form.addRow(lbl_out)

        row3, self.e_output_csv = _save_file_row(
            '', 'points_avec_altitude.csv', filter_str='CSV (*.csv)')
        self.form.addRow('CSV sortie :', row3)

        lbl_out2 = QLabel('Laisser vide = nom automatique (_avec_altitude.csv)')
        lbl_out2.setStyleSheet('color:#555; font-size:9px;')
        self.form.addRow('', lbl_out2)

        # ── Valeur NoData ─────────────────────────────────────────────────
        self.spin_nodata = QDoubleSpinBox()
        self.spin_nodata.setRange(-99999, 0)
        self.spin_nodata.setValue(-9999)
        self.spin_nodata.setDecimals(0)
        self.spin_nodata.setFixedWidth(120)
        self.spin_nodata.setToolTip('Valeur ecrite si le point est hors couverture du DEM')
        self.form.addRow('Valeur hors DEM :', self.spin_nodata)

        # ── Exemple de format CSV ────────────────────────────────────────
        lbl_ex = QLabel(
            'Format CSV accepte (avec separateur ";" ou ",") :\n'
            '  ID;X;Y;...autres colonnes...\n'
            '  1;200000;3900000;...\n'
            '  ou : ID,LONGITUDE,LATITUDE,...\n'
            '       1,-1.342,34.856,...'
        )
        lbl_ex.setStyleSheet(
            'font-size:9px; font-family:monospace; padding:5px;'
            'border-radius:4px; background:#E8F5E9; color:#1B5E20;')
        self.form.addRow('', lbl_ex)

    def _get_params(self):
        return {
            'input_csv':  self.e_input_csv.text().strip(),
            'output_csv': self.e_output_csv.text().strip(),
            'dem_path':   self.e_dem.text().strip(),
            'epsg_csv':   self.spin_epsg.value(),
            'col_x':      self.e_col_x.text().strip(),
            'col_y':      self.e_col_y.text().strip(),
            'nodata_val': self.spin_nodata.value(),
        }

    def _get_script_module(self):
        from .scripts import extract_altitudes_csv
        return extract_altitudes_csv


class TabAccueil(QWidget):
    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # Titre
        title = QLabel("🗺️  AEP Tools v2.1 — Traitement du réseau d'eau potable\n"
                       "Développé par Kouidri Sofiane")
        title.setFont(QFont("", 14, QFont.Bold))
        title.setStyleSheet("color: #1565C0; padding: 4px 0 8px 0;")
        title.setWordWrap(True)
        layout.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("background:#1565C0; max-height:2px;")
        layout.addWidget(sep)

        # ── Carte résultats validés V2.1 ─────────────────────────────
        stats_card = QFrame()
        stats_card.setStyleSheet(
            "QFrame { background: #E8F5E9; border: 1px solid #A5D6A7; border-radius: 6px; }"
        )
        sc_layout = QVBoxLayout(stats_card)
        sc_layout.setContentsMargins(12, 10, 12, 10)
        sc_layout.setSpacing(4)
        sc_title = QLabel("✅  Précision validée — Copernicus GLO-30 / Algérie (V2.1)")
        sc_title.setStyleSheet("color:#1B5E20; font-weight:bold; font-size:11px;")
        sc_layout.addWidget(sc_title)
        sc_stats = QLabel(
            "  N = 4 203 pts GPS  |  ME = +0.29 m  |  RMSE = 1.43 m  |  LE90 = 2.25 m\n"
            "  96.6 % dans ±3 m (seuil APS ±0.3 bar)  |  86.9 % dans ±2 m\n"
            "  Spec. Copernicus LE90 ≤ 4.00 m → DÉPASSÉ  |  Meilleur que SRTM (9.0 m) et Hawker (4.2 m)"
        )
        sc_stats.setStyleSheet("color:#2E7D32; font-size:10px; font-family:monospace;")
        sc_layout.addWidget(sc_stats)
        layout.addWidget(stats_card)

        steps = [
            ("📤", "Étape 1", "Exporter CSV",
             "Exporte les vertices du shapefile AEP vers un CSV "
             "(vertices_gps.csv) avec densification par défaut 5 m. "
             "Fichier de base utilisé par les étapes 2, 3 et 4."),
            ("🌍", "Étape 2", "Télécharger DEM",
             "Télécharge le DEM Copernicus GLO-30 (30 m). Zone bbox : "
             "préconfigurée Ouest Algérien, auto depuis CSV étape 1, ou coordonnées manuelles. "
             "Gratuit, sans compte requis."),
            ("⛰️", "Étape 3", "Altitudes DEM",
             "Échantillonne l'altitude depuis le DEM (étape 2) pour chaque vertex "
             "(vertices_avec_altitudes.csv). Validé : RMSE 1.43 m, LE90 2.25 m, "
             "96.6 % dans ±3 m sur 4 203 pts GPS (Algérie, V2.1)."),
            ("✂️", "Étape 4", "Découpage dénivelé",
             "Découpe un fichier source (SHP/GeoJSON) à chaque variation de "
             "dénivelé (seuil configurable, défaut 10 m). Utilise le CSV altitudes étape 3. "
             "Produit segments et points d'accrochage."),
            ("📍", "Étape 5", "Altitude → Couches",
             "Ajoute le champ altitude à toutes les couches vecteur SHP du projet QGIS "
             "à partir du CSV altitudes étape 3. Produit de nouveaux SHP enrichis."),
            ("📌", "Étape 6", "Altitudes depuis CSV",
             "Ajoute des altitudes à n'importe quel CSV avec coordonnées. "
             "Choisir l'EPSG, le DEM (étape 2) et le fichier de sortie. "
             "Détection automatique colonnes X/Y ou Lon/Lat."),
        ]

        for icon, step, name, desc in steps:
            card = QFrame()
            card.setStyleSheet(
                "QFrame { background: #F5F9FF; border: 1px solid #BBDEFB; "
                "border-radius: 6px; }"
            )
            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 10)
            card_layout.setSpacing(12)

            icon_lbl = QLabel(icon)
            icon_lbl.setFont(QFont("", 18))
            icon_lbl.setFixedWidth(32)
            icon_lbl.setAlignment(Qt.AlignTop | Qt.AlignHCenter)

            text_col = QVBoxLayout()
            text_col.setSpacing(3)

            step_lbl = QLabel(f"<b>{step} — {name}</b>")
            step_lbl.setStyleSheet("color: #1565C0; font-size: 12px;")
            step_lbl.setWordWrap(True)

            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet("color: #444; font-size: 11px;")
            desc_lbl.setWordWrap(True)

            text_col.addWidget(step_lbl)
            text_col.addWidget(desc_lbl)

            card_layout.addWidget(icon_lbl)
            card_layout.addLayout(text_col, 1)
            layout.addWidget(card)

        layout.addStretch()

        note = QLabel(
            "💡  Flux de travail conseillé : 1 → 2 → 3 → 4 → 5\n"
            "     Étape 6 : pour tout CSV quelconque avec coordonnées.\n"
            "     Si vous avez déjà un DEM local, commencez directement à l'étape 1.\n"
            "     V2.1 — Validé sur 4 203 pts GPS (Algérie) : RMSE 1.43 m, LE90 2.25 m ✅"
        )
        note.setStyleSheet(
            "background: #FFF8E1; border: 1px solid #FFE082; border-radius: 5px; "
            "padding: 10px; color: #5D4037; font-size: 11px;"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        scroll.setWidget(inner)
        outer.addWidget(scroll)


# ═══════════════════════════════════════════════════════════════════════
#  BOÎTE DE DIALOGUE PRINCIPALE
# ═══════════════════════════════════════════════════════════════════════

class AEPDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("AEP Tools — Traitement du réseau d'eau potable")
        # Taille suffisante pour que les 6 onglets s'affichent sans chevauchement
        self.setMinimumSize(QSize(860, 640))
        self.resize(960, 730)
        self.setStyleSheet(STYLE_SHEET)
        self._build_ui()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 6)
        main_layout.setSpacing(0)

        # ── Barre de navigation custom ────────────────────────────────
        nav_bar = QWidget()
        nav_bar.setStyleSheet(
            "QWidget { background: #1565C0; }"
            "QPushButton { background: #1976D2; color: white; border: none;"
            "  font-weight: bold; font-size: 9px; padding: 5px 6px;"
            "  border-right: 1px solid #0D47A1; }"
            "QPushButton:hover { background: #1E88E5; }"
            "QPushButton[active='true'] { background: #0D47A1; border-bottom: 3px solid white; }"
        )
        nav_layout = QHBoxLayout(nav_bar)
        nav_layout.setContentsMargins(4, 0, 4, 0)
        nav_layout.setSpacing(0)

        tabs_data = [
            ("Accueil",        TabAccueil()),
            ("1-Export CSV",   Tab01_Export()),
            ("2-DEM",          Tab00_DEM()),
            ("3-Altitudes",    Tab02_Altitudes()),
            ("4-Decoupage",    Tab03_Decoupage()),
            ("5-Alt Couches",  Tab04_ExtracterAlt()),
            ("6-Altitudes CSV",Tab05_ExtractCSV()),
        ]

        stack = QStackedWidget()
        self._nav_buttons = []

        def make_switch(idx):
            def switch():
                stack.setCurrentIndex(idx)
                for i, btn in enumerate(self._nav_buttons):
                    btn.setProperty("active", "true" if i == idx else "false")
                    btn.style().unpolish(btn)
                    btn.style().polish(btn)
            return switch

        for i, (label, widget) in enumerate(tabs_data):
            btn = QPushButton(label)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            btn.setProperty("active", "true" if i == 0 else "false")
            btn.clicked.connect(make_switch(i))
            nav_layout.addWidget(btn)
            self._nav_buttons.append(btn)
            stack.addWidget(widget)

        main_layout.addWidget(nav_bar)
        main_layout.addWidget(stack, 1)

        footer = QLabel(
            "Kouidri Sofiane · sofihydro@gmail.com  |  QGIS ≥ 3.16  |  AEP Tools v2.1.4  |  © 2026 Kouidri Sofiane"
        )
        footer.setStyleSheet("color: #888; font-size: 10px; padding: 2px 6px;")
        footer.setAlignment(Qt.AlignRight)
        main_layout.addWidget(footer)

    def closeEvent(self, event):
        for tab in self.findChildren(TabBase):
            if tab.worker and tab.worker.isRunning():
                tab.worker.stop()
                tab.worker.wait(2000)
        event.accept()
