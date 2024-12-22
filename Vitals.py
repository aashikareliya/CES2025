"""
Vitals Data Flow:
 
When UI Starts, By Default CSV data is streaming.
When Click on the HR, then if device is connected and live data in queue then Live data will Stream else CSV data.
When Click on the Spo2 at any point, then CSV data
When Click on the Temp at any point, it stop the vitals values and graph.
After Clicking on the Temp, for resume the process Either Click on the SPO2 or HR.

"""


import asyncio
import csv
from bleak import BleakClient
from flask import Flask, jsonify
from flask_cors import CORS
import threading
import signal
from queue import Queue
import logging
from datetime import datetime
from time import time

from flask import request

# BLE Configuration
MAC_ADDRESS = "00:18:80:04:52:85"
NOTIFICATION_UUID = "85fc567e-31d9-4185-87c6-339924d1c5be"

# Flask Application Initialization
app = Flask(__name__)
CORS(app)

# Logger Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Shared State
parsed_data_queue = Queue(maxsize=5000)  # Limit queue size to avoid overflow
csv_data_generator = None  # CSV data generator

client = None  # BLE Client instance
ble_connected = False  # BLE connection status flag
fetch_live_data = False  # Flag to control live data fetching

from threading import Lock

fetch_live_data_lock = Lock()

# CSV Setup
CSV_FILE = "Vitals.csv"
last_live_data_time = 0  # Timestamp of the last live data
LIVE_DATA_TIMEOUT = 5  # Seconds before falling back to CSV

def read_csv_data():
    """Generator to yield data from the CSV file."""
    while True:
        try:
            with open(CSV_FILE, mode="r") as csv_file:
                reader = csv.DictReader(csv_file)
                rows = list(reader)
                if not rows:
                    logger.warning(f"{CSV_FILE} is empty. Notifying UI.")
                    yield {"error": "CSV file is empty."}
                else:
                    for row in rows:
                        yield row
            logger.info(f"Rewinding {CSV_FILE} to the start after reading.")
        except FileNotFoundError:
            logger.error(f"CSV file {CSV_FILE} not found. Notifying UI.")
            yield {"error": "CSV file not found."}

def cleanup():
    """Cleanup BLE client and queue on exit."""
    global client
    logger.info("Initiating cleanup...")
    if client and client.is_connected:
        asyncio.run(client.disconnect())
        logger.info("Disconnected BLE client.")
    logger.info(f"Processing remaining {parsed_data_queue.qsize()} items in queue.")
    while not parsed_data_queue.empty():
        item = parsed_data_queue.get()
        logger.debug(f"Processed item during cleanup: {item}")
    logger.info("Cleanup complete. Exiting application.")


# Persistent cache for the latest valid data
latest_data_cache = {
    "ecg": 0,
    "ppg_red": 0,
    "ppg_ir": 0,
    "heart_rate": 0,
    "SpO2_val": 0,
    "SCD_status": "Off Skin",
    "temperature": 37.0,
}

def parse_data(data):
    """Parses incoming BLE notification data."""
    global latest_data_cache
    try:
        # Extract data fields with default values if parsing fails
        hardcoded_temperature = 37.0
        ecg = int.from_bytes(data[0:3], byteorder="little", signed=True)
        if (ecg & 0x020000) == 0x20000:
            ecg = ~ecg & 0x01FFFF
            ecg *= -1
        else:
            ecg &= 0x03FFFF

        ppg_red = int.from_bytes(data[3:6], byteorder="little", signed=False) & 0x000FFFFF
        ppg_ir = int.from_bytes(data[6:9], byteorder="little", signed=False) & 0x000FFFFF
        heart_rate = data[9] if len(data) > 9 else latest_data_cache["heart_rate"]
        SpO2_val = data[14] if len(data) > 14 else latest_data_cache["SpO2_val"]
        SCD_val = data[17] if len(data) > 17 else None
        SCD_status = "On Skin" if SCD_val == 3 else latest_data_cache["SCD_status"]

        # Update the cache with parsed data
        latest_data_cache = {
            "ecg": ecg,
            "ppg_red": ppg_red,
            "ppg_ir": ppg_ir,
            "heart_rate": heart_rate,
            "SpO2_val": SpO2_val,
            "SCD_status": SCD_status,
            "temperature": hardcoded_temperature,
        }

        # Add parsed data to the queue
        if not parsed_data_queue.full():
            parsed_data_queue.put(latest_data_cache)
        else:
            parsed_data_queue.get()  # Remove the oldest data
            parsed_data_queue.put(latest_data_cache)
        last_live_data_time = time()
        logger.info(f"Parsed Data: {latest_data_cache}")
    except Exception as e:
        logger.error(f"Error parsing data: {e}")
        # Fallback to cache in case of error
        logger.info("Falling back to cached data.")
        parsed_data_queue.put(latest_data_cache)

