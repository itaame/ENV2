import struct
import socket
import random
import math
from time import sleep

TM_SEND_ADDRESS = '127.0.0.1'
TM_SEND_PORT = 10015

# ---------------------------------------------------------------------------
# Novespace parabolic flight model
# Durations (in seconds) roughly follow a typical Novespace profile:
#   20 s pull‑up at ~1.8 g
#   22 s of microgravity (0 g)
#   20 s pull‑out at ~1.8 g
#   40 s of level flight at ~1 g between parabolas
# One campaign usually consists of 31 parabolas.
# ---------------------------------------------------------------------------
PULL_UP_DURATION = 20.0
ZERO_G_DURATION = 22.0
PULL_OUT_DURATION = 20.0
LEVEL_DURATION = 40.0
PARABOLA_COUNT = 31

# Total duration of a complete parabola cycle
CYCLE_DURATION = PULL_UP_DURATION + ZERO_G_DURATION + PULL_OUT_DURATION + LEVEL_DURATION

# CCSDS-like header helper reused from ENV2.py
# The CCSDS packet length field contains the total packet size minus one.

def header(seq_count: int, apid: int, data_len: int) -> bytes:
    """Build a CCSDS-like primary header.

    *data_len* is the size in bytes of the packet data field.  The length
    written to the header is ``(data_len + 6) - 1`` so that the complete
    packet occupies ``data_len + 6`` bytes.  For a 126-byte packet this value
    becomes 125 in CCSDS units.
    """

    if seq_count >= 16382:
        seq_count = 0
    ccsds_len = data_len + 5  # (data_len + 6) - 1
    return apid.to_bytes(2, 'big') + (49152 + seq_count).to_bytes(2, 'big') + ccsds_len.to_bytes(2, 'big')


def floats_to_be(*values: float) -> bytes:
    return b''.join(struct.pack('>f', v) for v in values)


def estimate_parabola(elapsed: float) -> int:
    """Estimate the current parabola number for a Novespace flight."""
    parabola = int(elapsed // CYCLE_DURATION) + 1
    return min(parabola, PARABOLA_COUNT)


def g_profile(elapsed: float) -> float:
    """Return the expected g level for the given elapsed time."""
    time_in_cycle = elapsed % CYCLE_DURATION
    if time_in_cycle < PULL_UP_DURATION:
        return 1.8  # Pull-up
    time_in_cycle -= PULL_UP_DURATION
    if time_in_cycle < ZERO_G_DURATION:
        return 0.0  # Microgravity
    time_in_cycle -= ZERO_G_DURATION
    if time_in_cycle < PULL_OUT_DURATION:
        return 1.8  # Pull-out
    return 1.0  # Level flight between parabolas


def simulate_all(tm_socket: socket.socket):
    seq = 0
    elapsed = 0.0
    while True:
        # --------------------------- 2 Hz sensors ---------------------------
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

        # --------------------------- 10 Hz sensors ---------------------------
        # a_x/a_y/a_z from LSM6DSOX during parabolic flight
        g_level = g_profile(elapsed)
        ax = random.gauss(0.0, 0.02)
        ay = random.gauss(0.0, 0.02)
        az = random.gauss(g_level, 0.02)

        # Derive normalised gravity vector (gx, gy, gz) and total g-force
        mag = math.sqrt(ax * ax + ay * ay + az * az)
        if mag == 0:
            gx = gy = gz = 0.0
        else:
            gx = ax / mag
            gy = ay / mag
            gz = az / mag
        g_force = mag
        parabola = estimate_parabola(elapsed)

        # Currents I_E1-E4, I_C1-C4 from INA219 (limit 2.2 A red)
        currents = [max(random.gauss(1.0, 0.8), 0.0) for _ in range(8)]
        # Voltages V_E1-E4, V_C1-C4 from INA219 (limit 40 V red)
        voltages = [max(random.gauss(28.0, 10.0), 0.0) for _ in range(8)]

        # Pack everything into a single telemetry packet (APID 0x64 = 100 decimal)
        data = floats_to_be(
            t_liq,
            p_box,
            rh_box,
            t_box,
            h2,
            ax,
            ay,
            az,
            gx,
            gy,
            gz,
            g_force,
            float(parabola),
            *currents,
            *voltages,
            0.0,  # padding to reach 120 data bytes (126 total)
        )
        pkt = header(seq, apid=0x64, data_len=len(data)) + data
        tm_socket.sendto(pkt, (TM_SEND_ADDRESS, TM_SEND_PORT))
        seq += 1
        sleep(0.1)  # 10 Hz
        elapsed += 0.1


def main():
    tm_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        simulate_all(tm_socket)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
