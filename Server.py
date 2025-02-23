from flask import Flask, render_template
import argparse
import sys
import time
import psutil

# Define the version
VERSION = "1.1.0"

app = Flask(__name__)


@app.route('/')
def index():
    return render_template('ipad.html')


def get_interface_ip(interface_name):
    """
    Retrieve the IP address of a specific network interface.
    """
    try:
        net_if_addrs = psutil.net_if_addrs()
        if interface_name in net_if_addrs:
            for addr in net_if_addrs[interface_name]:
                if addr.family.name == 'AF_INET':  # IPv4 Address
                    return addr.address
        return None
    except Exception as e:
        print(f"Error getting IP for {interface_name}: {e}")
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="A script with version support.")

    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"%(prog)s version {VERSION}"
    )

    args = parser.parse_args()

    desired_ip = "192.168.0.104"
    interface_name = "eth1"

    print(
        f"Waiting for IP address {desired_ip} on interface {interface_name}...")

    # Keep checking until the IP matches the desired one
    while True:
        current_ip = get_interface_ip(interface_name)
        if current_ip == desired_ip:
            print(
                f"IP address {desired_ip} detected on {interface_name}. Starting server...")
            app.run(host='0.0.0.0', port=5000)
            break
        else:
            print(
                f"Current IP on {interface_name} ({current_ip}) does not match {desired_ip}. Retrying in 5 seconds...")
            time.sleep(5)
