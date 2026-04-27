# FFichCAN

FFichCAN is a feature-rich, open-source CAN Bus tool, designed specifically to simplify engagement with Peak-System PCAN adapters. It provides a comprehensive interface for CAN and CAN-FD networks, alongside advanced diagnostics capabilities.

## Features

- **Dynamic Hardware Discovery:** Automatic detection of available PEAK-System adapters.
- **CAN & CAN-FD Support:** Toggle between standard CAN 2.0b and extended data-rate CAN-FD natively. In FD mode, you can specify both **Nominal Bitrate** and **Data Bitrate** for maximum performance.
- **Traffic Interception:** Real-time log of both incoming (RX) and echoing transmitted (TX) messages, including accurate message period tracking.
- **Dynamic Transmit Dashboard:** A scalable, scrollable layout allowing you to define multiple transmit frames simultaneously. Each frame supports unique ID, DLC, extended/FD toggles, 8 clean hex data byte cells, and periodic background injection.
- **CAN DBC association:** Allow to import a Vector DBC file, in order to associate real signal values.
- **Diagnostics (UDS & ISO-TP):** A dedicated diagnostic interface allowing you to transmit precise payloads over ISO 15765-2 (ISO-TP) and receive ISO 14229 (UDS) responses.

## How it looks like

### CAN Traffic View
<img width="1402" height="932" alt="image" src="https://github.com/user-attachments/assets/c6f64ab1-d4b9-45f2-bf5a-c18215e05821" />

### Diagnostic View
<img width="1402" height="932" alt="image" src="https://github.com/user-attachments/assets/fd90a811-c88e-4349-a22c-9b8f06286799" />
