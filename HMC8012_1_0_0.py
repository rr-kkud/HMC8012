import sys
import pyvisa
import numpy as np
import matplotlib

matplotlib.use('Qt5Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *
import time
from datetime import datetime
import csv
import threading
from collections import deque
import os

try:
    import allantools
except ImportError:
    allantools = None

pause_event = threading.Event()
pause_event.set()


class HMC8012GUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.instrument = None
        self.rm = pyvisa.ResourceManager()
        self.is_connected = False
        self.is_measuring = False
        self.is_logging = False  # локальное логирование (на ПК)
        self.is_hires_logging = False  # hi-res логирование (на USB)
        self.data_log_file = None
        self.measurement_thread = None
        self.data_lock = threading.Lock()
        self.hires_logging_timer = None  # Timer for timed hi-res logging

        # Данные для отображения
        self.time_data = deque(maxlen=1000)
        self.measurement_data = deque(maxlen=1000)
        self.full_time_data = []
        self.full_measurement_data = []

        # Словарь для типов измерений с единицами измерения
        self.measurement_types = {
            "VOLT:DC": {"name": "Voltage DC", "unit": "V", "ylabel": "Voltage (V)", "legend": "Voltage"},
            "VOLT:AC": {"name": "Voltage AC", "unit": "V", "ylabel": "Voltage (V)", "legend": "Voltage"},
            "CURR:DC": {"name": "Current DC", "unit": "A", "ylabel": "Current (A)", "legend": "Current"},
            "CURR:AC": {"name": "Current AC", "unit": "A", "ylabel": "Current (A)", "legend": "Current"},
            "RES": {"name": "Resistance", "unit": "Ω", "ylabel": "Resistance (Ω)", "legend": "Resistance"},
            "FRES": {"name": "Resistance 4W", "unit": "Ω", "ylabel": "Resistance (Ω)", "legend": "Resistance"},
            "FREQ": {"name": "Frequency", "unit": "Hz", "ylabel": "Frequency (Hz)", "legend": "Frequency"},
            "PER": {"name": "Period", "unit": "s", "ylabel": "Period (s)", "legend": "Period"},
            "TEMP": {"name": "Temperature", "unit": "°C", "ylabel": "Temperature (°C)", "legend": "Temperature"}
        }

        # Текущий тип измерения
        self.current_measurement_type = "VOLT:DC"
        self.current_unit = "V"
        self.current_ylabel = "Voltage (V)"
        self.current_legend = "Voltage"
        
        # ADC mode
        self.adc_mode = "SLOW"

        # Настройки по умолчанию
        self.settings = {
            'ip': '192.168.11.23',
            'port': '5025',
            'sample_rate': 500,
            'display_seconds': 30,
            'visa_address': 'TCPIP0::{ip}::INSTR'
        }

        self.init_ui()
        self.init_plot()
        self.setup_timer()

    def init_ui(self):
        """Инициализация пользовательского интерфейса"""
        self.setWindowTitle('HMC8012 Multimeter - Real Time Monitor')
        self.setGeometry(100, 100, 1200, 800)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QHBoxLayout(central_widget)

        # Левая панель
        left_panel = QFrame()
        left_panel.setFrameStyle(QFrame.StyledPanel)
        left_panel.setMaximumWidth(300)
        left_layout = QVBoxLayout(left_panel)

        # 1. Секция подключения (без изменений)
        connection_group = QGroupBox("Connection Settings")
        connection_layout = QFormLayout()
        self.ip_input = QLineEdit(self.settings['ip'])
        self.port_input = QLineEdit(self.settings['port'])
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.toggle_connection)
        self.status_label = QLabel("Status: Disconnected")
        self.status_label.setStyleSheet("color: red; font-weight: bold;")
        connection_layout.addRow("IP Address:", self.ip_input)
        connection_layout.addRow("Port:", self.port_input)
        connection_layout.addRow(self.connect_btn)
        connection_layout.addRow(self.status_label)
        connection_group.setLayout(connection_layout)
        left_layout.addWidget(connection_group)

        # 2. Секция настройки измерений (без изменений)
        settings_group = QGroupBox("Measurement Settings")
        settings_layout = QFormLayout()
        self.sample_rate_input = QSpinBox()
        self.sample_rate_input.setRange(1, 20000)
        self.sample_rate_input.setValue(self.settings['sample_rate'])
        self.sample_rate_input.setSuffix(" mHz")
        self.display_time_input = QSpinBox()
        self.display_time_input.setRange(1, 300000)
        self.display_time_input.setValue(self.settings['display_seconds'])
        self.display_time_input.setSuffix(" s")
        self.measurement_type_combo = QComboBox()
        for key, info in self.measurement_types.items():
            self.measurement_type_combo.addItem(info["name"], key)
        self.measurement_type_combo.currentIndexChanged.connect(self.on_measurement_type_changed)
        settings_layout.addRow("Sample Rate:", self.sample_rate_input)
        settings_layout.addRow("Display Time:", self.display_time_input)
        settings_layout.addRow("Measurement Type:", self.measurement_type_combo)
        settings_group.setLayout(settings_layout)
        left_layout.addWidget(settings_group)

        # ADC Mode Selection
        adc_group = QGroupBox("ADC Mode")
        adc_layout = QFormLayout()
        self.adc_combo = QComboBox()
        self.adc_combo.addItem("Slow", "SLOW")
        self.adc_combo.addItem("Fast", "FAST")
        self.adc_combo.setCurrentIndex(0)
        self.adc_combo.currentIndexChanged.connect(self.on_adc_mode_changed)
        adc_layout.addRow("Mode:", self.adc_combo)
        adc_group.setLayout(adc_layout)
        left_layout.addWidget(adc_group)

        # ========== ИЗМЕНЕНО: секция логирования разделена на локальное и hi-res ==========
        logging_group = QGroupBox("Data Logging")
        logging_layout = QVBoxLayout(logging_group)

        # ---- Подраздел: Local Logging (запись на ПК) ----
        local_box = QGroupBox("Local Logging")
        local_layout = QVBoxLayout(local_box)
        self.logging_checkbox = QCheckBox("Enable Data Logging")
        self.logging_checkbox.stateChanged.connect(self.toggle_logging)
        local_layout.addWidget(self.logging_checkbox)

        # Статус локального логирования
        local_status_layout = QHBoxLayout()
        local_status_layout.addWidget(QLabel("Status:"))
        self.local_logging_status = QLabel("OFF")  # переименовано
        self.local_logging_status.setStyleSheet("color: gray;")
        local_status_layout.addWidget(self.local_logging_status)
        local_status_layout.addStretch()
        local_layout.addLayout(local_status_layout)

        # Имя локального файла
        local_file_layout = QHBoxLayout()
        local_file_layout.addWidget(QLabel("File:"))
        self.local_filename_label = QLabel("None")  # переименовано
        self.local_filename_label.setWordWrap(True)
        local_file_layout.addWidget(self.local_filename_label)
        local_file_layout.addStretch()
        local_layout.addLayout(local_file_layout)

        # ---- Подраздел: Hi-Res Logging (запись на USB + перенос) ----
        hires_box = QGroupBox("Hi‑Res Logging (USB)")
        hires_layout = QVBoxLayout(hires_box)
        self.hires_checkbox = QCheckBox("Enable hi-res logging on USB")
        self.hires_checkbox.stateChanged.connect(self.on_hires_changed)
        hires_layout.addWidget(self.hires_checkbox)

        # Время логирования
        time_layout = QHBoxLayout()
        time_layout.addWidget(QLabel("Duration (s):"))
        self.hires_time_input = QSpinBox()
        self.hires_time_input.setRange(0, 3600)
        self.hires_time_input.setValue(0)
        self.hires_time_input.setSuffix(" s")
        self.hires_time_input.setToolTip("0 = Infinite logging")
        time_layout.addWidget(self.hires_time_input)
        time_layout.addStretch()
        hires_layout.addLayout(time_layout)

        # Статус hi-res логирования (3 состояния)
        hires_status_layout = QHBoxLayout()
        hires_status_layout.addWidget(QLabel("Status:"))
        self.hires_status_label = QLabel("OFF")  # новый виджет
        self.hires_status_label.setStyleSheet("color: gray;")
        hires_status_layout.addWidget(self.hires_status_label)
        hires_status_layout.addStretch()
        hires_layout.addLayout(hires_status_layout)

        # Имя файла на USB / во внутреннем хранилище
        hires_file_layout = QHBoxLayout()
        hires_file_layout.addWidget(QLabel("File:"))
        self.hires_filename_label = QLabel("None")  # новый виджет
        self.hires_filename_label.setWordWrap(True)
        hires_file_layout.addWidget(self.hires_filename_label)
        hires_file_layout.addStretch()
        hires_layout.addLayout(hires_file_layout)

        # Собираем общую группу логирования
        logging_layout.addWidget(local_box)
        logging_layout.addWidget(hires_box)
        logging_layout.addStretch()
        left_layout.addWidget(logging_group)
        # ========== КОНЕЦ ИЗМЕНЕНИЙ ==========

        # 3. Секция текущих значений (без изменений, только обновляется единица измерения)
        values_group = QGroupBox("Current Values")
        values_layout = QFormLayout()
        self.current_value_label = QLabel("--")
        self.current_value_label.setStyleSheet("font-size: 24px; font-weight: bold;")
        self.average_label = QLabel("--")
        self.min_label = QLabel("--")
        self.max_label = QLabel("--")
        self.std_label = QLabel("--")
        values_layout.addRow("Current:", self.current_value_label)
        values_layout.addRow("Average:", self.average_label)
        values_layout.addRow("Minimum:", self.min_label)
        values_layout.addRow("Maximum:", self.max_label)
        values_layout.addRow("Std Dev:", self.std_label)
        values_group.setLayout(values_layout)
        left_layout.addWidget(values_group)

        # 4. Кнопки управления (без изменений)
        control_group = QGroupBox("Controls")
        control_layout = QVBoxLayout()
        self.start_btn = QPushButton("Start Measurement")
        self.start_btn.clicked.connect(self.start_measurement)
        self.start_btn.setEnabled(False)
        self.stop_btn = QPushButton("Stop Measurement")
        self.stop_btn.clicked.connect(self.stop_measurement)
        self.stop_btn.setEnabled(False)
        self.clear_btn = QPushButton("Clear Data")
        self.clear_btn.clicked.connect(self.clear_data)
        self.export_btn = QPushButton("Export Data")
        self.export_btn.clicked.connect(self.export_data)
        control_layout.addWidget(self.start_btn)
        control_layout.addWidget(self.stop_btn)
        control_layout.addWidget(self.clear_btn)
        control_layout.addWidget(self.export_btn)
        control_layout.addStretch()
        control_group.setLayout(control_layout)
        left_layout.addWidget(control_group)
        left_layout.addStretch()

        # Правая панель (табуляция с графиками)
        right_panel = QFrame()
        right_layout = QVBoxLayout(right_panel)
        
        # Создаем QTabWidget
        self.tabs = QTabWidget()
        
        # ===== TAB 1: Measurement Graph =====
        tab1 = QWidget()
        tab1_layout = QVBoxLayout(tab1)
        self.figure = Figure(figsize=(10, 8), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        tab1_layout.addWidget(self.canvas)
        self.tabs.addTab(tab1, "Measurement")
        
        # ===== TAB 2: Allan Deviation =====
        tab2 = QWidget()
        tab2_layout = QVBoxLayout(tab2)
        self.figure_adev = Figure(figsize=(10, 8), dpi=100)
        self.canvas_adev = FigureCanvas(self.figure_adev)
        tab2_layout.addWidget(self.canvas_adev)
        
        # Add label for allantools status
        if allantools is None:
            warning_label = QLabel("⚠ allantools not installed. Install with: pip install allantools")
            warning_label.setStyleSheet("color: orange; font-weight: bold; padding: 5px;")
            tab2_layout.insertWidget(0, warning_label)
        
        self.tabs.addTab(tab2, "Allan Deviation")
        
        right_layout.addWidget(self.tabs)

        main_layout.addWidget(left_panel)
        main_layout.addWidget(right_panel, 1)

        self.create_menu()

    # ------------------------------------------------------------------
    # Методы для работы с hi-res логированием (ИЗМЕНЕНЫ И ДОПОЛНЕНЫ)
    # ------------------------------------------------------------------
    def on_hires_changed(self, state):
        """Обработка изменения состояния галочки hi-res logging."""
        if state == Qt.Checked:
            self.enable_hires_logging()
        else:
            self.disable_hires_logging()

    def enable_hires_logging(self):
        """Включение режима hi-res записи на USB."""
        try:
            pause_event.clear()

            self.hires_checkbox.setChecked(True)

            self.instrument.write("DATA:LOG:INTerval 0")
            
            # Set logging mode based on duration
            hires_duration = self.hires_time_input.value()
            if hires_duration == 0:
                # Infinite logging mode
                self.instrument.write("DATA:LOG:MODE UNL")
            else:
                # Time-based logging mode
                self.instrument.write("DATA:LOG:MODE TIME")
                self.instrument.write(f"DATA:LOG:TIME {hires_duration}")
            
            timestamp = datetime.now().strftime("%d%H%M%S")
            filename = f"{timestamp}.csv"
            self.instrument.write(f"DATA:LOG:FNAM '{filename}', EXT")
            path = self.instrument.query("DATA:LOG:FNAM?")
            global USB_filename
            USB_filename = (path.split('/')[-1])[0:-2]            
            # If time-based logging, set up auto-disable timer
            if hires_duration > 0:
                if self.hires_logging_timer is None:
                    self.hires_logging_timer = QTimer()
                    self.hires_logging_timer.timeout.connect(self.on_hires_logging_timeout)
                # Convert seconds to milliseconds
                self.hires_logging_timer.start(hires_duration * 1000)
            script_dir = os.path.dirname(os.path.abspath(__file__))
            global local_filename
            local_filename = f"hires_transfer_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

            if not os.path.isdir(os.path.join(script_dir, 'usb_logs')):
                print("aa")
                os.mkdir(os.path.join(script_dir, 'usb_logs'))
            global local_path
            local_path = os.path.join(script_dir, 'usb_logs', local_filename)
            self.data_log_file = open(local_path, 'w', newline='')

            self.is_hires_logging = True
            # Обновляем статус hi-res
            self.hires_status_label.setText("Recording")
            self.hires_status_label.setStyleSheet("color: green; font-weight: bold;")
            self.hires_filename_label.setText(f"USB: {USB_filename}")

            self.instrument.write("DATA:LOG ON")
            print("hi-res logging enabled")
            pause_event.set()
        except Exception as e:
            QMessageBox.critical(self, "Logging Error", f"Failed to start hi-res logging:\n{str(e)}")
            self.hires_checkbox.setChecked(False)
            self.hires_status_label.setText("OFF")
            self.hires_status_label.setStyleSheet("color: gray;")
            self.hires_filename_label.setText("None")
            pause_event.set()
            print(f"Error enabling hi-res logging: {e}")

    def on_hires_logging_timeout(self):
        """Handle timed hi-res logging completion."""
        if self.hires_logging_timer:
            self.hires_logging_timer.stop()
        # Wait 3 seconds to allow multimeter to finalize logging, then disable and transfer
        self.hires_status_label.setText("Finalizing")
        self.hires_status_label.setStyleSheet("color: yellow; font-weight: bold;")
        QTimer.singleShot(3000, self.finalize_hires_logging)
    
    def finalize_hires_logging(self):
        """Complete hi-res logging after finalization delay."""
        # Uncheck the checkbox to trigger disable_hires_logging
        self.hires_checkbox.blockSignals(True)
        self.hires_checkbox.setChecked(False)
        self.hires_checkbox.blockSignals(False)
        self.disable_hires_logging()

    def disable_hires_logging(self):
        """
        Выключение режима hi-res логирования.
        Здесь же выполняется перенос данных с USB во внутреннее хранилище.
        """
        try:
            pause_event.clear()
            # Останавливаем запись на приборе
            self.instrument.write("DATA:LOG OFF")
            time.sleep(0.25)

            # Получаем информацию о файле на USB
            nop = self.instrument.query(f'DATA:POINts? "{USB_filename}", EXT')
            print(f"Number of points on USB: {nop}")

            self.hires_status_label.setText("Transferring")
            self.hires_status_label.setStyleSheet("color: orange; font-weight: bold;")
            self.hires_filename_label.setText(f"Local: {local_filename}")
            # QMessageBox.information(f"Please wait for data transfer to file '{local_filename}'")
            Logdata = self.instrument.query(f'DATA:DATA? "{USB_filename}", EXT')
            self.data_log_file.write(Logdata + chr(10))
            self.data_log_file.close()

            QMessageBox.information(self, "Success", f"Data successfully transferred to file '{local_path}'")
            self.hires_status_label.setText("OFF")
            self.hires_status_label.setStyleSheet("color: gray;")
            self.hires_filename_label.setText("None")
            # -----------------------------------------------------------

            self.is_hires_logging = False
            # Stop the timer if it's running
            if self.hires_logging_timer:
                self.hires_logging_timer.stop()
            print("hi-res logging disabled")
            pause_event.set()

        except Exception as e:
            print(f"Error disabling hi-res logging: {e}")
            pause_event.set()
            self.hires_status_label.setText("OFF")
            self.hires_status_label.setStyleSheet("color: gray;")
            self.hires_filename_label.setText("None")

    # ------------------------------------------------------------------
    # Методы для локального логирования (ПЕРЕИМЕНОВАНЫ ВИДЖЕТЫ)
    # ------------------------------------------------------------------
    def toggle_logging(self, state):
        """Включение/выключение записи в локальный файл."""
        if state == Qt.Checked:
            self.start_logging()
        else:
            self.stop_logging()

    def start_logging(self):
        """Начало записи в локальный файл."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            measurement_name = self.measurement_types[self.current_measurement_type]["name"].replace(" ", "_")
            filename = f"hmc8012_{measurement_name}_{timestamp}.csv"

            if not os.path.exists('logs'):
                os.makedirs('logs')
            filepath = os.path.join('logs', filename)

            self.data_log_file = open(filepath, 'w', newline='')
            self.log_writer = csv.writer(self.data_log_file)
            self.log_writer.writerow(['Timestamp', 'Time(s)', f'Value({self.current_unit})'])

            self.is_logging = True
            # Обновляем статус локального логирования
            self.local_logging_status.setText("ON")
            self.local_logging_status.setStyleSheet("color: green; font-weight: bold;")
            self.local_filename_label.setText(f"{filename}")

            # Заголовок с настройками
            settings_header = [
                f"# HMC8012 Measurement Log",
                f"# Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"# IP Address: {self.ip_input.text()}",
                f"# Measurement Type: {self.measurement_types[self.current_measurement_type]['name']}",
                f"# Unit: {self.current_unit}",
                f"# Sample Rate: {self.sample_rate_input.value()} mHz",
                f"# Hi-Res Mode: {'Enabled' if self.hires_checkbox.isChecked() else 'Disabled'}",
                "#"
            ]
            for line in settings_header:
                self.data_log_file.write(f"{line}\n")

        except Exception as e:
            QMessageBox.critical(self, "Logging Error", f"Failed to start logging:\n{str(e)}")
            self.is_logging = False
            self.logging_checkbox.setChecked(False)

    def stop_logging(self):
        """Остановка записи в локальный файл."""
        if self.data_log_file:
            try:
                # Запись статистики в конец файла
                if self.full_measurement_data:
                    stats = [
                        "#",
                        "# STATISTICS:",
                        f"# Total Measurements: {len(self.full_measurement_data)}",
                        f"# Average Value: {np.mean(self.full_measurement_data):.6f} {self.current_unit}",
                        f"# Minimum Value: {np.min(self.full_measurement_data):.6f} {self.current_unit}",
                        f"# Maximum Value: {np.max(self.full_measurement_data):.6f} {self.current_unit}",
                        f"# Standard Deviation: {np.std(self.full_measurement_data):.6f} {self.current_unit}",
                        f"# Duration: {self.full_time_data[-1] if self.full_time_data else 0:.2f} s",
                        f"# Hi-Res Mode: {'Enabled' if self.hires_checkbox.isChecked() else 'Disabled'}"
                    ]
                    for line in stats:
                        self.data_log_file.write(f"{line}\n")
                self.data_log_file.close()
            except Exception as e:
                print(f"Error closing log file: {e}")
            finally:
                self.data_log_file = None
                self.log_writer = None

        self.is_logging = False
        # Сброс статуса локального логирования
        self.local_logging_status.setText("OFF")
        self.local_logging_status.setStyleSheet("color: gray;")
        self.local_filename_label.setText("None")

    # ------------------------------------------------------------------
    # Остальные методы (с небольшими исправлениями)
    # ------------------------------------------------------------------
    def on_measurement_type_changed(self, index):
        """Обработка изменения типа измерения."""
        measurement_key = self.measurement_type_combo.itemData(index)
        if measurement_key in self.measurement_types:
            self.current_measurement_type = measurement_key
            measurement_info = self.measurement_types[measurement_key]
            self.current_unit = measurement_info["unit"]
            self.current_ylabel = measurement_info["ylabel"]
            #self.current_legend = measurement_info["legend"]
            self.update_plot_labels()
            if self.is_measuring and self.instrument:
                try:
                    self.instrument.write(f"CONF:{measurement_key}")
                    self.instrument.write(f"{measurement_key}:RANG:AUTO ON")
                except Exception as e:
                    print(f"Error changing measurement type: {e}")

    def update_plot_labels(self):
        """Обновление меток на графике."""
        if hasattr(self, 'ax'):
            self.ax.set_ylabel(self.current_ylabel, fontsize=12)
            self.ax.set_title(f'HMC8012 - {self.measurement_types[self.current_measurement_type]["name"]} Measurement',
                              fontsize=14, pad=20)
            if hasattr(self, 'line'):
                self.line.set_label(self.current_legend)
                self.ax.legend(loc='upper right')
            self.canvas.draw()

    def on_adc_mode_changed(self, index):
        """Handle ADC mode change (SLOW or FAST)."""
        self.adc_mode = self.adc_combo.itemData(index)
        if self.is_connected and self.instrument:
            try:
                self.instrument.write(f"ADCR {self.adc_mode}")
                print(f"ADC mode set to: {self.adc_mode}")
            except Exception as e:
                print(f"Error setting ADC mode: {e}")

    def init_plot(self):
        """Инициализация графика."""
        self.figure.clear()
        self.ax = self.figure.add_subplot(111)
        self.line, = self.ax.plot([], [], 'b-', linewidth=2, label=self.current_legend)
        self.ax.set_xlabel('Time (s)', fontsize=12)
        self.ax.set_ylabel(self.current_ylabel, fontsize=12)
        self.ax.set_title(f'HMC8012 - {self.measurement_types[self.current_measurement_type]["name"]} Measurement',
                          fontsize=14, pad=20)
        self.ax.grid(True, alpha=0.3)
        #self.ax.legend(loc='upper right')
        self.stats_text = self.ax.text(0.02, 0.95, '',
                                       transform=self.ax.transAxes,
                                       fontsize=10,
                                       verticalalignment='top',
                                       bbox=dict(boxstyle='round',
                                                 facecolor='wheat',
                                                 alpha=0.8))
        self.canvas.draw()
        
        # Initialize Allan deviation plot
        self.init_adev_plot()

    def init_adev_plot(self):
        """Инициализация графика Allan Deviation."""
        self.figure_adev.clear()
        self.ax_adev = self.figure_adev.add_subplot(111)
        self.ax_adev.set_xlabel('Averaging Time (s)', fontsize=12)
        self.ax_adev.set_ylabel('OADEV (absolute)', fontsize=12)
        self.ax_adev.set_title('Allan Deviation', fontsize=14, pad=20)
        self.ax_adev.grid(True, alpha=0.3, which='both')
        self.ax_adev.set_xscale('log')
        self.ax_adev.set_yscale('log')
        self.adev_line = None
        self.adev_tau = None
        self.adev_values = None
        self.canvas_adev.draw()

    def setup_timer(self):
        self.plot_timer = QTimer()
        self.plot_timer.timeout.connect(self.on_plot_timer_update)
        self.plot_timer.start(100)
    
    def on_plot_timer_update(self):
        """Обновляет все графики."""
        self.update_plot()
        # Update Allan deviation if enough data and allantools is available
        if allantools is not None:
            self.update_adev_plot()

    def create_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu('File')
        exit_action = QAction('Exit', self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        settings_menu = menubar.addMenu('Settings')
        reset_action = QAction('Reset Settings', self)
        reset_action.triggered.connect(self.reset_settings)
        settings_menu.addAction(reset_action)

    def toggle_connection(self):
        if not self.is_connected:
            self.connect_to_device()
        else:
            self.disconnect_from_device()

    def connect_to_device(self):
        ip = self.ip_input.text().strip()
        port = self.port_input.text().strip()
        if not ip:
            QMessageBox.warning(self, "Warning", "Please enter IP address")
            return
        try:
            visa_address = f"TCPIP0::{ip}::INSTR"
            self.instrument = self.rm.open_resource(visa_address)
            self.instrument.timeout = 25000
            idn = self.instrument.query("*IDN?")
            self.is_connected = True
            self.status_label.setText(f"Status: Connected\n{idn.strip()}")
            self.status_label.setStyleSheet("color: green; font-weight: bold;")
            self.connect_btn.setText("Disconnect")
            QMessageBox.information(self, "Success", f"Connected to:\n{idn}\nPlease wait for reset")
            self.instrument.write("ADCR SLOW")
            self.instrument.write(f"CONF:{self.current_measurement_type}")
            self.instrument.write(f"{self.current_measurement_type}:RANG:AUTO ON")
            self.start_btn.setEnabled(True)
        except Exception as e:
            QMessageBox.critical(self, "Connection Error", f"Failed to connect:\n{str(e)}")
            self.is_connected = False
            self.status_label.setText("Status: Connection Failed")
            self.status_label.setStyleSheet("color: red; font-weight: bold;")

    def disconnect_from_device(self):
        if self.is_measuring:
            self.stop_measurement()
        if self.instrument:
            self.instrument.close()
            self.instrument = None
        self.is_connected = False
        self.status_label.setText("Status: Disconnected")
        self.status_label.setStyleSheet("color: red; font-weight: bold;")
        self.connect_btn.setText("Connect")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        if self.is_logging:
            self.stop_logging()

    def start_measurement(self):
        if not self.is_connected or not self.instrument:
            QMessageBox.warning(self, "Warning", "Not connected to device")
            return
        sample_rate = self.sample_rate_input.value()/1000
        try:
            self.instrument.write(f"CONF:{self.current_measurement_type}")
            self.instrument.write(f"{self.current_measurement_type}:RANG:AUTO ON")
            self.instrument.write("TRIG:SOUR IMM")
            self.is_measuring = True
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.measurement_type_combo.setEnabled(False)
            self.measurement_thread = threading.Thread(
                target=self.measurement_loop,
                args=(sample_rate,),
                daemon=True
            )
            self.measurement_thread.start()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to start measurement:\n{str(e)}")
            self.is_measuring = False
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.measurement_type_combo.setEnabled(True)

    def stop_measurement(self):
        self.is_measuring = False
        self.measurement_type_combo.setEnabled(True)
        if self.measurement_thread and self.measurement_thread.is_alive():
            self.measurement_thread.join(timeout=2.0)
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def measurement_loop(self, sample_rate):
        interval = 1.0 / (sample_rate)
        start_time = time.time()
        while self.is_measuring and self.is_connected:
            try:
                pause_event.wait()
                current_time = time.time() - start_time
                response = self.instrument.query("READ?")
                time.sleep(0.02)
                try:
                    value = float(response.strip().split()[0])
                except:
                    value = float(response.strip())
                with self.data_lock:
                    self.time_data.append(current_time)
                    self.measurement_data.append(value)
                    self.full_time_data.append(current_time)
                    self.full_measurement_data.append(value)
                if self.is_logging and self.data_log_file:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    self.data_log_file.write(f"{timestamp},{current_time:.3f},{value:.6f}\n")
                elapsed = time.time() - start_time
                target_time = len(self.time_data) * interval
                sleep_time = target_time - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)
            except Exception as e:
                print(f"Measurement error: {e}")
                time.sleep(interval)

    def update_plot(self):
        if not self.time_data or not self.measurement_data:
            return
        with self.data_lock:
            time_data = list(self.time_data)
            measurement_data = list(self.measurement_data)
        if not time_data or not measurement_data:
            return
        display_seconds = self.display_time_input.value()
        current_time = time_data[-1] if time_data else 0
        if current_time > display_seconds:
            min_time = current_time - display_seconds
            filtered_indices = [i for i, t in enumerate(time_data) if t >= min_time]
            if filtered_indices:
                display_time = [time_data[i] for i in filtered_indices]
                display_values = [measurement_data[i] for i in filtered_indices]
            else:
                display_time = time_data[-100:] if len(time_data) > 100 else time_data
                display_values = measurement_data[-100:] if len(measurement_data) > 100 else measurement_data
        else:
            display_time = time_data
            display_values = measurement_data
        self.line.set_data(display_time, display_values)
        if display_time:
            self.ax.set_xlim(min(display_time), max(display_time))
        if display_values:
            y_min = min(display_values)
            y_max = max(display_values)
            y_range = y_max - y_min
            if y_range == 0:
                y_range = 1
            margin = y_range * 0.1
            self.ax.set_ylim(y_min - margin, y_max + margin)
        if measurement_data:
            last_n = min(50, len(measurement_data))
            recent_data = measurement_data[-last_n:]
            stats_text = (
                f"Current: {measurement_data[-1]:.6f} {self.current_unit}\n"
                f"Average: {np.mean(recent_data):.6f} {self.current_unit}\n"
                f"Min/Max: {np.min(recent_data):.6f}/{np.max(recent_data):.6f} {self.current_unit}\n"
                f"Std Dev: {np.std(recent_data):.6f} {self.current_unit}\n"
                f"Points: {len(time_data)}\n"
                f"Time: {time_data[-1]:.1f} s"
            )
            self.stats_text.set_text(stats_text)
            self.current_value_label.setText(f"{measurement_data[-1]:.6f} {self.current_unit}")
            self.average_label.setText(f"{np.mean(recent_data):.6f} {self.current_unit}")
            self.min_label.setText(f"{np.min(recent_data):.6f} {self.current_unit}")
            self.max_label.setText(f"{np.max(recent_data):.6f} {self.current_unit}")
            self.std_label.setText(f"{np.std(recent_data):.6f} {self.current_unit}")
        self.canvas.draw_idle()

    def update_adev_plot(self):
        """Обновляет график Allan Deviation на основе текущих данных."""
        if not self.full_measurement_data or len(self.full_measurement_data) < 10:
            return
        
        try:
            with self.data_lock:
                data = np.array(list(self.full_measurement_data))
                time_data = np.array(list(self.full_time_data))
            
            # Estimate sample rate from time data
            if len(time_data) < 2:
                return
            
            # Calculate mean time difference to get sample rate
            time_diffs = np.diff(time_data)
            if len(time_diffs) > 0:
                mean_interval = np.mean(time_diffs[time_diffs > 0])
                if mean_interval <= 0:
                    return
                rate = 1.0 / mean_interval
            else:
                return
            
            # Compute OADEV using allantools
            # tau values are powers of 2: [1, 2, 4, 8, 16, ...] * sample_interval
            tau, adev, _, _ = allantools.oadev(data, rate=rate, data_type="freq")
            
            # Update the plot
            self.ax_adev.clear()
            self.ax_adev.loglog(tau, adev, 'b-', linewidth=2, marker='o', markersize=4)
            self.ax_adev.set_xlabel('Averaging Time (s)', fontsize=12)
            self.ax_adev.set_ylabel('OADEV (absolute)', fontsize=12)
            self.ax_adev.set_title('Allan Deviation', fontsize=14, pad=20)
            self.ax_adev.grid(True, alpha=0.3, which='both')
            
            # Add stats text
            min_adev = np.min(adev)
            max_adev = np.max(adev)
            adev_stats_text = (
                f"Data Points: {len(data)}\n"
                f"Sample Rate: {rate:.2f} mHz\n"
                f"Min ADEV: {min_adev:.2e}\n"
                f"Max ADEV: {max_adev:.2e}"
            )
            stats_box = self.ax_adev.text(0.02, 0.95, adev_stats_text,
                                          transform=self.ax_adev.transAxes,
                                          fontsize=10,
                                          verticalalignment='top',
                                          bbox=dict(boxstyle='round',
                                                    facecolor='lightblue',
                                                    alpha=0.8))
            self.canvas_adev.draw_idle()
            
        except Exception as e:
            print(f"Error updating Allan deviation plot: {e}")

    def clear_data(self):
        reply = QMessageBox.question(self, 'Clear Data',
                                     'Are you sure you want to clear all data?',
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            with self.data_lock:
                self.time_data.clear()
                self.measurement_data.clear()
                self.full_time_data.clear()
                self.full_measurement_data.clear()
            self.line.set_data([], [])
            self.current_value_label.setText("--")
            self.average_label.setText("--")
            self.min_label.setText("--")
            self.max_label.setText("--")
            self.std_label.setText("--")
            self.stats_text.set_text("")
            self.canvas.draw()
            
            # Clear Allan deviation plot
            self.init_adev_plot()

    def export_data(self):
        if not self.full_time_data:
            QMessageBox.warning(self, "Warning", "No data to export")
            return
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            measurement_name = self.measurement_types[self.current_measurement_type]["name"].replace(" ", "_")
            default_filename = f"hmc8012_{measurement_name}_export_{timestamp}.csv"
            filename, _ = QFileDialog.getSaveFileName(
                self, "Export Data",
                default_filename,
                "CSV Files (*.csv);;All Files (*)"
            )
            if filename:
                with open(filename, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(['Time(s)', f'Value({self.current_unit})'])
                    for t, v in zip(self.full_time_data, self.full_measurement_data):
                        writer.writerow([f"{t:.3f}", f"{v:.6f}"])
                QMessageBox.information(self, "Success", f"Data exported to:\n{filename}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export data:\n{str(e)}")

    def reset_settings(self):
        """Сброс настроек к значениям по умолчанию."""
        reply = QMessageBox.question(self, 'Reset Settings',
                                     'Reset all settings to default values?',
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.ip_input.setText(self.settings['ip'])
            self.port_input.setText(self.settings['port'])
            self.sample_rate_input.setValue(self.settings['sample_rate'])
            self.display_time_input.setValue(self.settings['display_seconds'])
            self.measurement_type_combo.setCurrentIndex(0)
            # Сброс hi-res статуса
            self.hires_checkbox.setChecked(False)
            self.hires_status_label.setText("OFF")
            self.hires_status_label.setStyleSheet("color: gray;")
            self.hires_filename_label.setText("None")
            if self.is_logging:
                self.logging_checkbox.setChecked(False)
                self.stop_logging()

    def closeEvent(self, event):
        if self.is_connected:
            reply = QMessageBox.question(self, 'Exit',
                                         'Device is still connected. Exit anyway?',
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.No:
                event.ignore()
                return
        if self.is_measuring:
            self.stop_measurement()
        if self.is_logging:
            self.stop_logging()
        if self.is_connected:
            self.disconnect_from_device()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = HMC8012GUI()
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()