import array
import os

import soundfile
import numpy as np
import urllib
import debugpy
import json

from typing import List
from PySide6.QtCore import *
from PySide6.QtWidgets import *
from PySide6.QtGui import *
from slicer2 import Slicer

from gui.Ui_MainWindow import Ui_MainWindow


class MainWindow(QMainWindow):
    active_worker:QThread
    def __init__(self):
        super(MainWindow, self).__init__()

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        self.ui.pushButtonAddFiles.clicked.connect(self._q_add_audio_files)
        self.ui.pushButtonBrowse.clicked.connect(self._q_browse_output_dir)
        self.ui.pushButtonBrowseMappingFile.clicked.connect(self._q_browse_mapping_file)
        self.ui.pushButtonClearList.clicked.connect(self._q_clear_audio_list)
        self.ui.pushButtonAbout.clicked.connect(self._q_about)
        self.ui.pushButtonStart.clicked.connect(self._q_start)

        self.ui.progressBar.setMinimum(0)
        self.ui.progressBar.setMaximum(100)
        self.ui.progressBar.setValue(0)
        self.ui.pushButtonStart.setDefault(True)

        validator = QRegularExpressionValidator(QRegularExpression(r"\d+"))
        self.ui.lineEditThreshold.setValidator(QDoubleValidator())
        self.ui.lineEditMinLen.setValidator(validator)
        self.ui.lineEditMinInterval.setValidator(validator)
        self.ui.lineEditHopSize.setValidator(validator)
        self.ui.lineEditMaxSilence.setValidator(validator)

        self.ui.listWidgetTaskList.setAlternatingRowColors(True)

        # State variables
        self.workCount = 0
        self.workFinished = 0
        self.processing = False

        self.setWindowTitle(QApplication.applicationName())

        # Must set to accept drag and drop events
        self.setAcceptDrops(True)

    def _q_browse_output_dir(self):
        path = QFileDialog.getExistingDirectory(
            self, "Browse Output Directory", ".")
        if path != "":
            self.ui.lineEditOutputDir.setText(QDir.toNativeSeparators(path))
            
    def _q_browse_mapping_file(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Browse Mapping file", ".", "Mapping file (*.json)")
        if path != "":
            self.ui.lineEditOutputMappingFile.setText(QDir.toNativeSeparators(path))

    def _q_add_audio_files(self):
        if self.processing:
            self.warningProcessNotFinished()
            return

        paths, _ = QFileDialog.getOpenFileNames(
            self, 'Select Audio Files', ".", 'Wave Files(*.wav)')
        for path in paths:
            item = QListWidgetItem()
            item.setSizeHint(QSize(200, 24))
            item.setText(QFileInfo(path).fileName())
            # Save full path at custom role
            item.setData(Qt.ItemDataRole.UserRole + 1, path)
            self.ui.listWidgetTaskList.addItem(item)

    def _q_clear_audio_list(self):
        if self.processing:
            self.warningProcessNotFinished()
            return

        self.ui.listWidgetTaskList.clear()

    def _q_about(self):
        QMessageBox.information(
            self, "About", "Audio Slicer v1.2.1\nCopyright 2020-2023 OpenVPI Team")

    def _q_start(self):
        if self.processing:
            self.warningProcessNotFinished()
            return

        item_count = self.ui.listWidgetTaskList.count()
        if item_count == 0:
            return

        # Collect paths
        paths: list[str] = []
        for i in range(0, item_count):
            item = self.ui.listWidgetTaskList.item(i)
            path = item.data(Qt.ItemDataRole.UserRole + 1)  # Get full path
            paths.append(path)

        self.ui.progressBar.setMaximum(item_count)
        self.ui.progressBar.setValue(0)

        self.workCount = item_count
        self.workFinished = 0
        self.setProcessing(True)
        
        class WorkThread(QThread):
            oneFinished = Signal()
            task_items:list[TaskItem]=[]
    
            def __init__(self, filenames: List[str], window: MainWindow):
                super().__init__()

                self.filenames = filenames
                self.win = window

            def run(self):
                debugpy.debug_this_thread()
                for filename in self.filenames:
                    audio, sr = soundfile.read(filename, dtype=np.float32)
                    is_mono = True
                    if len(audio.shape) > 1:
                        is_mono = False
                        audio = audio.T
                    slicer = Slicer(
                        sr=sr,
                        threshold=float(self.win.ui.lineEditThreshold.text()),
                        min_length=int(self.win.ui.lineEditMinLen.text()),
                        min_interval=int(
                            self.win.ui.lineEditMinInterval.text()),
                        hop_size=int(self.win.ui.lineEditHopSize.text()),
                        max_sil_kept=int(self.win.ui.lineEditMaxSilence.text())
                    )
                    time_spans,chunks = slicer.slice(audio)
                    out_dir = self.win.ui.lineEditOutputDir.text()
                    self.win.last_out_dir=out_dir
                    mapping_file = self.win.ui.lineEditOutputMappingFile.text()
                    self.win.last_mapping_file=mapping_file
                    if out_dir == '':
                        out_dir = os.path.dirname(os.path.abspath(filename))
                    else:
                        # Make dir if not exists
                        info = QDir(out_dir)
                        if not info.exists():
                            info.mkpath(out_dir)

                    has_mapping = mapping_file != ''
                    mapping_items:list[SliceItem]=[]
            
                    for i, chunk in enumerate(chunks):
                        out_wav_name = f"{os.path.basename(filename).rsplit('.', maxsplit=1)[0]}_{i}.wav"
                        out_wav_path = os.path.join(out_dir, out_wav_name)
                        if not is_mono:
                            chunk = chunk.T
                        soundfile.write(out_wav_path, chunk, sr)
                        if has_mapping:
                            start_time,end_time=time_spans[i]
                            mapping_items.append(SliceItem(start_time,end_time,out_wav_name))

                    self.task_items.append(TaskItem(QDir.toNativeSeparators(filename),mapping_items))
                    self.oneFinished.emit()

        # Start work thread
        worker = WorkThread(paths, self)
        self.active_worker=worker  # Collect in case of auto deletion
        worker.oneFinished.connect(self._q_oneFinished)
        worker.finished.connect(self._q_threadFinished)
        worker.start()

    def _q_oneFinished(self):
        self.workFinished += 1
        self.ui.progressBar.setValue(self.workFinished)

    def _q_threadFinished(self):
        if self.last_mapping_file != '':
            mapping_object = SliceMapping(self.last_out_dir,self.active_worker.task_items)
            mapping_json = json.dumps(mapping_object, indent=2, default=lambda o: o.__dict__)
            with open(self.last_mapping_file, 'w', encoding='utf-8') as file:
                file.write(mapping_json)
            
        self.active_worker=None
        self.setProcessing(False)

        QMessageBox.information(
            self, QApplication.applicationName(), "Slicing complete!")

    def warningProcessNotFinished(self):
        QMessageBox.warning(self, QApplication.applicationName(),
                            "Please wait for slicing to complete!")

    def setProcessing(self, processing: bool):
        enabled = not processing
        self.ui.pushButtonStart.setText(
            "Slicing..." if processing else "Start")
        self.ui.pushButtonStart.setEnabled(enabled)
        self.ui.pushButtonAddFiles.setEnabled(enabled)
        self.ui.listWidgetTaskList.setEnabled(enabled)
        self.ui.pushButtonClearList.setEnabled(enabled)
        self.ui.lineEditThreshold.setEnabled(enabled)
        self.ui.lineEditMinLen.setEnabled(enabled)
        self.ui.lineEditMinInterval.setEnabled(enabled)
        self.ui.lineEditHopSize.setEnabled(enabled)
        self.ui.lineEditMaxSilence.setEnabled(enabled)
        self.ui.lineEditOutputDir.setEnabled(enabled)
        self.ui.lineEditOutputMappingFile.setEnabled(enabled)
        self.ui.pushButtonBrowse.setEnabled(enabled)
        self.ui.pushButtonBrowseMappingFile.setEnabled(enabled)
        self.processing = processing

    # Event Handlers
    def closeEvent(self, event):
        if self.processing:
            self.warningProcessNotFinished()
            event.ignore()

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        has_wav = False
        for url in urls:
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            ext = os.path.splitext(path)[1]
            if ext.lower() == '.wav':
                has_wav = True
                break
        if has_wav:
            event.accept()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        for url in urls:
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            ext = os.path.splitext(path)[1]
            if ext.lower() != '.wav':
                continue
            item = QListWidgetItem()
            item.setSizeHint(QSize(200, 24))
            item.setText(QFileInfo(path).fileName())
            item.setData(Qt.ItemDataRole.UserRole + 1,
                         path)
            self.ui.listWidgetTaskList.addItem(item)

class SliceItem:
    start:int
    end:int
    file:str
    def __init__(self, start:int, end:int, file:str):
        self.start = int(start)
        self.end = int(end)
        self.file = file

class TaskItem:
    original_file:str
    slices:list[SliceItem]
    def __init__(self, original_file:str, slices:list[SliceItem]):
        self.originalFile = original_file
        self.slices = slices

class SliceMapping:
    output_folder:str
    tasks:list[TaskItem]
    def __init__(self, output_folder:str, tasks:list[TaskItem]):
        self.outputFolder = output_folder
        self.tasks = tasks