import tkinter as tk
import os
import json
from tkinter import ttk, messagebox, filedialog
import can
import can.interfaces.pcan
import cantools
import threading
import time
from datetime import datetime
import isotp
from udsoncan.connections import PythonIsoTpConnection

class IsotpBusWrapper:
    """Wrapper for python-can bus to work with isotp.CanStack while sharing with a Notifier."""
    def __init__(self, bus, reader):
        self.bus = bus
        self.reader = reader
        # Proxy common attributes that might be checked by libraries
        self.filters = getattr(bus, 'filters', None)
        self.channel_info = getattr(bus, 'channel_info', 'IsotpBusWrapper')
    
    def send(self, msg, timeout=None):
        self.bus.send(msg, timeout)
        
    def recv(self, timeout=None):
        # isotp.CanStack expects a non-blocking recv or one with a timeout
        return self.reader.get_message(timeout)

    def shutdown(self):
        pass # Do not shutdown the main bus

# Register the wrapper as a python-can BusABC to satisfy type checks in libraries like isotp
can.BusABC.register(IsotpBusWrapper)

class CanInterfaceApp:
    def __init__(self, root):
        self.root = root
        self.root.title("FFichCAN")
        self.root.geometry("1400x900")
        self.root.minsize(1200, 700)

        self.bus = None
        self.notifier = None
        self.is_connected = False
        self.message_items = {}
        self.last_timestamps = {}
        self.tx_rows = []
        self.uds_buffer = None
        self.uds_overwrite_var = tk.BooleanVar(value=False)
        self.uds_fd_var = tk.BooleanVar(value=False)
        self.show_ascii_var = tk.BooleanVar(value=False)
        
        # DBC state
        self.db = None
        self.db_path = None
        self.signal_items = {} # (msg_id, sig_name) -> tree_item_id
        self.data_overflow_items = {} # (msg_id_hex, chunk_idx) -> iid
        self.message_data = {} # msg_id_hex -> full_bytes
        self.last_ui_update = {} # msg_id -> last_time_sig_updated
        self.signal_values = {} # sig_name -> latest_value

        self.style = ttk.Style()
        # Removed custom rowheight

        self.create_widgets()
        
        # Load last config if exists
        self.root.after(100, self.auto_load)

    def create_widgets(self):
        # Top Frame for connection
        control_frame = ttk.Frame(self.root)
        control_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=5)

        try:
            configs = can.detect_available_configs(interfaces=['pcan'])
            channels = list(set([c['channel'] for c in configs]))
            if not channels:
                channels = ["PCAN_USBBUS1"]
        except Exception:
            channels = ["PCAN_USBBUS1", "PCAN_USBBUS2"]

        ttk.Label(control_frame, text="Channel:").pack(side=tk.LEFT, padx=(0, 5))
        self.channel_cb = ttk.Combobox(control_frame, values=channels, width=15)
        self.channel_cb.set(channels[0])
        self.channel_cb.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(control_frame, text="Bitrate (Nominal):").pack(side=tk.LEFT, padx=5)
        self.bitrate_cb = ttk.Combobox(control_frame, values=["125000", "250000", "500000", "1000000"], width=10)
        self.bitrate_cb.set("500000")
        self.bitrate_cb.pack(side=tk.LEFT, padx=5)

        self.data_bitrate_label = ttk.Label(control_frame, text="FD Data Rate:")
        self.data_bitrate_cb = ttk.Combobox(control_frame, values=["1000000", "2000000", "4000000", "5000000", "8000000"], width=10)
        self.data_bitrate_cb.set("2000000")
        
        self.fd_var = tk.BooleanVar(value=False)
        self.fd_check = ttk.Checkbutton(control_frame, text="FD Mode", variable=self.fd_var, command=self.on_fd_toggle)
        self.fd_check.pack(side=tk.LEFT, padx=5)
        
        # Initial visibility
        self.on_fd_toggle()

        self.connect_btn = ttk.Button(control_frame, text="Connect", command=self.toggle_connection)
        self.connect_btn.pack(side=tk.LEFT)
        
        self.clear_btn = ttk.Button(control_frame, text="Clear Msg", command=self.clear_messages)
        self.clear_btn.pack(side=tk.LEFT, padx=5)

        self.dbc_btn = ttk.Button(control_frame, text="Load DBC", command=self.load_dbc)
        self.dbc_btn.pack(side=tk.LEFT, padx=5)

        self.save_cfg_btn = ttk.Button(control_frame, text="Save Config", command=self.save_config)
        self.save_cfg_btn.pack(side=tk.LEFT)

        self.load_cfg_btn = ttk.Button(control_frame, text="Load Config", command=self.load_config)
        self.load_cfg_btn.pack(side=tk.LEFT, padx=5)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.tab_bus = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_bus, text="Bus Control")
        
        self.tab_signals = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_signals, text="Signals Monitor")
        
        self.tab_uds = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_uds, text="Diagnostics (UDS)")
        
        self.create_signals_tab(self.tab_signals)

        # Main Table for incoming data
        data_frame = ttk.LabelFrame(self.tab_bus, text="Incoming Messages")
        data_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.tree = ttk.Treeview(data_frame, columns=("Time", "Period", "RX/TX", "Type", "ID", "DLC", "Data"), show="tree headings")
        self.tree.heading("#0", text="Message/Signal Name")
        self.tree.heading("Time", text="Time")
        self.tree.heading("Period", text="Period (ms)")
        self.tree.heading("RX/TX", text="RX/TX")
        self.tree.heading("Type", text="Type")
        self.tree.heading("ID", text="ID (Hex)")
        self.tree.heading("DLC", text="DLC")
        self.tree.heading("Data", text="Data / Value")
        
        self.tree.column("#0", width=180, anchor=tk.W)
        self.tree.column("Time", width=110, anchor=tk.W)
        self.tree.column("Period", width=80, anchor=tk.CENTER)
        self.tree.column("RX/TX", width=60, anchor=tk.CENTER)
        self.tree.column("Type", width=70, anchor=tk.CENTER)
        self.tree.column("ID", width=100, anchor=tk.W)
        self.tree.column("DLC", width=50, anchor=tk.CENTER)
        self.tree.column("Data", width=300, anchor=tk.W)
        
        v_scrollbar = ttk.Scrollbar(data_frame, orient=tk.VERTICAL, command=self.tree.yview)
        # Keep horizontal scrollbar but it will be less needed
        h_scrollbar = ttk.Scrollbar(data_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscroll=v_scrollbar.set, xscroll=h_scrollbar.set)
        
        self.tree.grid(row=0, column=0, sticky="nsew")
        v_scrollbar.grid(row=0, column=1, sticky="ns")
        h_scrollbar.grid(row=1, column=0, sticky="ew")
        
        data_frame.grid_rowconfigure(0, weight=1)
        data_frame.grid_columnconfigure(0, weight=1)

        self.tree.bind("<Double-1>", self.on_message_double_click)

        # Bottom Frame for sending
        self.send_frame = ttk.LabelFrame(self.tab_bus, text="Transmit Messages")
        self.send_frame.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=False, padx=10, pady=10)

        tx_tools_frame = ttk.Frame(self.send_frame)
        tx_tools_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        ttk.Button(tx_tools_frame, text="Add TX Message", command=self.add_tx_row).pack(side=tk.LEFT)

        self.tx_canvas = tk.Canvas(self.send_frame, height=150)
        self.tx_scrollbar = ttk.Scrollbar(self.send_frame, orient="vertical", command=self.tx_canvas.yview)
        self.tx_scrollable_frame = ttk.Frame(self.tx_canvas)

        self.tx_scrollable_frame.bind(
            "<Configure>",
            lambda e: self.tx_canvas.configure(
                scrollregion=self.tx_canvas.bbox("all")
            )
        )

        self.tx_canvas.create_window((0, 0), window=self.tx_scrollable_frame, anchor="nw")
        self.tx_canvas.configure(yscrollcommand=self.tx_scrollbar.set)

        self.tx_canvas.pack(side="left", fill="both", expand=True)
        self.tx_scrollbar.pack(side="right", fill="y")

        self.add_tx_row()
        
        self.create_uds_widgets(self.tab_uds)

    def create_uds_widgets(self, parent):
        config_frame = ttk.LabelFrame(parent, text="UDS Configuration")
        config_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=5)
        
        ttk.Label(config_frame, text="Target TX ID (Hex):").pack(side=tk.LEFT, padx=5)
        self.uds_tx_id = ttk.Entry(config_frame, width=8)
        self.uds_tx_id.insert(0, "7E0")
        self.uds_tx_id.pack(side=tk.LEFT, padx=5)
        
        ttk.Label(config_frame, text="Target RX ID (Hex):").pack(side=tk.LEFT, padx=5)
        self.uds_rx_id = ttk.Entry(config_frame, width=8)
        self.uds_rx_id.insert(0, "7E8")
        self.uds_rx_id.pack(side=tk.LEFT, padx=5)
        
        ttk.Checkbutton(config_frame, text="Use CAN FD", variable=self.uds_fd_var).pack(side=tk.LEFT, padx=10)
        
        req_frame = ttk.LabelFrame(parent, text="UDS Request")
        req_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=5)
        
        ttk.Label(req_frame, text="Service ID (Hex):").pack(side=tk.LEFT, padx=5)
        self.uds_sid = ttk.Entry(req_frame, width=5)
        self.uds_sid.insert(0, "10")
        self.uds_sid.pack(side=tk.LEFT, padx=5)
        
        ttk.Label(req_frame, text="Payload (Hex):").pack(side=tk.LEFT, padx=5)
        self.uds_payload = ttk.Entry(req_frame, width=30)
        self.uds_payload.insert(0, "01")
        self.uds_payload.pack(side=tk.LEFT, padx=5)
        self.uds_payload.bind("<KeyRelease>", self.format_uds_payload)
        
        ttk.Button(req_frame, text="Send Request", command=self.send_uds_request).pack(side=tk.LEFT, padx=10)
        ttk.Button(req_frame, text="Clear Log", command=self.clear_uds_log).pack(side=tk.LEFT)
        ttk.Checkbutton(req_frame, text="Overwrite Mode", variable=self.uds_overwrite_var).pack(side=tk.LEFT, padx=10)
        ttk.Checkbutton(req_frame, text="Show ASCII", variable=self.show_ascii_var).pack(side=tk.LEFT, padx=10)
        
        log_frame = ttk.LabelFrame(parent, text="UDS Log")
        log_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.uds_log = tk.Text(log_frame, height=15)
        self.uds_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        scroll = ttk.Scrollbar(log_frame, command=self.uds_log.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.uds_log.config(yscrollcommand=scroll.set)

    def log_uds(self, msg):
        if self.uds_overwrite_var.get():
            self.uds_log.delete(1.0, tk.END)
        self.uds_log.insert(tk.END, f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}\n")
        self.uds_log.see(tk.END)

    def clear_uds_log(self):
        self.uds_log.delete(1.0, tk.END)

    def format_uds_payload(self, event):
        # Auto-space formatting for Hex payload
        if event.keysym in ("BackSpace", "Delete", "Left", "Right", "Shift_L", "Shift_R"): return
        
        # Get current state
        original_content = self.uds_payload.get()
        cursor_pos = self.uds_payload.index(tk.INSERT)
        
        # Count non-space characters before the cursor
        chars_before_cursor = len(original_content[:cursor_pos].replace(" ", ""))
        
        # Process content
        val = original_content.replace(" ", "").upper()
        val = "".join(c for c in val if c in "0123456789ABCDEF")
        
        # Format with spaces
        fmt = " ".join(val[i:i+2] for i in range(0, len(val), 2))
        
        # Only update if changed to avoid unnecessary cursor jumping
        if original_content != fmt:
            self.uds_payload.delete(0, tk.END)
            self.uds_payload.insert(0, fmt)
            
            # Calculate new cursor position
            new_cursor_pos = 0
            chars_seen = 0
            for i, char in enumerate(fmt):
                if chars_seen == chars_before_cursor:
                    new_cursor_pos = i
                    break
                if char != " ":
                    chars_seen += 1
                new_cursor_pos = i + 1
                
            self.uds_payload.icursor(new_cursor_pos)

    def create_signals_tab(self, parent):
        search_frame = ttk.Frame(parent)
        search_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=5)
        
        ttk.Label(search_frame, text="Search Signals:").pack(side=tk.LEFT, padx=5)
        self.sig_search_var = tk.StringVar()
        self.sig_search_var.trace_add("write", lambda *args: self.filter_signals_list())
        ttk.Entry(search_frame, textvariable=self.sig_search_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        list_frame = ttk.Frame(parent)
        list_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.sig_tree = ttk.Treeview(list_frame, columns=("Name", "Value", "Message"), show="headings")
        self.sig_tree.heading("Name", text="Signal Name")
        self.sig_tree.heading("Value", text="Value")
        self.sig_tree.heading("Message", text="Message")
        
        self.sig_tree.column("Name", width=250)
        self.sig_tree.column("Value", width=150)
        self.sig_tree.column("Message", width=200)
        
        # Restore show headings because we don't need tree here
        self.sig_tree.configure(show="headings")

        scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.sig_tree.yview)
        self.sig_tree.configure(yscroll=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.sig_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self.monitor_sig_items = {} # sig_name -> tree_id

    def filter_signals_list(self):
        # Implementation for filtering if needed, for now we update all via latest_values
        pass

    def load_dbc(self):
        path = filedialog.askopenfilename(filetypes=[("DBC Files", "*.dbc"), ("All Files", "*.*")])
        if not path:
            return
            
        try:
            self.db = cantools.database.load_file(path)
            self.db_path = path
            self.dbc_btn.config(text=f"DBC: {os.path.basename(path)}")
            self.clear_messages()
            self.populate_signals_monitor()
            # Update TX rows to new database
            for row in self.tx_rows:
                row.update_dbc_options()
        except Exception as e:
            messagebox.showerror("DBC Error", f"Failed to load DBC:\n{e}")

    def save_config(self, path=None):
        if not path:
            path = filedialog.asksaveasfilename(defaultextension=".ffich", filetypes=[("FFichCAN Config", "*.ffich"), ("All Files", "*.*")])
        if not path:
            return
            
        config = {
            "version": "1.0",
            "connection": {
                "channel": self.channel_cb.get(),
                "bitrate": self.bitrate_cb.get(),
                "data_bitrate": self.data_bitrate_cb.get(),
                "fd": self.fd_var.get()
            },
            "dbc": self.db_path,
            "uds": {
                "tx_id": self.uds_tx_id.get(),
                "rx_id": self.uds_rx_id.get(),
                "fd": self.uds_fd_var.get(),
                "sid": self.uds_sid.get(),
                "payload": self.uds_payload.get()
            },
            "tx_rows": [row.get_state() for row in self.tx_rows]
        }
        
        try:
            with open(path, 'w') as f:
                json.dump(config, f, indent=4)
        except Exception as e:
            if not path.endswith("auto.ffich"): # Don't annoy on auto-save fail
                messagebox.showerror("Save Error", f"Failed to save config:\n{e}")

    def load_config(self, path=None):
        if not path:
            path = filedialog.askopenfilename(filetypes=[("FFichCAN Config", "*.ffich"), ("All Files", "*.*")])
        if not path or not os.path.exists(path):
            return
            
        try:
            with open(path, 'r') as f:
                config = json.load(f)
            
            # Connection
            conn = config.get("connection", {})
            self.channel_cb.set(conn.get("channel", ""))
            self.bitrate_cb.set(conn.get("bitrate", "500000"))
            self.data_bitrate_cb.set(conn.get("data_bitrate", "2000000"))
            self.fd_var.set(conn.get("fd", False))
            self.on_fd_toggle()
            
            # DBC
            dbc_path = config.get("dbc")
            if dbc_path and os.path.exists(dbc_path):
                # We reuse the logic from load_dbc but with path
                self.db = cantools.database.load_file(dbc_path)
                self.db_path = dbc_path
                self.dbc_btn.config(text=f"DBC: {os.path.basename(dbc_path)}")
                self.clear_messages()
                self.populate_signals_monitor()
            
            # UDS
            uds = config.get("uds", {})
            self.uds_tx_id.delete(0, tk.END)
            self.uds_tx_id.insert(0, uds.get("tx_id", "7E0"))
            self.uds_rx_id.delete(0, tk.END)
            self.uds_rx_id.insert(0, uds.get("rx_id", "7E8"))
            self.uds_fd_var.set(uds.get("fd", False))
            self.uds_sid.delete(0, tk.END)
            self.uds_sid.insert(0, uds.get("sid", "10"))
            self.uds_payload.delete(0, tk.END)
            self.uds_payload.insert(0, uds.get("payload", "01"))
            
            # TX Rows
            # Clear existing rows
            for row in list(self.tx_rows):
                row.destroy()
            
            rows_data = config.get("tx_rows", [])
            for r_state in rows_data:
                row = TxRow(self.tx_scrollable_frame, self)
                self.tx_rows.append(row)
                row.update_dbc_options()
                row.set_state(r_state)
                
        except Exception as e:
            if not path.endswith("auto.ffich"):
                messagebox.showerror("Load Error", f"Failed to load config:\n{e}")

    def auto_save(self):
        # Save to a hidden or local file
        path = os.path.join(os.path.dirname(__file__), "auto.ffich")
        self.save_config(path)

    def auto_load(self):
        path = os.path.join(os.path.dirname(__file__), "auto.ffich")
        if os.path.exists(path):
            self.load_config(path)

    def populate_signals_monitor(self):
        for item in self.sig_tree.get_children():
            self.sig_tree.delete(item)
        self.monitor_sig_items.clear()
        
        if not self.db:
            return
            
        # Sort signals by message and name
        all_sigs = []
        for msg in self.db.messages:
            for sig in msg.signals:
                all_sigs.append((sig.name, msg.name))
        
        all_sigs.sort()
        for name, msg_name in all_sigs:
            iid = self.sig_tree.insert("", tk.END, values=(name, "-", msg_name))
            self.monitor_sig_items[name] = iid

    def send_uds_request(self):
        if not self.is_connected:
            messagebox.showwarning("Warning", "Not connected to any CAN interface.")
            return

        try:
            txid = int(self.uds_tx_id.get(), 16)
            rxid = int(self.uds_rx_id.get(), 16)
            sid = int(self.uds_sid.get(), 16)
            
            payload_str = self.uds_payload.get().replace(" ", "")
            payload = bytes.fromhex(payload_str) if payload_str else b""
        except ValueError:
            messagebox.showerror("Error", "Please enter valid hexadecimal values.")
            return
            
        # Run in a separate thread so UI doesn't freeze during ISO-TP
        threading.Thread(target=self._uds_task, args=(txid, rxid, sid, payload), daemon=True).start()

    def _uds_task(self, txid, rxid, sid, payload):
        if not self.uds_buffer:
            self.root.after(0, self.log_uds, "Error: UDS Buffer not initialized.")
            return

        try:
            # Clear old messages from buffer
            while True:
                msg = self.uds_buffer.get_message(timeout=0)
                if msg is None:
                    break

            addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=txid, rxid=rxid)
            # Use wrapper to share bus with Notifier
            wrapper = IsotpBusWrapper(self.bus, self.uds_buffer)
            
            # Configure ISO-TP for FD if enabled
            isotp_params = {}
            if self.uds_fd_var.get():
                isotp_params = {
                    'tx_data_length': 64, # Max FD length
                    'can_fd': True
                }
            
            stack = isotp.CanStack(wrapper, address=addr, params=isotp_params)
            conn = PythonIsoTpConnection(stack)
            conn.open()
            
            raw_payload = bytes([sid]) + payload
            payload_fmt = " ".join(f"{b:02X}" for b in raw_payload)
            self.root.after(0, self.log_uds, f"Sending: {payload_fmt}")
            
            conn.send(raw_payload)
            response_data = conn.wait_frame(timeout=2.0)
            
            if response_data is not None:
                resp_hex = " ".join(f"{b:02X}" for b in response_data)
                log_msg = f"Response received: {resp_hex}"
                
                if self.show_ascii_var.get():
                    # Skip UDS header (SID + ID) for ASCII decoding
                    # e.g., for ReadDataByIdentifier (0x22), skip 3 bytes (0x62 + DID)
                    start_idx = 0
                    if len(response_data) > 0 and response_data[0] == (sid + 0x40):
                        if sid in (0x22, 0x2E) and len(response_data) >= 3:
                            start_idx = 3
                        else:
                            start_idx = 1
                    
                    ascii_str = "".join(chr(b) for b in response_data[start_idx:] if 32 <= b <= 126)
                    if ascii_str:
                        log_msg += f"\n  ↳ ASCII: {ascii_str}"
                
                self.root.after(0, self.log_uds, log_msg)
            else:
                self.root.after(0, self.log_uds, "Timeout waiting for response.")
                
            conn.close()
        except Exception as e:
            self.root.after(0, self.log_uds, f"Error: {e}")

    def on_fd_toggle(self):
        if self.fd_var.get():
            self.data_bitrate_label.pack(side=tk.LEFT, padx=(10, 5))
            self.data_bitrate_cb.pack(side=tk.LEFT, padx=5)
        else:
            self.data_bitrate_label.pack_forget()
            self.data_bitrate_cb.pack_forget()

    def toggle_connection(self):
        if self.is_connected:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        channel = self.channel_cb.get()
        bitrate_str = self.bitrate_cb.get()
        is_fd = self.fd_var.get()
        
        if not channel:
            messagebox.showerror("Error", "Please specify a channel.")
            return
            
        if not bitrate_str:
            messagebox.showwarning("Warning", "Please select a bitrate.")
            return

        try:
            # Initialize python-can bus for Peak adapter
            if is_fd:
                data_bitrate_str = self.data_bitrate_cb.get()
                nom_bitrate = int(bitrate_str)
                data_bitrate = int(data_bitrate_str)
                
                # Use explicit timing parameters for PCAN-FD (80MHz clock)
                # This ensures stability across different driver versions
                params = {
                    'channel': channel,
                    'fd': True,
                    'f_clock_mhz': 80,
                    'nom_brp': 4 if nom_bitrate == 500000 else (2 if nom_bitrate == 1000000 else 8),
                    'nom_tseg1': 31,
                    'nom_tseg2': 8,
                    'nom_sjw': 8,
                    'receive_own_messages': True
                }
                
                # Dynamic data bitrate timings for 80MHz
                if data_bitrate == 2000000:
                    params.update({'data_brp': 1, 'data_tseg1': 31, 'data_tseg2': 8, 'data_sjw': 8, 'ssp_offset': 32})
                elif data_bitrate == 1000000:
                    params.update({'data_brp': 2, 'data_tseg1': 31, 'data_tseg2': 8, 'data_sjw': 8, 'ssp_offset': 32})
                elif data_bitrate == 4000000:
                    params.update({'data_brp': 1, 'data_tseg1': 15, 'data_tseg2': 4, 'data_sjw': 4, 'ssp_offset': 16})
                elif data_bitrate == 8000000:
                    params.update({'data_brp': 1, 'data_tseg1': 7, 'data_tseg2': 2, 'data_sjw': 2, 'ssp_offset': 8})
                else: # Default or others (e.g. 5M)
                    # Use python-can's internal calculation if possible, or fallback to 2M
                    params.update({'data_brp': 1, 'data_tseg1': 31, 'data_tseg2': 8, 'data_sjw': 8, 'ssp_offset': 32})
                
                self.bus = can.interfaces.pcan.PcanBus(**params)
            else:
                bitrate = int(bitrate_str)
                self.bus = can.interface.Bus(interface='pcan', channel=channel, bitrate=bitrate, receive_own_messages=True)
            
            self.uds_buffer = can.BufferedReader()
            self.notifier = can.Notifier(self.bus, [self.on_message_received, self.uds_buffer])
            
            self.is_connected = True
            self.connect_btn.config(text="Disconnect")
            self.channel_cb.config(state=tk.DISABLED)
            self.bitrate_cb.config(state=tk.DISABLED)
            self.fd_check.config(state=tk.DISABLED)
            self.data_bitrate_cb.config(state=tk.DISABLED) # Disable data bitrate when connected
            
        except Exception as e:
            messagebox.showerror("Connection Error", f"Failed to open PCAN interface:\n{e}\n\nDo you have the PEAK PCAN-Basic drivers installed?")

    def disconnect(self):
        if self.is_connected:
            self.is_connected = False
            if self.notifier:
                self.notifier.stop()
            
            for row in self.tx_rows:
                row.stop_periodic()
                
            if self.bus:
                self.bus.shutdown()
            
            self.connect_btn.config(text="Connect")
            self.channel_cb.config(state=tk.NORMAL)
            self.bitrate_cb.config(state=tk.NORMAL)
            self.fd_check.config(state=tk.NORMAL)
            self.data_bitrate_cb.config(state=tk.NORMAL) # Re-enable data bitrate
            self.uds_buffer = None
            self.on_fd_toggle() # Refresh visibility

    def on_message_received(self, msg):
        # Determine if message is RX or TX
        # Some backends use msg.is_rx, some don't set it for TX if receive_own_messages is False
        direction = "RX"
        if hasattr(msg, 'is_rx') and not msg.is_rx:
            direction = "TX"
            
        # We need to dispatch UI updates to the main thread
        self.root.after(0, self._insert_msg_to_tree, msg, direction)

    def _insert_msg_to_tree(self, msg, direction="RX"):
        if msg.arbitration_id == 1:
            return
            
        ts = msg.timestamp if msg.timestamp and msg.timestamp > 0 else time.time()
        timestamp = datetime.fromtimestamp(ts).strftime('%H:%M:%S.%f')[:-3]
        msg_id_val = msg.arbitration_id
        msg_id_hex = f"{msg_id_val:X}"
        
        cache_key = f"{msg_id_hex}_{direction}"
        last_time = self.last_timestamps.get(cache_key)
        if last_time is not None:
            period_ms = (ts - last_time) * 1000
            period_str = f"{period_ms:.1f}"
        else:
            period_str = "-"
        self.last_timestamps[cache_key] = ts
        
        msg_type = "CAN FD" if msg.is_fd else "CAN 2.0"
        dlc = str(msg.dlc)
        data_hex_list = [f"{b:02X}" for b in msg.data]
        self.message_data[msg_id_hex] = msg.data
        
        # Show only first 8 bytes in main row
        main_data_hex = " ".join(data_hex_list[:8])
        if len(data_hex_list) > 8:
            main_data_hex += " ..."
        
        # Determine Message Name from DBC
        msg_name = msg_id_hex
        decoded_signals = {}
        if self.db:
            try:
                msg_def = self.db.get_message_by_frame_id(msg_id_val)
                msg_name = msg_def.name
                decoded_signals = msg_def.decode(msg.data)
                # Store latest values for monitor
                for sname, sval in decoded_signals.items():
                    self.signal_values[sname] = sval
            except (KeyError, ValueError):
                pass

        values = (timestamp, period_str, direction, msg_type, msg_id_hex, dlc, main_data_hex)

        if msg_id_hex in self.message_items:
            iid = self.message_items[msg_id_hex]
            self.tree.item(iid, text=msg_name, values=values)
        else:
            iid = self.tree.insert("", tk.END, text=msg_name, values=values)
            self.message_items[msg_id_hex] = iid
            
        # Handle data overflow as child rows
        if len(data_hex_list) > 8:
            for i in range(8, len(data_hex_list), 8):
                chunk_idx = i // 8
                chunk_data = " ".join(data_hex_list[i:i+8])
                ov_key = (msg_id_hex, chunk_idx)
                
                # Show byte range in Name column
                chunk_name = f"  ↳ Data[{i:02}:{min(i+7, len(data_hex_list)-1):02}]"
                ov_values = ("", "", "", "", "", "", chunk_data)
                
                if ov_key in self.data_overflow_items:
                    oiid = self.data_overflow_items[ov_key]
                    self.tree.item(oiid, text=chunk_name, values=ov_values)
                else:
                    oiid = self.tree.insert(iid, tk.END, text=chunk_name, values=ov_values)
                    self.data_overflow_items[ov_key] = oiid

        # Update children signals with throttling
        now = time.time()
        last_upd = self.last_ui_update.get(msg_id_hex, 0)
        
        if decoded_signals and (now - last_upd > 0.1): # 100ms throttle
            self.last_ui_update[msg_id_hex] = now
            for sname, sval in decoded_signals.items():
                sig_key = (msg_id_hex, sname)
                val_str = f"{sval}"
                
                # Try to get unit
                unit = ""
                try:
                    unit_val = self.db.get_message_by_frame_id(msg_id_val).get_signal_by_name(sname).unit
                    unit = f" {unit_val}" if unit_val else ""
                except: pass
                
                full_val_str = val_str + unit
                
                if sig_key in self.signal_items:
                    siid = self.signal_items[sig_key]
                    self.tree.item(siid, values=("", "", "", "", "", full_val_str))
                else:
                    siid = self.tree.insert(iid, tk.END, text=f"  ↳ {sname}", values=("", "", "", "", "", full_val_str))
                    self.signal_items[sig_key] = siid
            
            # Also update Global monitor tab entries
            for sname, sval in decoded_signals.items():
                if sname in self.monitor_sig_items:
                    miid = self.monitor_sig_items[sname]
                    curr_vals = self.sig_tree.item(miid)['values']
                    new_vals = list(curr_vals)
                    new_vals[1] = f"{sval}"
                    self.sig_tree.item(miid, values=new_vals)

    def on_message_double_click(self, event):
        item = self.tree.identify_row(event.y)
        if not item: return
        
        # If it's a child row, get parent
        parent = self.tree.parent(item)
        if parent: item = parent
        
        msg_name = self.tree.item(item, "text")
        vals = self.tree.item(item, "values")
        if not vals: return
        
        msg_type = vals[3]
        msg_id_hex = vals[4]
        
        # Create Popup
        pop = tk.Toplevel(self.root)
        pop.title(f"Details: {msg_name} (0x{msg_id_hex})")
        pop.geometry("550x450")
        
        main_f = ttk.Frame(pop, padding=10)
        main_f.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(main_f, text=f"Message: {msg_name}", font=("Arial", 12, "bold")).pack(anchor=tk.W)
        ttk.Label(main_f, text=f"Type: {msg_type} | ID: 0x{msg_id_hex} | DLC: {vals[5]} | Dir: {vals[2]}").pack(anchor=tk.W, pady=(0, 10))
        
        data_f = ttk.LabelFrame(main_f, text="Signals Decoding", padding=5)
        data_f.pack(fill=tk.BOTH, expand=True)
        
        stree = ttk.Treeview(data_f, columns=("Value", "Unit", "Range"), show="headings")
        stree["columns"] = ("Name", "Value", "Unit", "Range")
        stree.heading("Name", text="Signal Name")
        stree.heading("Value", text="Value")
        stree.heading("Unit", text="Unit")
        stree.heading("Range", text="Min/Max")
        stree.column("Name", width=160)
        stree.column("Value", width=90)
        stree.column("Unit", width=60)
        stree.column("Range", width=120)
        stree.configure(show="headings")
        stree.pack(fill=tk.BOTH, expand=True)
        
        if self.db:
            try:
                mid = int(msg_id_hex, 16)
                msg_def = self.db.get_message_by_frame_id(mid)
                raw_data = self.message_data.get(msg_id_hex)
                if raw_data is None:
                    raw_data_hex = vals[6].replace(" ", "").replace("...", "")
                    raw_data = bytes.fromhex(raw_data_hex)
                
                decoded = msg_def.decode(raw_data)
                
                for sig in msg_def.signals:
                    val = decoded.get(sig.name, "-")
                    rng = f"{sig.minimum} / {sig.maximum}" if sig.minimum is not None else "N/A"
                    stree.insert("", tk.END, values=(sig.name, val, sig.unit or "", rng))
            except Exception as e:
                ttk.Label(data_f, text=f"Decoding Error: {e}", foreground="red").pack()

    def clear_messages(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.message_items.clear()
        self.last_timestamps.clear()
        self.signal_items.clear()
        self.data_overflow_items.clear()
        self.last_ui_update.clear()

    def add_tx_row(self):
        row = TxRow(self.tx_scrollable_frame, self)
        self.tx_rows.append(row)
        if self.db:
            row.update_dbc_options()

    def on_closing(self):
        self.auto_save()
        self.disconnect()
        self.root.destroy()

class TxRow:
    def __init__(self, parent, app):
        self.app = app
        self.frame = ttk.Frame(parent)
        self.frame.pack(fill=tk.X, pady=2, anchor="w")
        
        main_line = ttk.Frame(self.frame)
        main_line.pack(fill=tk.X)

        ttk.Label(main_line, text="ID(Hex/DBC):").pack(side=tk.LEFT)
        self.id_entry = ttk.Combobox(main_line, width=20)
        self.id_entry.pack(side=tk.LEFT, padx=2)
        self.id_entry.bind("<<ComboboxSelected>>", lambda e: self.check_dbc())
        self.id_entry.bind("<FocusOut>", lambda e: self.check_dbc())
        self.id_entry.bind("<Return>", lambda e: self.check_dbc())
        
        ttk.Label(main_line, text="DLC:").pack(side=tk.LEFT)
        self.dlc_entry = ttk.Combobox(main_line, width=3, values=["0","1","2","3","4","5","6","7","8"])
        self.dlc_entry.set("8")
        self.dlc_entry.pack(side=tk.LEFT, padx=2)
        self.dlc_entry.bind("<<ComboboxSelected>>", self.on_dlc_change)
        self.dlc_entry.bind("<FocusOut>", self.on_dlc_change)
        self.dlc_entry.bind("<Return>", self.on_dlc_change)
        
        ttk.Label(main_line, text="Data(Hex):").pack(side=tk.LEFT)
        self.data_entry = ttk.Entry(main_line, width=50)
        self.data_entry.insert(0, "00 00 00 00 00 00 00 00")
        self.data_entry.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        self.data_entry.bind("<KeyRelease>", self.format_data_entry)
            
        self.fd_var = tk.BooleanVar(value=app.fd_var.get())
        self.fd_check = ttk.Checkbutton(main_line, text="FD", variable=self.fd_var, command=self.update_dlc_options)
        self.fd_check.pack(side=tk.LEFT, padx=2)
        self.update_dlc_options()
        
        self.ext_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(main_line, text="EXT", variable=self.ext_var).pack(side=tk.LEFT, padx=2)
        
        ttk.Button(main_line, text="Send", command=self.send_once).pack(side=tk.LEFT, padx=2)
        
        self.periodic_var = tk.BooleanVar(value=False)
        self.periodic_cb = ttk.Checkbutton(main_line, text="Periodic", variable=self.periodic_var, command=self.toggle_periodic)
        self.periodic_cb.pack(side=tk.LEFT, padx=2)
        
        ttk.Label(main_line, text="Period(ms):").pack(side=tk.LEFT)
        self.period_entry = ttk.Entry(main_line, width=5)
        self.period_entry.insert(0, "100")
        self.period_entry.pack(side=tk.LEFT, padx=2)
        
        ttk.Button(main_line, text="X", width=2, command=self.destroy).pack(side=tk.LEFT, padx=5)
        
        self.sig_frame = ttk.Frame(self.frame)
        self.sig_frame.pack(fill=tk.X, padx=(40, 0))
        self.sig_entries = {}
        
        self.periodic_task = None
        self.msg_def = None

    def update_dbc_options(self):
        if not self.app.db:
            self.id_entry['values'] = []
            return
            
        # Get list of all message names and IDs for the dropdown
        # Format: "MessageName (0xID)"
        options = []
        for msg in sorted(self.app.db.messages, key=lambda x: x.name):
            options.append(f"{msg.name} (0x{msg.frame_id:X})")
        
        self.id_entry['values'] = options

    def _get_msg_id_from_entry(self):
        val_str = self.id_entry.get().strip()
        if not val_str: return None
        
        # 1. Check if it matches a dropdown format "Name (0xID)"
        if "(" in val_str and val_str.endswith(")"):
            try:
                # Extract hex ID from parentheses
                hex_part = val_str.split("(0x")[-1].replace(")", "")
                return int(hex_part, 16)
            except: pass
        
        # 2. Check if it's a direct Name in DBC
        if self.app.db:
            try:
                return self.app.db.get_message_by_name(val_str).frame_id
            except: pass
            
        # 3. Check if it's a direct Hex string
        try:
            # Remove 0x prefix if exists
            clean_hex = val_str.replace("0x", "").replace("0X", "")
            return int(clean_hex, 16)
        except: pass
        
        return None

    def check_dbc(self):
        msg_id_val = self._get_msg_id_from_entry()

        if msg_id_val is not None and self.app.db:
            try:
                self.msg_def = self.app.db.get_message_by_frame_id(msg_id_val)
                self._build_signals_ui()
                return
            except: pass
            
        self.msg_def = None
        self._clear_signals_ui()

    def _build_signals_ui(self):
        self._clear_signals_ui()
        if not self.msg_def: return
        
        # Update DLC automatically
        self.dlc_entry.set(str(self.msg_def.length))
        
        # Create small entry for each signal
        for i, sig in enumerate(self.msg_def.signals):
            row_idx = i // 4
            col_idx = (i % 4) * 2
            
            lbl = ttk.Label(self.sig_frame, text=f"{sig.name}:", font=("Arial", 8))
            lbl.grid(row=row_idx, column=col_idx, sticky=tk.W, padx=(5, 2))
            
            ent = ttk.Entry(self.sig_frame, width=8)
            ent.insert(0, str(sig.initial or 0))
            ent.grid(row=row_idx, column=col_idx+1, sticky=tk.W, padx=2)
            self.sig_entries[sig.name] = ent

    def _clear_signals_ui(self):
        for widget in self.sig_frame.winfo_children():
            widget.destroy()
        self.sig_entries.clear()

    def update_dlc_options(self, *args):
        if self.fd_var.get():
            self.dlc_entry['values'] = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "12", "16", "20", "24", "32", "48", "64"]
        else:
            self.dlc_entry['values'] = ["0", "1", "2", "3", "4", "5", "6", "7", "8"]

    def on_dlc_change(self, *args):
        try:
            dlc = int(self.dlc_entry.get().strip())
        except ValueError:
            return
            
        content = self.data_entry.get().replace(" ", "")
        try:
            current_bytes = bytes.fromhex(content)
        except ValueError:
            current_bytes = b""
            
        if len(current_bytes) < dlc:
            # Pad with zeros
            new_bytes = current_bytes + b'\x00' * (dlc - len(current_bytes))
        else:
            # Truncate
            new_bytes = current_bytes[:dlc]
            
        fmt = " ".join(f"{b:02X}" for b in new_bytes)
        self.data_entry.delete(0, tk.END)
        self.data_entry.insert(0, fmt)

    def format_data_entry(self, event):
        if event.keysym in ("BackSpace", "Delete", "Left", "Right", "Shift_L", "Shift_R"): return
        content = self.data_entry.get()
        cursor_pos = self.data_entry.index(tk.INSERT)
        chars_before = len(content[:cursor_pos].replace(" ", ""))
        val = "".join(c for c in content.replace(" ", "").upper() if c in "0123456789ABCDEF")
        fmt = " ".join(val[i:i+2] for i in range(0, len(val), 2))
        if content != fmt:
            self.data_entry.delete(0, tk.END)
            self.data_entry.insert(0, fmt)
            new_pos = 0
            seen = 0
            for i, char in enumerate(fmt):
                if seen == chars_before:
                    new_pos = i
                    break
                if char != " ": seen += 1
                new_pos = i + 1
            self.data_entry.icursor(new_pos)

    def _create_message(self):
        dlc_str = self.dlc_entry.get().strip()
        is_fd = self.fd_var.get()
        is_ext = self.ext_var.get()
        
        try:
            msg_id = self._get_msg_id_from_entry()
            if msg_id is None:
                raise ValueError("Invalid ID")
                
            dlc = int(dlc_str) if dlc_str else 8
            
            if self.msg_def and self.sig_entries:
                # Encode from signals
                sig_data = {}
                for sname, sentry in self.sig_entries.items():
                    try:
                        sig_data[sname] = float(sentry.get())
                    except:
                        sig_data[sname] = 0
                
                data_bytes = self.msg_def.encode(sig_data)
            else:
                # Use raw hex entry
                val_str = self.data_entry.get().replace(" ", "")
                data_bytes = bytes.fromhex(val_str) if val_str else b""
            
            if len(data_bytes) > dlc:
                data_bytes = data_bytes[:dlc]
            elif len(data_bytes) < dlc:
                data_bytes += b'\x00' * (dlc - len(data_bytes))
                
            # Update Hex entry for visual feedback
            fmt_hex = " ".join(f"{b:02X}" for b in data_bytes)
            self.data_entry.delete(0, tk.END)
            self.data_entry.insert(0, fmt_hex)
                
            msg = can.Message(
                arbitration_id=msg_id,
                data=data_bytes,
                is_extended_id=is_ext,
                is_fd=is_fd
            )
            return msg
        except ValueError:
            messagebox.showerror("Format Error", "Please ensure ID, DLC and Data are valid numeric/Hex values.")
            return None

    def send_once(self):
        if not self.app.is_connected:
            messagebox.showwarning("Warning", "Not connected to any CAN interface.")
            return
            
        msg = self._create_message()
        if msg:
            try:
                msg.timestamp = time.time()
                msg.is_rx = False
                self.app.bus.send(msg)
            except can.CanError as e:
                messagebox.showerror("Transmit Error", f"Failed to send CAN message:\n{e}")

    def toggle_periodic(self):
        if self.periodic_var.get():
            if not self.app.is_connected:
                messagebox.showwarning("Warning", "Not connected to any CAN interface.")
                self.periodic_var.set(False)
                return
            
            msg = self._create_message()
            if not msg:
                self.periodic_var.set(False)
                return
                
            try:
                period_ms = float(self.period_entry.get().strip())
                period_s = period_ms / 1000.0
                if period_s <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Format Error", "Please enter a valid positive period in ms.")
                self.periodic_var.set(False)
                return
                
            self.id_entry.config(state=tk.DISABLED)
            self.dlc_entry.config(state=tk.DISABLED)
            self.data_entry.config(state=tk.DISABLED)
            for e in self.sig_entries.values():
                e.config(state=tk.DISABLED)
            self.period_entry.config(state=tk.DISABLED)
            
            self.periodic_task = self.app.bus.send_periodic(msg, period_s)
        else:
            self.stop_periodic()
            
    def stop_periodic(self):
        if self.periodic_task:
            self.periodic_task.stop()
            self.periodic_task = None
            
        self.periodic_var.set(False)
        self.id_entry.config(state=tk.NORMAL)
        self.dlc_entry.config(state=tk.NORMAL)
        self.data_entry.config(state=tk.NORMAL)
        for e in self.sig_entries.values():
            e.config(state=tk.NORMAL)
        self.period_entry.config(state=tk.NORMAL)

    def destroy(self):
        self.stop_periodic()
        if self in self.app.tx_rows:
            self.app.tx_rows.remove(self)
        self.frame.destroy()

    def get_state(self):
        # Extract signal values
        sigs = {name: entry.get() for name, entry in self.sig_entries.items()}
        # Extract data bytes
        data = self.data_entry.get()
        
        return {
            "id": self.id_entry.get(),
            "dlc": self.dlc_entry.get(),
            "data": data,
            "fd": self.fd_var.get(),
            "ext": self.ext_var.get(),
            "periodic": self.periodic_var.get(),
            "period": self.period_entry.get(),
            "signals": sigs
        }

    def set_state(self, state):
        self.id_entry.set(state.get("id", ""))
        self.check_dbc() # Populate signals if possible
        
        self.dlc_entry.delete(0, tk.END)
        self.dlc_entry.insert(0, state.get("dlc", "8"))
        
        # Restore hex data
        data = state.get("data", "")
        self.data_entry.delete(0, tk.END)
        self.data_entry.insert(0, data)
        
        self.fd_var.set(state.get("fd", False))
        self.ext_var.set(state.get("ext", False))
        self.period_entry.delete(0, tk.END)
        self.period_entry.insert(0, state.get("period", "100"))
        
        # Restore signals if they exist
        saved_sigs = state.get("signals", {})
        for name, val in saved_sigs.items():
            if name in self.sig_entries:
                self.sig_entries[name].delete(0, tk.END)
                self.sig_entries[name].insert(0, val)
        
        # Handle periodic starting if it was active
        # (Though usually we don't start it automatically for safety)
        # self.periodic_var.set(state.get("periodic", False))
        # if self.periodic_var.get(): self.toggle_periodic()

if __name__ == "__main__":
    root = tk.Tk()
    app = CanInterfaceApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
