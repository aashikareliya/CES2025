import asyncio
from bleak import BleakClient
from flask import Flask, jsonify
from flask_cors import CORS
import threading
import signal

# UUID for BLE notifications
NOTIFICATION_UUID = "85fc567e-31d9-4185-87c6-339924d1c5be"

# Initialize Flask application
app = Flask(__name__)
CORS(app)

# Global variable to store parsed data
parsed_data = []
client = None  # Global variable for the BLE client

def parse_data(data):
    """Parses the incoming Bluetooth packet and stores it in a global list."""
    global parsed_data
    try:
        # Parse ECG data as a 32-bit integer (first four bytes, assuming signed integer format)
        ecg = int.from_bytes(data[0:4], byteorder='little', signed=True)

        # Parse PPG (Red) data as a 24-bit integer (next three bytes)
        ppg_red = int.from_bytes(data[4:6], byteorder='little', signed=False)

        # Parse PPG IR data as a 24-bit integer (next three bytes after PPG Red)
        ppg_ir = int.from_bytes(data[6:9], byteorder='little', signed=False)

        # Parse Heart Rate (HR) and its confidence (HR_CON)
        HR_val = data[9] if len(data) > 9 else "N/A"
        HR_CON_val = data[10] if len(data) > 10 else "N/A"

        # Parse Respiratory Rate (RR) and its confidence (RR_CON)
        RR_val = (data[11] | (data[12] << 8)) if len(data) > 12 else "N/A"
        RR_CON_val = data[13] if len(data) > 13 else "N/A"

        # Parse SpO2 value and its confidence
        SpO2_val = data[14] if len(data) > 14 else "N/A"
        SpO2_CON_val = data[15] if len(data) > 15 else "N/A"
        SpO2_Reliable = data[16] if len(data) > 16 else "N/A"

        # Parse Skin Contact Detection (SCD) value
        SCD_val = data[17] if len(data) > 17 else "N/A"
        SCD_status = "On Skin" if SCD_val == 3 else "Off Skin"

        # Parse temperature as a string from bytes 18 to 22
        temperature_d = "".join([chr(data[i]) for i in range(18, 23)]) if len(data) > 22 else "N/A"
        try:
            temperature = float(temperature_d) if temperature_d != "N/A" else "N/A"
        except ValueError:
            temperature = "N/A"

        # Conditionally display data based on SCD_status and heart rate value
        if SCD_status == "On Skin":
            ecg_display = f"{ecg} uV"
            ppg_ir_display = f"{ppg_ir} AU"
            heart_rate_display = f"{HR_val} bpm" if HR_val != "N/A" and HR_val >= 25 else "--"
        else:
            ecg_display = "--"
            ppg_ir_display = "--"
            heart_rate_display = "--"

        # Store parsed data in a dictionary with units
        entry = {
            "ecg": ecg_display,
            "ppg_red": f"{ppg_red} AU",
            "ppg_ir": ppg_ir_display,
            "heart_rate": heart_rate_display,
            "HR_CON_val": HR_CON_val,
            "RR_val": RR_val,
            "RR_CON_val": RR_CON_val,
            "SpO2_val": f"{SpO2_val} %" if SpO2_val != "N/A" else "N/A",
            "SpO2_CON_val": SpO2_CON_val,
            "SpO2_Reliable": SpO2_Reliable,
            "SCD_status": SCD_status,
            "temperature": f"{temperature} °C" if temperature != "N/A" else "N/A"
        }

        # Append the entry to the parsed data list
        parsed_data.append(entry)
        print("Parsed Data:", entry)

    except Exception as e:
        print(f"Error parsing data: {e}")

def notification_handler(sender, data):
    """Handles incoming notifications from the BLE device."""
    parse_data(data)

async def connect_to_device(mac_address):
    """Attempts to connect to the BLE device and handle notifications indefinitely."""
    global client  # Access the global client variable
    while True:
        try:
            print(f"Attempting to connect to {mac_address}...")
            client = BleakClient(mac_address)
            async with client:
                print(f"Connected to {mac_address}!")
                await client.start_notify(NOTIFICATION_UUID, notification_handler)

                while client.is_connected:
                    await asyncio.sleep(1)  # Keep the connection alive

                print("Device disconnected, attempting to reconnect...")
        except Exception as e:
            print(f"Connection error: {e}. Retrying in 5 seconds...")
            await asyncio.sleep(5)  # Wait and retry connection if there is an error

@app.route('/data', methods=['GET'])
def get_data():
    """API endpoint to get the parsed data."""
    return jsonify(parsed_data)

def start_server():
    """Start the Flask web server on port 5050."""
    app.run(host='0.0.0.0', port=5000)

def cleanup():
    """Ensure the BLE client disconnects on exit."""
    global client
    if client and client.is_connected:
        asyncio.run(client.disconnect())
        print("Disconnected BLE client on exit.")

# Set up signal handling for clean exit
signal.signal(signal.SIGINT, lambda s, f: cleanup())
signal.signal(signal.SIGTERM, lambda s, f: cleanup())

if __name__ == "__main__":
    mac_address = "00:18:80:04:52:85"  # Replace with your BLE device MAC address
    loop = asyncio.get_event_loop()
    
    # Start the Flask server in a separate thread
    threading.Thread(target=start_server).start()
    
    # Start the BLE connection process
    loop.run_until_complete(connect_to_device(mac_address))
