import tkinter as tk
from tkinter import ttk, messagebox
import can
import can.interfaces.pcan
import threading
import time
from datetime import datetime
import isotp
from udsoncan.connections import PythonIsoTpConnection

class CanInterfaceApp:
    def __init__(self, root):
        self.root = root
        self.root.title("FFichCAN")
        self.root.geometry("1000x850")
        self.root.minsize(900, 650)

        self.bus = None
        self.notifier = None
        self.is_connected = False
        self.message_items = {}
        self.last_timestamps = {}
        self.tx_rows = []

        self.create_widgets()

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

        ttk.Label(control_frame, text="Bitrate:").pack(side=tk.LEFT, padx=(0, 5))
        self.bitrate_cb = ttk.Combobox(control_frame, values=["1000000", "500000", "250000", "125000"], width=10)
        self.bitrate_cb.set("500000")
        self.bitrate_cb.pack(side=tk.LEFT, padx=(0, 10))
        
        self.fd_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(control_frame, text="FD Mode", variable=self.fd_var).pack(side=tk.LEFT, padx=(0, 10))

        self.connect_btn = ttk.Button(control_frame, text="Connect", command=self.toggle_connection)
        self.connect_btn.pack(side=tk.LEFT)
        
        self.clear_btn = ttk.Button(control_frame, text="Clear Msg", command=self.clear_messages)
        self.clear_btn.pack(side=tk.LEFT, padx=(5, 0))

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.tab_bus = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_bus, text="Bus Control")
        
        self.tab_uds = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_uds, text="Diagnostics (UDS)")

        # Main Table for incoming data
        data_frame = ttk.LabelFrame(self.tab_bus, text="Incoming Messages")
        data_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.tree = ttk.Treeview(data_frame, columns=("Time", "Period", "RX/TX", "ID", "DLC", "Data"), show="headings")
        self.tree.heading("Time", text="Time")
        self.tree.heading("Period", text="Period (ms)")
        self.tree.heading("RX/TX", text="RX/TX")
        self.tree.heading("ID", text="ID (Hex)")
        self.tree.heading("DLC", text="DLC")
        self.tree.heading("Data", text="Data (Hex)")
        
        self.tree.column("Time", width=120, anchor=tk.W)
        self.tree.column("Period", width=80, anchor=tk.CENTER)
        self.tree.column("RX/TX", width=60, anchor=tk.CENTER)
        self.tree.column("ID", width=100, anchor=tk.W)
        self.tree.column("DLC", width=50, anchor=tk.CENTER)
        self.tree.column("Data", width=250, anchor=tk.W)
        
        scrollbar = ttk.Scrollbar(data_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

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
        
        ttk.Button(req_frame, text="Send Request", command=self.send_uds_request).pack(side=tk.LEFT, padx=10)
        
        log_frame = ttk.LabelFrame(parent, text="UDS Log")
        log_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.uds_log = tk.Text(log_frame, height=15)
        self.uds_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        scroll = ttk.Scrollbar(log_frame, command=self.uds_log.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.uds_log.config(yscrollcommand=scroll.set)

    def log_uds(self, msg):
        self.uds_log.insert(tk.END, f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}\n")
        self.uds_log.see(tk.END)

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
        self.root.after(0, self.log_uds, f"Init ISO-TP... TX: {txid:X}, RX: {rxid:X}")
        
        try:
            addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=txid, rxid=rxid)
            stack = isotp.CanStack(self.bus, address=addr)
            conn = PythonIsoTpConnection(stack)
            conn.open()
            
            raw_payload = bytes([sid]) + payload
            self.root.after(0, self.log_uds, f"Sending: {raw_payload.hex().upper()}")
            
            conn.send(raw_payload)
            response_data = conn.wait_frame(timeout=2.0)
            
            if response_data is not None:
                resp_hex = response_data.hex().upper()
                self.root.after(0, self.log_uds, f"Response received: {resp_hex}")
            else:
                self.root.after(0, self.log_uds, "Timeout waiting for response.")
                
            conn.close()
        except Exception as e:
            self.root.after(0, self.log_uds, f"Error: {e}")

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

        try:
            bitrate = int(bitrate_str)
            # Initialize python-can bus for Peak adapter
            if is_fd:
                self.bus = can.interface.Bus(
                    interface='pcan', 
                    channel=channel, 
                    bitrate=bitrate, 
                    data_bitrate=2000000, 
                    fd=True, 
                    receive_own_messages=True
                )
            else:
                self.bus = can.interface.Bus(interface='pcan', channel=channel, bitrate=bitrate, receive_own_messages=True)
            
            self.notifier = can.Notifier(self.bus, [self.on_message_received])
            
            self.is_connected = True
            self.connect_btn.config(text="Disconnect")
            self.channel_cb.config(state=tk.DISABLED)
            self.bitrate_cb.config(state=tk.DISABLED)
            self.fd_var.set(is_fd)
            
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

    def on_message_received(self, msg):
        # Determine if message is RX or TX
        # Some backends use msg.is_rx, some don't set it for TX if receive_own_messages is False
        direction = "RX"
        if hasattr(msg, 'is_rx') and not msg.is_rx:
            direction = "TX"
            
        # We need to dispatch UI updates to the main thread
        self.root.after(0, self._insert_msg_to_tree, msg, direction)

    def _insert_msg_to_tree(self, msg, direction="RX"):
        # Handle timestamp potentially being 0.0 for raw injected TX messages
        ts = msg.timestamp if msg.timestamp and msg.timestamp > 0 else time.time()
        timestamp = datetime.fromtimestamp(ts).strftime('%H:%M:%S.%f')[:-3]
        msg_id = f"{msg.arbitration_id:X}"
        
        # Track periods separately for RX and TX to avoid mixing timestamps
        cache_key = f"{msg_id}_{direction}"
        last_time = self.last_timestamps.get(cache_key)
        if last_time is not None:
            period_ms = (ts - last_time) * 1000
            period_str = f"{period_ms:.1f}"
        else:
            period_str = "-"
        self.last_timestamps[cache_key] = ts
        
        dlc = str(msg.dlc)
        data = "   ".join(f"{b:02X}" for b in msg.data)
        
        values = (timestamp, period_str, direction, msg_id, dlc, data)

        if msg_id in self.message_items:
            # Update existing row for this CAN ID
            self.tree.item(self.message_items[msg_id], values=values)
        else:
            # Create a new row
            item_id = self.tree.insert("", tk.END, values=values)
            self.message_items[msg_id] = item_id

    def clear_messages(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.message_items.clear()
        self.last_timestamps.clear()

    def add_tx_row(self):
        row = TxRow(self.tx_scrollable_frame, self)
        self.tx_rows.append(row)

    def on_closing(self):
        self.disconnect()
        self.root.destroy()

class TxRow:
    def __init__(self, parent, app):
        self.app = app
        self.frame = ttk.Frame(parent)
        self.frame.pack(fill=tk.X, pady=2, anchor="w")
        
        ttk.Label(self.frame, text="ID(Hex):").pack(side=tk.LEFT)
        self.id_entry = ttk.Entry(self.frame, width=8)
        self.id_entry.pack(side=tk.LEFT, padx=2)
        
        ttk.Label(self.frame, text="DLC:").pack(side=tk.LEFT)
        self.dlc_entry = ttk.Entry(self.frame, width=3)
        self.dlc_entry.insert(0, "8")
        self.dlc_entry.pack(side=tk.LEFT, padx=2)
        
        self.data_entries = []
        for i in range(8):
            e = ttk.Entry(self.frame, width=3)
            e.insert(0, "00")
            e.pack(side=tk.LEFT, padx=1)
            self.data_entries.append(e)
            
        self.fd_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.frame, text="FD", variable=self.fd_var).pack(side=tk.LEFT, padx=2)
        
        self.ext_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.frame, text="EXT", variable=self.ext_var).pack(side=tk.LEFT, padx=2)
        
        ttk.Button(self.frame, text="Send", command=self.send_once).pack(side=tk.LEFT, padx=2)
        
        self.periodic_var = tk.BooleanVar(value=False)
        self.periodic_cb = ttk.Checkbutton(self.frame, text="Periodic", variable=self.periodic_var, command=self.toggle_periodic)
        self.periodic_cb.pack(side=tk.LEFT, padx=2)
        
        ttk.Label(self.frame, text="Period(ms):").pack(side=tk.LEFT)
        self.period_entry = ttk.Entry(self.frame, width=5)
        self.period_entry.insert(0, "100")
        self.period_entry.pack(side=tk.LEFT, padx=2)
        
        ttk.Button(self.frame, text="X", width=2, command=self.destroy).pack(side=tk.LEFT, padx=5)
        
        self.periodic_task = None
        
    def _create_message(self):
        id_str = self.id_entry.get().strip()
        dlc_str = self.dlc_entry.get().strip()
        is_fd = self.fd_var.get()
        is_ext = self.ext_var.get()
        
        try:
            msg_id = int(id_str, 16) if id_str else 0
            dlc = int(dlc_str) if dlc_str else 8
            
            data_bytes = []
            for e in self.data_entries:
                val = e.get().strip()
                if val:
                    data_bytes.append(int(val, 16))
            
            if len(data_bytes) > dlc:
                data_bytes = data_bytes[:dlc]
                
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
            for e in self.data_entries:
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
        for e in self.data_entries:
            e.config(state=tk.NORMAL)
        self.period_entry.config(state=tk.NORMAL)

    def destroy(self):
        self.stop_periodic()
        if self in self.app.tx_rows:
            self.app.tx_rows.remove(self)
        self.frame.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = CanInterfaceApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
