#!/usr/bin/env -S pipx run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pyserial>=3.5",
# ]
# ///
# python serial_logger.py -p /dev/ttyUSB0 -b 115200
# Performs boot time benchmark with timestamps
import serial
import time
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("-p", "--port", required=True, help="Serial port, for example/dev/ttyUSB0")
parser.add_argument("-b", "--baud", type=int, default=115200, help="Speed (baudrate)")
args = parser.parse_args()

ser = serial.Serial(args.port, args.baud, timeout=1)

t0 = None
try:
    while True:
        line = ser.readline()
        if not line:
            continue
        now = time.monotonic()
        if t0 is None:
            t0 = now
        delta = now - t0
        print(f"[+{delta:8.3f}s] {line.decode(errors='replace').rstrip()}")
except KeyboardInterrupt:
    ser.close()
