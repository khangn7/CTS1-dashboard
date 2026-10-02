#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Created on 17/10/2019
Tom Mladenov, European Space Agency
"""

__author__ = 'Tom Mladenov, European Space Operations Centre ESA/ESOC'
__email__ = 'Tom.Mladenov@esa.int'

import os
import struct
import zmq
import sys
import time
from threading import Thread, Lock
from PyQt5.QtCore import (QCoreApplication, QObject, QRunnable, QThread, pyqtSignal, QEvent, Qt, QUrl, QTimer)
from PyQt5.QtWidgets import QApplication, QMainWindow, QLineEdit, QWidget, QVBoxLayout, QPushButton, QTableWidgetItem, QFormLayout, QLabel, QLayout, QTextEdit
from PyQt5.uic import loadUi
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtGui import QImage, QIcon, QPixmap, QFont, QSyntaxHighlighter, QTextCharFormat, QColor, QBrush
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkRequest
import subprocess

import numpy as np
import argparse
import datetime
import collections
from crccheck.crc import Crc32c
import logging
import tempfile
import shutil
import requests
import json
from pathlib import Path

# import decode_packet_safe
# packages\cts1_mo_tools\src\cts1_mo_tools\cts1_decode_satnogs_packets.py
from cts1_mo_tools.cts1_decode_satnogs_packets import decode_packet_safe


SCRIPT = os.path.realpath(__file__)
HERE = os.path.dirname(SCRIPT)

NODES = {
            "1": "Nanomind",
            "2": "EPS dock",
            "3": "EPS ACU1",
            "4": "EPS ACU2",
            "5": "Nanocom",
            "6": "EPS PDU1",
            "7": "EPS PDU2"
}


# gotta pull this from server but for now it is local file
TELECOMMANDS_LIST = json.loads(Path("app-grc_script/lib/cts_sat_1_telecommand_list.json").read_text(encoding="utf-8"))
SERVER_URL = "https://echo.free.beeceptor.com"

class Main(QMainWindow):


    def __init__(self, parent=None):
        super(Main, self).__init__(parent)
        eventLogger.info('Starting OPS-SAT UHF Desktop')
        gui = path + '/gui/gui.ui'
        eventLogger.info('Loading GUI file {GUI}'.format(GUI=gui))
        loadUi(gui, self)

        self.setWindowTitle('CTS1 Telemetry Desktop')
        self.resize(1000, 800)


        # create tab for telecommands
        command_tab = QWidget()
        command_tab.setLayout(QVBoxLayout())

        command_input = QLineEdit()
        command_input.setPlaceholderText("Command name:")
        command_input.textChanged.connect(self.on_command_typing)
        command_tab.layout().addWidget(command_input)
        self.current_valid_command_name = None

        command_suggestion = QTextEdit()
        f = QFont()
        f.setPointSize(10)
        command_suggestion.setFont(f)
        command_suggestion.setPlainText("(꒪▿꒪) will send command that is showing here on ok click")
        command_suggestion.setReadOnly(True)
        self.command_suggestion = command_suggestion
        command_tab.layout().addWidget(command_suggestion)

        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.send_server_command)
        self.ok_button = ok_button
        command_tab.layout().addWidget(ok_button)

        self.tabWidget_2.addTab (command_tab, "Telecommands")

        #for sending get requests to server
        self.network_manager = QNetworkAccessManager(self)
        self.network_manager.finished.connect(self.on_reply_finished)

        self.logo.setScaledContents(True)

        ops_sat_logo = QPixmap(path + '/gui/img/ESA_OPS_SAT.png')
        self.logo.setPixmap(ops_sat_logo)

        self.clear_tm_button.clicked.connect(self.clearTM)

        self.all_tm_widget.layout().setSizeConstraint(QLayout.SetMinAndMaxSize)

        self.scrollArea.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)

        self.packet_history_table.setColumnCount(10)
        self.packet_history_table.setHorizontalHeaderLabels(['RX Time (UTC)', 'Type','Length', 'CRC OK', 'pri', 'src', 'dst', 'dst port', 'src port', 'flags'])
        packet_history_header = self.packet_history_table.horizontalHeader()
        packet_history_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        packet_history_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        packet_history_header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        packet_history_header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        packet_history_header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
        packet_history_header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeToContents)
        packet_history_header.setSectionResizeMode(6, QtWidgets.QHeaderView.ResizeToContents)
        packet_history_header.setSectionResizeMode(7, QtWidgets.QHeaderView.ResizeToContents)
        packet_history_header.setSectionResizeMode(8, QtWidgets.QHeaderView.ResizeToContents)
        packet_history_header.setSectionResizeMode(9, QtWidgets.QHeaderView.ResizeToContents)

        self.raw_history_table.setColumnCount(3)
        self.raw_history_table.setHorizontalHeaderLabels(['RX Time (UTC)', 'RAW data', 'Bytes'])
        raw_history_table_header = self.raw_history_table.horizontalHeader()
        raw_history_table_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        raw_history_table_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        raw_history_table_header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)


        self.adapter = TMadapter(self, "127.0.0.1", 38211)
        self.adapter.link.connect(self.updateLink)
        self.adapter.packet.connect(self.update)
        self.adapter.packet_count.connect(self.updatePacketCounter)
        self.adapter.start()

        self.host_label.setText(self.adapter.host)
        self.packet_counter_label.setText('Received packets: 0')

        eventLogger.info('GUI Thread started')

        # cache packet fields
        self._packet_fields: dict[str, QLabel] = {}

    #send command to server
    def send_server_command(self):
        if self.current_valid_command_name is None:
            return
        url = f"{SERVER_URL}/{self.current_valid_command_name}"
        self.send_get_request(url)  

    #stuff needed for get request
    def send_get_request(self, url: str):
        request = QNetworkRequest(QUrl(url))
        self.network_manager.get(request)

    def on_reply_finished(self, reply):
        if reply.error() == reply.NoError:
            data = reply.readAll().data()   # bytes
            print(data.decode("utf-8"))
            self.ok_button.setStyleSheet("background-color: #8aef80;")
            QTimer.singleShot(500, lambda: self.ok_button.setStyleSheet(""))

        else:
            print("Error:", reply.errorString())
        reply.deleteLater()   # important -- clean up the reply object


    def on_command_typing(self, text):
        for command in TELECOMMANDS_LIST:
            if text.lower() in command["name"].lower():
                self.command_suggestion.setPlainText(json.dumps(command, indent=4))
                self.current_valid_command_name = command["name"].lower()
                return

    def populate_fields(self, container_widget, data: dict) -> None:
        layout = container_widget.layout()
        if layout is None:
            layout = QFormLayout()
            container_widget.setLayout(layout)
        layout.setSizeConstraint(QLayout.SetMinAndMaxSize)

        font = QFont()
        font.setPointSize(10)

        for key, value in data.items():

            text = str(value)
            if key in self._packet_fields:
                # Key already exists update its label's text.
                self._packet_fields[key].setText(text)
            else:
                # New key add a new row and cache the value label.
                field_label = QLabel(str(key))
                field_label.setFont(font)
                value_label = QLabel(text)
                value_label.setFont(font)

                layout.addRow(field_label, value_label)
                self._packet_fields[key] = value_label


    def clearTM(self):
        self.adapter.recvd_packets = 0
        self.packet_history_table.clearContents()
        self.packet_history_table.setRowCount(0)
        self.raw_history_table.clearContents()
        self.raw_history_table.setRowCount(0)

        eventLogger.info('Telemetry counter has been reset to 0')


    def updatePacketCounter(self, count):
        self.packet_counter_label.setText('Received packets: ' + str(count))


    def updateLink(self, up):
        if up:
            self.link_label.setText('TM FLOW')
            self.link_label.setStyleSheet('background-color: rgb(0, 255, 0)')
        else:
            self.link_label.setText('NO TM')
            self.link_label.setStyleSheet('background-color: rgb(255, 0, 0)')


    def update(self, csp, crc):
        try:
            csp_crc = csp.getCRC32C() #calculate the CRC of the CSP packet
            sent_crc = int.from_bytes(crc, byteorder='big') #Check what was attached as a CRC

            
            packet = csp.csp_bytes.hex()
            data = decode_packet_safe(packet)
            self.populate_fields(self.all_tm_widget, data)

            if csp_crc == sent_crc: #Compare
                crc_ok = True

                if csp.isBeacon():
                    temp_brd,\
                    temp_pa,\
                    last_rssi,\
                    last_rferr,\
                    tx_count,\
                    rx_count,\
                    tx_bytes,\
                    rx_bytes,\
                    active_conf,\
                    boot_count,\
                    boot_cause,\
                    last_contact,\
                    bgnd_rssi,\
                    tx_duty,\
                    tot_tx_count,\
                    tot_rx_count,\
                    tot_tx_bytes,\
                    tot_rx_bytes =  csp.getBeaconContents()

                    self.temp_brd_label.setText(str(temp_brd/10.0))
                    self.temp_pa_label.setText(str(temp_pa/10.0))
                    self.last_rssi_label.setText(str(last_rssi))
                    self.last_rferr_label.setText(str(last_rferr))
                    self.tx_count_label.setText(str(tx_count))
                    self.rx_count_label.setText(str(rx_count))
                    self.tx_bytes_label.setText(str(tx_bytes))
                    self.rx_bytes_label.setText(str(rx_bytes))
                    self.active_conf_label.setText(str(active_conf))
                    self.boot_count_label.setText(str(boot_count))
                    self.boot_cause_label.setText(str(boot_cause))
                    self.last_contact_label.setText(str(last_contact))
                    self.bgnd_rssi_label.setText(str(bgnd_rssi))
                    self.tx_duty_label.setText(str(tx_duty))
                    self.tot_tx_count_label.setText(str(tot_tx_count))
                    self.tot_rx_count_label.setText(str(tot_rx_count))
                    self.tot_tx_bytes_label.setText(str(tot_tx_bytes))
                    self.tot_rx_bytes_label.setText(str(tot_rx_bytes))


                    current_packet_count = self.packet_history_table.rowCount()
                    self.packet_history_table.insertRow(current_packet_count)
                    self.packet_history_table.setItem(current_packet_count, 0, QTableWidgetItem(str(datetime.datetime.utcnow())))
                    self.packet_history_table.setItem(current_packet_count, 1, QTableWidgetItem('Beacon'))
                    self.packet_history_table.setItem(current_packet_count, 2, QTableWidgetItem(str(csp.getLength())))
                    self.packet_history_table.setItem(current_packet_count, 3, QTableWidgetItem(str(crc_ok)))
                    self.packet_history_table.setItem(current_packet_count, 4, QTableWidgetItem(str(csp.priority)))
                    self.packet_history_table.setItem(current_packet_count, 5, QTableWidgetItem(NODES[str(csp.source)]))
                    self.packet_history_table.setItem(current_packet_count, 6, QTableWidgetItem(str(csp.destination)))
                    self.packet_history_table.setItem(current_packet_count, 7, QTableWidgetItem(str(csp.dest_port)))
                    self.packet_history_table.setItem(current_packet_count, 8, QTableWidgetItem(str(csp.source_port)))
                    self.packet_history_table.setItem(current_packet_count, 9, QTableWidgetItem(str(csp.flags)))

                    self.packet_history_table.item(current_packet_count, 3).setBackground(QtGui.QColor(0, 255, 0))
                    self.packet_history_table.scrollToBottom()


                    self.raw_history_table.insertRow(current_packet_count)
                    self.raw_history_table.setItem(current_packet_count, 0, QTableWidgetItem(str(datetime.datetime.utcnow())))
                    self.raw_history_table.setItem(current_packet_count, 1, QTableWidgetItem(str(csp.getHex())))
                    self.raw_history_table.setItem(current_packet_count, 2, QTableWidgetItem(str(csp.getLength())))
                    self.raw_history_table.scrollToBottom()

                    parsed_data = '{iso_time},{temp_brd},{temp_pa},{last_rssi},{last_rferr},{tx_count},{rx_count},{tx_bytes},{rx_bytes},{active_conf},{boot_count},{boot_cause},{last_contact},{bgnd_rssi},{tx_duty},{tot_tx_count},{tot_rx_count},{tot_tx_bytes},{tot_rx_bytes}'.format(iso_time=datetime.datetime.utcnow().isoformat(),\
                                                            temp_brd=temp_brd,\
                                                            temp_pa=temp_pa,\
                                                            last_rssi=last_rssi,\
                                                            last_rferr=last_rferr,\
                                                            tx_count=tx_count,\
                                                            rx_count=rx_count,\
                                                            tx_bytes=tx_bytes,\
                                                            rx_bytes=rx_bytes,\
                                                            active_conf=active_conf,\
                                                            boot_count=boot_count,\
                                                            boot_cause=boot_cause,\
                                                            last_contact=last_contact,\
                                                            bgnd_rssi=bgnd_rssi,\
                                                            tx_duty=tx_duty,\
                                                            tot_tx_count=tot_tx_count,\
                                                            tot_rx_count=tot_rx_count,\
                                                            tot_tx_bytes=tot_tx_bytes,\
                                                            tot_rx_bytes=tot_rx_bytes)


                    eventLogger.info('Received CSP beacon packet of length {LEN} bytes + 4 byte CRC32C check: OK'.format(LEN=csp.getLength()))
                    raw_data = str(csp.getHex()) + str(crc.hex())
                    raw_frame = '{iso_time},{data}'.format(iso_time=datetime.datetime.utcnow().isoformat(), data=raw_data)
                    rawLogger.info(raw_frame)
                    parsedBeaconLogger.info(parsed_data)

                else:
                    current_packet_count = self.packet_history_table.rowCount()
                    self.packet_history_table.insertRow(current_packet_count)
                    self.packet_history_table.setItem(current_packet_count, 0, QTableWidgetItem(str(datetime.datetime.utcnow())))
                    self.packet_history_table.setItem(current_packet_count, 1, QTableWidgetItem('SPP over CSP'))
                    self.packet_history_table.setItem(current_packet_count, 2, QTableWidgetItem(str(csp.getLength())))
                    self.packet_history_table.setItem(current_packet_count, 3, QTableWidgetItem(str(crc_ok)))
                    self.packet_history_table.setItem(current_packet_count, 4, QTableWidgetItem(str(csp.priority)))
                    self.packet_history_table.setItem(current_packet_count, 5, QTableWidgetItem(NODES[str(csp.source)]))
                    self.packet_history_table.setItem(current_packet_count, 6, QTableWidgetItem(str(csp.destination)))
                    self.packet_history_table.setItem(current_packet_count, 7, QTableWidgetItem(str(csp.dest_port)))
                    self.packet_history_table.setItem(current_packet_count, 8, QTableWidgetItem(str(csp.source_port)))
                    self.packet_history_table.setItem(current_packet_count, 9, QTableWidgetItem(str(csp.flags)))

                    self.packet_history_table.item(current_packet_count, 3).setBackground(QtGui.QColor(0, 255, 0))
                    self.packet_history_table.scrollToBottom()

                    self.raw_history_table.insertRow(current_packet_count)
                    self.raw_history_table.setItem(current_packet_count, 0, QTableWidgetItem(str(datetime.datetime.utcnow())))
                    self.raw_history_table.setItem(current_packet_count, 1, QTableWidgetItem(str(csp.getHex())))
                    self.raw_history_table.setItem(current_packet_count, 2, QTableWidgetItem(str(csp.getLength())))
                    self.raw_history_table.scrollToBottom()

                    eventLogger.info('Received SPP over CSP packet of length {LEN} bytes + 4 byte CRC32C check: OK'.format(LEN=csp.getLength()))
                    data = str(csp.getHex()) + str(crc.hex())
                    raw_frame = '{iso_time},{data}'.format(iso_time=datetime.datetime.utcnow().isoformat(), data=data)
                    rawLogger.info(raw_frame)

            else:
                crc_ok = False

                current_packet_count = self.packet_history_table.rowCount()

                self.packet_history_table.insertRow(current_packet_count)
                self.packet_history_table.setItem(current_packet_count, 0, QTableWidgetItem(str(datetime.datetime.utcnow())))
                self.packet_history_table.setItem(current_packet_count, 1, QTableWidgetItem('--'))
                self.packet_history_table.setItem(current_packet_count, 2, QTableWidgetItem(str(csp.getLength())))
                self.packet_history_table.setItem(current_packet_count, 3, QTableWidgetItem(str(crc_ok)))
                self.packet_history_table.setItem(current_packet_count, 4, QTableWidgetItem(str(csp.priority)))
                self.packet_history_table.setItem(current_packet_count, 5, QTableWidgetItem(str(csp.source)))
                self.packet_history_table.setItem(current_packet_count, 6, QTableWidgetItem(str(csp.destination)))
                self.packet_history_table.setItem(current_packet_count, 7, QTableWidgetItem(str(csp.dest_port)))
                self.packet_history_table.setItem(current_packet_count, 8, QTableWidgetItem(str(csp.source_port)))
                self.packet_history_table.setItem(current_packet_count, 9, QTableWidgetItem(str(csp.flags)))
                self.packet_history_table.item(current_packet_count, 3).setBackground(QtGui.QColor(255, 0, 0))
                self.packet_history_table.scrollToBottom()

                self.raw_history_table.insertRow(current_packet_count)
                self.raw_history_table.setItem(current_packet_count, 0, QTableWidgetItem(str(datetime.datetime.utcnow())))
                self.raw_history_table.setItem(current_packet_count, 1, QTableWidgetItem(str(csp.getHex())))
                self.raw_history_table.setItem(current_packet_count, 2, QTableWidgetItem(str(csp.getLength())))
                self.raw_history_table.scrollToBottom()

                eventLogger.error('Received CSP packet of length {LEN} bytes - Failed CRC32C!'.format(LEN=str(csp.getLength())))
                #Don't write to any logs.
        except Exception as e:
            eventLogger.error('Error occured: {ERR}'.format(ERR=e))


class CSP(object):
    """
    Reused from:
    https://github.com/daniestevez/gr-satellites/blob/master/python/csp_header.py
    """

    def __init__(self, csp_bytes):
        if len(csp_bytes) < 4:
            raise ValueError("Malformed CSP packet (too short)")
        self.csp_bytes = csp_bytes
        csp = struct.unpack(">I", csp_bytes[0:4])[0]
        self.priority = (csp >> 30) & 0x3
        self.source = (csp >> 25) & 0x1f
        self.destination = (csp >> 20) & 0x1f
        self.dest_port = (csp >> 14) & 0x3f
        self.source_port = (csp >> 8) & 0x3f
        self.reserved = (csp >> 4) & 0xf
        self.hmac = (csp >> 3) & 1
        self.xtea = (csp >> 2) & 1
        self.rdp = (csp >> 1) & 1
        self.crc = csp & 1
        self.flags=int('{HMAC}{XTEA}{RDP}{CRC}'.format(HMAC=self.hmac, XTEA=self.xtea, RDP=self.rdp, CRC=self.crc))

    def toString(self):
        return ("""CSP header:
        Priority:\t\t{}
        Source:\t\t\t{}
        Destination:\t\t{}
        Destination port:\t{}
        Source port:\t\t{}
        Reserved field:\t\t{}
        HMAC:\t\t\t{}
        XTEA:\t\t\t{}
        RDP:\t\t\t{}
        CRC:\t\t\t{}""".format(
            self.priority, self.source, self.destination, self.dest_port,
            self.source_port, self.reserved, self.hmac, self.xtea, self.rdp,
            self.crc))

    def isBeacon(self):
        if self.priority == 3 and self.source == 5 and self.destination == 10 and self.dest_port == 31 and self.source_port==0 and self.getLength() == 58:
            return True
        else:
            return False

    def getCRC32C(self):
        calculated_CRC32C = Crc32c.calc(self.csp_bytes)
        return calculated_CRC32C

    def getLength(self):
        return len(self.csp_bytes)

    def getHex(self):
        return self.csp_bytes.hex()

    def getBeaconContents(self):
        if self.isBeacon():
            payload = struct.unpack('>4h 4I B H 2I h B 4I', self.csp_bytes[4:]) #nanocom beacon format
            print(payload, type(payload))
            return payload


class TMadapter(QThread):

    packet = pyqtSignal(object, object)
    link = pyqtSignal(bool)
    packet_count = pyqtSignal(int)

    recvd_packets = 0

    active = False
    host = None

    def __init__(self, parent, ip, port):
        QtCore.QThread.__init__(self, parent)
        self.parent = parent
        self.active = True

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.SUB)
        self.host = 'tcp://' + ip + ':' + str(port)
        self.socket.connect(self.host)
        self.socket.setsockopt_string(zmq.SUBSCRIBE, "")
        self.socket.setsockopt(zmq.RCVTIMEO, 15000) #Timeout set to 15s, beacon frame is radiated every 10s by the spacecraft

        eventLogger.info('Started listening on {HOST} for incoming packets from GR flowgraph'.format(HOST=self.host))

    def run(self):
        while self.active:
            try:
                data = self.socket.recv()
                crc = data[-4:] #4 last bytes are CRC32-C
                csp = CSP(data[:(len(data)-4)])#Everything but the last 4 bytes belongs to the CSP packet
                self.packet.emit(csp, crc)
                self.recvd_packets = self.recvd_packets + 1
                self.packet_count.emit(self.recvd_packets)
                self.link.emit(True)
            except Exception as e:
                eventLogger.warning('No packets received in the last 15 seconds...{ERR}'.format(ERR=e))
                self.link.emit(False)

def setup_logger(name, log_file, formatter, level=logging.INFO):
    fileHandler = logging.FileHandler(log_file)
    streamHandler = logging.StreamHandler()

    fileHandler.setFormatter(formatter)
    streamHandler.setFormatter(formatter)

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.addHandler(fileHandler)
    logger.addHandler(streamHandler)

    return logger


if __name__ == '__main__':
    path = os.path.dirname(os.path.abspath(__file__))
    log_dir = tempfile.mkdtemp(prefix="log_OPSSAT")

    try:
        # print(decode_packet_safe("C2A28A0001435453310100FA1FAF6B0DCE18008B37B472A00100000501D40E21001951010001044E501C000000000000FF7FAC03C60E0000A30000000000000000000000000000000000000091050000030201020002000248656C6C6F2066726F6D2043616C67617279546F53706163652046726F6E746965725361740000000000454E440014932758"))

        formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
        eventLogger = setup_logger('first_logger', os.path.join(log_dir, 'gui_event.log'), formatter, level=logging.INFO)

        plainFormatter = logging.Formatter('%(message)s')
        rawLogger = setup_logger('second_logger', os.path.join(log_dir, 'raw.log'), plainFormatter, level=logging.INFO)
        parsedBeaconLogger = setup_logger('third_logger', os.path.join(log_dir, 'parsed_beacon.log'), plainFormatter, level=logging.INFO)
        

        a = QApplication(sys.argv)
        a.setWindowIcon(QIcon(os.path.join(log_dir, 'gui/img/1200px-ESA_logo_simple.png')))
        app = Main()
        app.show()
        a.exec_()
    finally:
        # Nettoyer les fichiers temporaires une fois que l'application est arrêtée
        logging.shutdown()
        shutil.rmtree(log_dir)
        os._exit(0)