# FFichCAN

FFichCAN is a feature-rich, open-source Graphical User Interface (GUI) wrapper for the `python-can` library, built entirely in Python using `tkinter`. Designed specifically to simplify engagement with Peak-System PCAN adapters, it provides a comprehensive interface for CAN and CAN-FD networks, alongside advanced diagnostics capabilities.

## Features

- **Dynamic Hardware Discovery:** Automatic detection of available PEAK-System adapters.
- **CAN & CAN-FD Support:** Toggle between standard CAN 2.0b and extended data-rate CAN-FD natively. In FD mode, you can specify both **Nominal Bitrate** and **Data Bitrate** for maximum performance.
- **Traffic Interception:** Real-time log of both incoming (RX) and echoing transmitted (TX) messages, including accurate message period tracking.
- **Dynamic Transmit Dashboard:** A scalable, scrollable layout allowing you to define multiple transmit frames simultaneously. Each frame supports unique ID, DLC, extended/FD toggles, 8 clean hex data byte cells, and periodic background injection.
- **Diagnostics (UDS & ISO-TP):** A dedicated diagnostic interface allowing you to transmit precise payloads over ISO 15765-2 (ISO-TP) and receive ISO 14229 (UDS) responses.

## Installation

Ensure you have Python 3.8+ and the PEAK-System PCAN-Basic drivers installed on your operating system.

```bash
pip install -r requirements.txt
```

## Usage

Simply run the application script:
```bash
python can_interface.py
```