async def connect_to_device():
    """Continuously attempts to connect to BLE device and handles notifications."""
    global client, ble_connected
    while True:
        try:
            logger.info(f"Attempting to connect to {MAC_ADDRESS}...")
            client = BleakClient(MAC_ADDRESS)
            async with client:
                logger.info(f"Connected to {MAC_ADDRESS}!")
                ble_connected = True
                await client.start_notify(NOTIFICATION_UUID, notification_handler)

                # Stay connected as long as possible
                while client.is_connected:
                    await asyncio.sleep(1)

        except Exception as e:
            ble_connected = False
            logger.error(f"Connection error: {e}. Retrying in 5 seconds...")
            await asyncio.sleep(5)


def notification_handler(sender, data):
    """Handles BLE notifications."""
    global fetch_live_data

    # Parse the incoming data
    parse_data(data)  # Always parse and store data in the queue

    if fetch_live_data:
        logger.debug("Live data fetching is active.")
    else:
        logger.debug("Live data is being collected but not sent.")

@app.route("/data", methods=["GET"])
def get_data():
    """API endpoint to fetch data for UI."""
    global csv_data_generator, fetch_live_data

    # Handle live data when enabled
    if fetch_live_data:
        if not parsed_data_queue.empty():
            try:
                # Send live data from the queue
                live_data = parsed_data_queue.get()
                logger.info("Sending live data to UI.")
                return jsonify(live_data)
            except Exception as e:
                logger.error(f"Error fetching live data: {e}")
                # Continue to CSV only if live data fails entirely
        else:
            logger.info("Live data queue is empty; falling back to CSV.")
            set_fetch_live_data(False)  # Disable live fetching if queue is empty

    # Fallback to CSV data
    if not csv_data_generator:
        csv_data_generator = read_csv_data()

    try:
        csv_data = next(csv_data_generator, None)
        if csv_data:
            logger.info("Sending CSV data to UI.")
            return jsonify(csv_data)
    except Exception as e:
        logger.error(f"Error fetching CSV data: {e}")

    # Fallback to cached data if no CSV data is available
    logger.info("Sending cached data to UI.")
    return jsonify(latest_data_cache)

def set_fetch_live_data(value):
    global fetch_live_data
    with fetch_live_data_lock:
        if fetch_live_data != value:
            logger.info(f"Changing fetch_live_data to {value}")
            fetch_live_data = value

@app.route("/livedata", methods=["POST"])
def live_data():
    """API endpoint to enable live data fetching for UI."""
    global fetch_live_data

    # Enable live data fetching on HR click
    set_fetch_live_data(True)
    logger.info("Live data fetching enabled via POST request.")
    return jsonify({"status": "live_data_enabled"})


send_data = True  # Global flag to control all data transmission


@app.route("/temp", methods=["POST"])
def temp_clear():
    global send_data, parsed_data_queue, fetch_live_data
    send_data = False
    set_fetch_live_data(False)  # Explicitly disable live fetching

    # Clear the live data queue
    with parsed_data_queue.mutex:
        parsed_data_queue.queue.clear()

    logger.info("Data sending stopped and queue cleared via Temp click.")
    return jsonify({"status": "cleared", "message": "All values and graphs cleared."})




@app.route("/spodata", methods=["POST"])
def spodata():
    global send_data, fetch_live_data
    send_data = True
    fetch_live_data = False
    logger.info("CSV data sending re-enabled via SpO2 click.")
    return jsonify({"status": "csv_data_enabled"})



@app.route("/status", methods=["GET"])
def get_status():
    """API endpoint to fetch connection status."""
    status = {"connected": ble_connected}
    return jsonify(status)


def start_server():
    """Start the Flask web server."""
    logger.info("Starting Flask server...")
    app.run(host="0.0.0.0", port=8000)


def cleanup():
    """Cleanup BLE client on exit."""
    global client
    if client and client.is_connected:
        asyncio.run(client.disconnect())
        logger.info("Disconnected BLE client on exit.")


def start_ble_connection():
    """Start BLE connection logic."""
    asyncio.run(connect_to_device())


if __name__ == "__main__":
    # Signal handling for clean exit
    signal.signal(signal.SIGINT, lambda s, f: cleanup())
    signal.signal(signal.SIGTERM, lambda s, f: cleanup())

    # Start Flask server in a thread
    threading.Thread(target=start_server, daemon=True).start()

    # Start BLE connection in the main thread
    try:
        start_ble_connection()
    except KeyboardInterrupt:
        logger.info("Program interrupted by user. Exiting...")