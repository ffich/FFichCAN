# FFichCAN

FFichCAN is a feature-rich, open-source Graphical User Interface (GUI) wrapper for the `python-can` library, built entirely in Python using `tkinter`. Designed specifically to simplify engagement with Peak-System PCAN adapters, it provides a comprehensive interface for CAN and CAN-FD networks, alongside advanced diagnostics capabilities.

## Features

- **Dynamic Hardware Discovery:** Automatic detection of available PEAK-System adapters.
- **CAN & CAN-FD Support:** Toggle between standard CAN 2.0b and extended data-rate CAN-FD natively.
- **Traffic Interception:** Real-time log of both incoming (RX) and echoing transmitted (TX) messages, including accurate message period tracking.
- **Dynamic Transmit Dashboard:** A scalable, scrollable layout allowing you to define multiple transmit frames simultaneously. Each frame supports unique ID, DLC, extended/FD toggles, 8 clean hex data byte cells, and periodic background injection.
- **Diagnostics (UDS & ISO-TP):** A dedicated diagnostic interface allowing you to transmit precise payloads over ISO 15765-2 (ISO-TP) and receive ISO 14229 (UDS) responses.

## GUI
Here is an imahe of how the GUI looks like:

<img width="1002" height="882" alt="image" src="https://github.com/user-attachments/assets/f8314b8f-ea6d-4e91-b811-e2ce48eaf5a9" />
