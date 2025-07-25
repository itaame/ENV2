import struct
import socket
import random
import threading
from time import sleep, perf_counter

TM_SEND_ADDRESS = '127.0.0.1'
TM_SEND_PORT    = 10015
TC_LISTEN_ADDRESS = '127.0.0.1'
TC_LISTEN_PORT    = 10025

# TCP ping target for connectivity monitoring
PING_HOST = '127.0.0.1'
PING_PORT = 64738

PACKET_ID_MAP = {
    2: "Pressurize",
    3: "Depressurize"
}

PRESSURIZED_PRESSURE = 1015.0
DEPRESSURIZED_PRESSURE = 0.1
TRANSITION_TIME_SEC = 180
RATE = 1

airlock_target = PRESSURIZED_PRESSURE
airlock_pressure = PRESSURIZED_PRESSURE
suffix_lock = threading.Lock()
fixed_suffix = b'\x00\x00\x00\x00'  # Default: pressurized state

# Network monitoring metrics
net_lock = threading.Lock()
net_latency = 0.0  # in seconds
packet_loss = 0.0  # in percent

def floats_to_ieee754_with_prefix_suffix(*values):
    return b''.join(struct.pack('>f', value) for value in values)

def Values_sim(CO2_Level, Hum_Level, Temp, Airlock_Press, Cabin_Press, Ammonia_Level, Air_Quality, seq_count):
    CO2_Level += round(random.uniform(-5, 5), 2)
    Hum_Level += round(random.uniform(-1, 1), 2)
    Temp += round(random.uniform(-0.5, 0.5), 2)
    Cabin_Press += round(random.uniform(-5, 5), 2)
    Ammonia_Level += round(random.uniform(-0.1, 0.1), 2)
    Air_Quality += round(random.uniform(-0.5, 0.5), 2)
    values_ENV = [CO2_Level, Hum_Level, Temp, Airlock_Press, Cabin_Press, Ammonia_Level, Air_Quality]
    with suffix_lock:
        suffix = fixed_suffix
    byte_seq = Header(seq_count, apid=100) + floats_to_ieee754_with_prefix_suffix(*values_ENV) + suffix
    return byte_seq

def Header(seq_count: int, apid: int = 100) -> bytes:
    """Build a simple CCSDS‑like header for the given APID and sequence count."""
    if seq_count >= 16382:
        seq_count = 0
    return apid.to_bytes(2, 'big') + (49152 + seq_count).to_bytes(2, 'big') + b'\x01\x45'


