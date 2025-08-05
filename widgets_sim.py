import struct
import socket
import random
import math
import threading
from time import sleep

TM_SEND_ADDRESS = '127.0.0.1'
TM_SEND_PORT = 10015

# CCSDS-like header helper reused from ENV2.py

def header(seq_count: int, apid: int) -> bytes:
    if seq_count >= 16382:
        seq_count = 0
    return apid.to_bytes(2, 'big') + (49152 + seq_count).to_bytes(2, 'big') + b'\x01\x45'


def floats_to_be(*values: float) -> bytes:
    return b''.join(struct.pack('>f', v) for v in values)


# --------------------------- 2 Hz sensors ---------------------------

def simulate_2hz(tm_socket: socket.socket):
    seq = 0
    while True:
        # Generate values
        # T_liq from CHT8305C (>28°C amber, >30°C red)
        t_liq = random.gauss(26.0, 2.0)
        # p_box from BME280 (<0.80 bar or >1.05 bar red)
        p_box = random.gauss(0.95, 0.05)
        # RH_box from BME280 (no alarm)
        rh_box = max(min(random.gauss(50.0, 10.0), 100.0), 0.0)
        # T_box from BME280 (>35°C red)
        t_box = random.gauss(30.0, 3.0)
        # H2 from MQ-8 (>4000 ppm amber; >10000 ppm red)
        h2 = max(random.gauss(3000.0, 2000.0), 0.0)

        # Pack floats into a single telemetry packet (APID 200)
        data = floats_to_be(t_liq, p_box, rh_box, t_box, h2)
        pkt = header(seq, apid=200) + data
        tm_socket.sendto(pkt, (TM_SEND_ADDRESS, TM_SEND_PORT))
        seq += 1
        sleep(0.5)  # 2 Hz


# --------------------------- 10 Hz sensors ---------------------------

def simulate_10hz(tm_socket: socket.socket):
    seq = 0
    while True:
        # a_x/a_y/a_z from LSM6DSOX (magnitude > ±1.5 g red)
        ax = random.gauss(0.0, 0.02)
        ay = random.gauss(0.0, 0.02)
        az = random.gauss(1.0, 0.02)  # approx 1g on Z

        # Derive normalised gravity vector (gx, gy, gz)
        mag = math.sqrt(ax * ax + ay * ay + az * az)
        if mag == 0:
            gx = gy = gz = 0.0
        else:
            gx = ax / mag
            gy = ay / mag
            gz = az / mag

        # Currents I_E1-E4, I_C1-C4 from INA219 (limit 2.2 A red)
        currents = [max(random.gauss(1.0, 0.8), 0.0) for _ in range(8)]
        # Voltages V_E1-E4, V_C1-C4 from INA219 (limit 40 V red)
        voltages = [max(random.gauss(28.0, 10.0), 0.0) for _ in range(8)]

        data = floats_to_be(ax, ay, az, gx, gy, gz, *currents, *voltages)
        pkt = header(seq, apid=201) + data
        tm_socket.sendto(pkt, (TM_SEND_ADDRESS, TM_SEND_PORT))
        seq += 1
        sleep(0.1)  # 10 Hz


def main():
    tm_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    t1 = threading.Thread(target=simulate_2hz, args=(tm_socket,), daemon=True)
    t2 = threading.Thread(target=simulate_10hz, args=(tm_socket,), daemon=True)
    t1.start()
    t2.start()

    # Keep main thread alive
    try:
        while True:
            sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