def check(host: str, port: int, timeout: float = 2) -> bool:
    """Attempt a TCP connection to host:port within *timeout* seconds."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
            return True
        except (socket.timeout, OSError):
            return False


def timed_check(host: str, port: int, timeout: float = 2) -> float | None:
    """Return round-trip time in seconds if reachable, otherwise None."""
    t0 = perf_counter()
    if check(host, port, timeout):
        return perf_counter() - t0
    return None


def network_monitor(host: str, port: int, attempts: int = 4, timeout: float = 2):
    """Continuously update latency and packet loss statistics."""
    global net_latency, packet_loss
    while True:
        total = 0.0
        errors = 0
        for _ in range(attempts):
            lat = timed_check(host, port, timeout)
            if lat is None:
                errors += 1
            else:
                total += lat
            sleep(0.1)
        success = attempts - errors
        with net_lock:
            if success:
                net_latency = total / success
            else:
                net_latency = 0.0
            packet_loss = (errors / attempts) * 100

def tc_listener():
    global airlock_target
    print(f"Listening for TCs on {TC_LISTEN_ADDRESS}:{TC_LISTEN_PORT} ...")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # Safety: allow reuse
            s.bind((TC_LISTEN_ADDRESS, TC_LISTEN_PORT))
            while True:
                data, addr = s.recvfrom(1024)
                if len(data) == 10:
                    user_data = data[6:]
                    packet_id = struct.unpack(">H", user_data[:2])[0]
                    airlock = struct.unpack(">H", user_data[2:])[0]
                    command = PACKET_ID_MAP.get(packet_id)
                    if command and airlock == 1:
                        with suffix_lock:
                            if command == "Pressurize":
                                airlock_target = PRESSURIZED_PRESSURE
                                print("TC RECEIVED: Pressurize Airlock 1 — starting pressurization!")
                            elif command == "Depressurize":
                                airlock_target = DEPRESSURIZED_PRESSURE
                                print("TC RECEIVED: Depressurize Airlock 1 — starting depressurization!")
                    else:
                        print(f"TC RECEIVED: {command} Airlock {airlock} (ignored)")
                else:
                    print(f"TC RECEIVED: Invalid length {len(data)} (ignored)")
    except OSError as e:
        print(f"ERROR: Could not bind TC listener socket on {TC_LISTEN_ADDRESS}:{TC_LISTEN_PORT}: {e}")
        print("Is another process using this port? Please kill it and restart this script.")

def main():
    global fixed_suffix, airlock_pressure, airlock_target
    # Start the TC listener thread
    t = threading.Thread(target=tc_listener, daemon=True)
    t.start()

    # Start network monitoring thread
    net_thread = threading.Thread(target=network_monitor, args=(PING_HOST, PING_PORT), daemon=True)
    net_thread.start()

    seq_count = 0
    net_seq_count = 0
    CO2_Level =  700
    Hum_Level = 90
    Temp =  21.5
    Cabin_Press = 1015
    Ammonia_Level = 2.5
    Air_Quality = 25

    print('Sending ENV Data Using playback rate of ', str(RATE) + 'Hz')
    print('TM host=' + str(TM_SEND_ADDRESS) + ', TM port=' + str(TM_SEND_PORT))
    tm_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    global_step = 0
    last_suffix = None
    while True:
        global airlock_pressure
        with suffix_lock:
            target = airlock_target
            current = airlock_pressure
            step_size = abs(PRESSURIZED_PRESSURE - DEPRESSURIZED_PRESSURE) / (TRANSITION_TIME_SEC * RATE)
            if abs(current - target) > 0.01:
                if current < target:
                    airlock_pressure = min(current + step_size, target)
                else:
                    airlock_pressure = max(current - step_size, target)
                if abs(airlock_pressure - PRESSURIZED_PRESSURE) < 0.01:
                    fixed_suffix = b'\x00\x00\x00\x00'
                    print("Airlock now fully pressurized (suffix set).")
                elif abs(airlock_pressure - DEPRESSURIZED_PRESSURE) < 0.01:
                    fixed_suffix = b'\x20\x00\x00\x00'
                    print("Airlock now fully depressurized (suffix set).")
            else:
                if abs(target - PRESSURIZED_PRESSURE) < 0.01:
                    fixed_suffix = b'\x00\x00\x00\x00'
                elif abs(target - DEPRESSURIZED_PRESSURE) < 0.01:
                    fixed_suffix = b'\x20\x00\x00\x00'
            state = "pressurized" if fixed_suffix == b'\x00\x00\x00\x00' else "depressurized"
            if last_suffix != fixed_suffix or global_step % 5 == 0:
                print(f"Airlock pressure: {airlock_pressure:.2f} hPa | Target: {target} | State: {state} | Suffix: {fixed_suffix.hex()}")
                last_suffix = fixed_suffix
            global_step += 1

        byte_seq = Values_sim(CO2_Level, Hum_Level, Temp, airlock_pressure, Cabin_Press, Ammonia_Level, Air_Quality, seq_count)
        tm_socket.sendto(byte_seq, (TM_SEND_ADDRESS, TM_SEND_PORT))
        seq_count += 1

        # Send network metrics packet (APID 103)
        with net_lock:
            latency_ms = net_latency * 1000
            loss = packet_loss
        net_packet = Header(net_seq_count, apid=103) + floats_to_ieee754_with_prefix_suffix(latency_ms, loss)
        tm_socket.sendto(net_packet, (TM_SEND_ADDRESS, TM_SEND_PORT))
        net_seq_count += 1
        sleep(1/RATE)

if __name__ == "__main__":
    main()
