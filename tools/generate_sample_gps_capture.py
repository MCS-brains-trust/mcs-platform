#!/usr/bin/env python3
"""
Generate sample GPS capture data for testing analyze_gps_data.py

Creates a synthetic hex dump file that mimics the BLE notification stream
captured from the watch: 0x01C5/0xACAC session bookends, 0x01D1/0xAA04
GPS payloads with FD69 headers, 0x01E0/0xAC02 control frames,
0x01E9/0xAC03 end control, and FDFDD4 terminator.

Usage:
    python generate_sample_gps_capture.py
    python generate_sample_gps_capture.py --points 100 --output capture.hex
    python generate_sample_gps_capture.py --route sydney_loop
    python generate_sample_gps_capture.py --route straight --start-lat -33.8688 --start-lon 151.2093

Then test with:
    python analyze_gps_data.py sample_gps_capture.hex --probe --inspect 5
"""

import argparse
import math
import os
import random
import struct
import time


def generate_route_points(route_type: str, num_points: int,
                          start_lat: float, start_lon: float) -> list:
    """Generate synthetic GPS route coordinates."""
    points = []

    if route_type == "loop":
        # Circular route ~1km radius
        radius_deg = 0.009  # ~1km
        for i in range(num_points):
            angle = (2 * math.pi * i) / num_points
            lat = start_lat + radius_deg * math.sin(angle)
            lon = start_lon + radius_deg * math.cos(angle)
            alt = 50 + 10 * math.sin(angle * 2)  # gentle hills
            speed = 2.5 + random.uniform(-0.5, 0.5)  # ~9 km/h jog
            bearing = math.degrees(angle + math.pi / 2) % 360
            points.append((lat, lon, alt, speed, bearing))

    elif route_type == "straight":
        # Straight line heading north-east
        for i in range(num_points):
            lat = start_lat + (i * 0.00005)  # ~5.5m per point
            lon = start_lon + (i * 0.00003)
            alt = 30 + i * 0.1
            speed = 1.5 + random.uniform(-0.3, 0.3)  # walking
            bearing = 45.0
            points.append((lat, lon, alt, speed, bearing))

    elif route_type == "sydney_loop":
        # Recognisable loop around Sydney CBD (Circular Quay area)
        waypoints = [
            (-33.8568, 151.2153),  # Circular Quay
            (-33.8523, 151.2108),  # The Rocks
            (-33.8546, 151.2066),  # Barangaroo
            (-33.8610, 151.2050),  # Darling Harbour
            (-33.8670, 151.2070),  # Pyrmont Bridge
            (-33.8700, 151.2100),  # Town Hall
            (-33.8688, 151.2150),  # Martin Place
            (-33.8620, 151.2180),  # Royal Botanic
            (-33.8568, 151.2153),  # Back to start
        ]
        # Interpolate between waypoints
        pts_per_segment = num_points // (len(waypoints) - 1)
        for j in range(len(waypoints) - 1):
            lat1, lon1 = waypoints[j]
            lat2, lon2 = waypoints[j + 1]
            for k in range(pts_per_segment):
                t = k / pts_per_segment
                lat = lat1 + t * (lat2 - lat1) + random.uniform(-0.00002, 0.00002)
                lon = lon1 + t * (lon2 - lon1) + random.uniform(-0.00002, 0.00002)
                alt = 15 + random.uniform(-2, 2)
                dlat = lat2 - lat1
                dlon = lon2 - lon1
                bearing = math.degrees(math.atan2(dlon, dlat)) % 360
                speed = 2.0 + random.uniform(-0.5, 0.5)
                points.append((lat, lon, alt, speed, bearing))

    return points[:num_points]


def encode_gps_frame_hypothesis_a(seq: int, timestamp: int,
                                   lat: float, lon: float, alt: float,
                                   speed: float, bearing: float,
                                   satellites: int = 12, hdop: float = 1.2,
                                   total_size: int = 244) -> bytes:
    """
    Encode a GPS frame using Hypothesis A layout (int32 x10^-6, big-endian).
    Pads to total_size with pseudo-random fill to simulate real firmware output.
    """
    buf = bytearray()
    # FD69 header
    buf += b'\xFD\x69'
    # Timestamp (uint32 BE)
    buf += struct.pack(">I", timestamp)
    # Sequence (uint16 BE)
    buf += struct.pack(">H", seq)
    # Latitude (int32 BE, x10^-6)
    buf += struct.pack(">i", int(lat * 1_000_000))
    # Longitude (int32 BE, x10^-6)
    buf += struct.pack(">i", int(lon * 1_000_000))
    # Altitude (int16 BE)
    buf += struct.pack(">h", int(alt))
    # Speed (uint16 BE, cm/s)
    buf += struct.pack(">H", int(speed * 100))
    # Satellites
    buf += struct.pack("B", satellites)
    # HDOP x10
    buf += struct.pack("B", int(hdop * 10))
    # Bearing (uint16 BE, degrees x10)
    buf += struct.pack(">H", int(bearing * 10))

    # Pad remaining bytes with plausible firmware data
    while len(buf) < total_size:
        buf += struct.pack("B", random.randint(0, 255))

    return bytes(buf[:total_size])


def encode_session_start() -> bytes:
    """0x01C5 with 0xACAC flag — session start bookend."""
    return struct.pack(">HH", 0x01C5, 0xACAC) + b'\x01' + bytes(20)


def encode_session_end() -> bytes:
    """0x01C5 with 0xACAC flag — session end bookend."""
    return struct.pack(">HH", 0x01C5, 0xACAC) + b'\x00' + bytes(20)


def encode_control_frame() -> bytes:
    """0x01E0 with 0xAC02 — status/control."""
    return struct.pack(">HH", 0x01E0, 0xAC02) + bytes(12)


def encode_end_control() -> bytes:
    """0x01E9 with 0xAC03 — near-end control."""
    return struct.pack(">HH", 0x01E9, 0xAC03) + bytes(8)


def encode_gps_cmd_wrapper(payload: bytes) -> bytes:
    """Wrap a GPS payload with 0x01D1 command + 0xAA04 flag header."""
    return struct.pack(">HH", 0x01D1, 0xAA04) + payload


def encode_short_frame() -> bytes:
    """Short 0x01D1 frame (9 bytes, 0xAA02 flag)."""
    return struct.pack(">HH", 0x01D1, 0xAA02) + bytes(5)


def encode_terminator() -> bytes:
    """End-of-transfer 0x01D1 frame with FDFDD4 marker (7 bytes)."""
    return struct.pack(">HH", 0x01D1, 0xAA04) + b'\xFD\xFD\xD4'


def main():
    parser = argparse.ArgumentParser(
        description="Generate sample GPS capture data for testing"
    )
    parser.add_argument("--output", "-o", default="sample_gps_capture.hex",
                        help="Output hex dump file")
    parser.add_argument("--points", "-n", type=int, default=50,
                        help="Number of GPS data points (default: 50)")
    parser.add_argument("--route", choices=["loop", "straight", "sydney_loop"],
                        default="sydney_loop",
                        help="Route type (default: sydney_loop)")
    parser.add_argument("--start-lat", type=float, default=-33.8688)
    parser.add_argument("--start-lon", type=float, default=151.2093)
    parser.add_argument("--start-time", type=int, default=None,
                        help="Start Unix timestamp (default: now)")
    parser.add_argument("--interval", type=int, default=1,
                        help="Seconds between GPS points (default: 1)")
    parser.add_argument("--raw-payload", action="store_true",
                        help="Output raw FD69 payloads without command header")
    parser.add_argument("--with-cmd-header", action="store_true",
                        help="Wrap payloads with 0x01D1/0xAA04 command header")

    args = parser.parse_args()

    start_ts = args.start_time or int(time.time()) - (args.points * args.interval)
    route = generate_route_points(args.route, args.points,
                                   args.start_lat, args.start_lon)

    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(args.output, "w") as f:
        f.write(f"# Sample GPS capture - {args.route} route\n")
        f.write(f"# Generated for testing analyze_gps_data.py\n")
        f.write(f"# {args.points} GPS points, {args.interval}s interval\n")
        f.write(f"# Start: ({args.start_lat}, {args.start_lon})\n")
        f.write(f"# Device: 78:02:B7:37:35:F4 (simulated)\n")
        f.write(f"# Channel: 34F2 (service 56FF)\n")
        f.write(f"\n")

        # Session start
        f.write(f"# --- Session start (0x01C5 / 0xACAC) ---\n")
        f.write(encode_session_start().hex() + "\n")

        # First control frame
        f.write(f"# --- Control (0x01E0 / 0xAC02) ---\n")
        f.write(encode_control_frame().hex() + "\n")

        # GPS data frames
        f.write(f"# --- GPS data (0x01D1 / 0xAA04) x{len(route)} ---\n")
        for i, (lat, lon, alt, speed, bearing) in enumerate(route):
            ts = start_ts + (i * args.interval)
            sats = random.randint(8, 16)
            hdop = 0.8 + random.uniform(0, 0.8)

            payload = encode_gps_frame_hypothesis_a(
                seq=i + 1, timestamp=ts,
                lat=lat, lon=lon, alt=alt,
                speed=speed, bearing=bearing,
                satellites=sats, hdop=hdop
            )

            if args.with_cmd_header:
                frame = encode_gps_cmd_wrapper(payload)
            else:
                frame = payload

            f.write(frame.hex() + "\n")

        # Mid-session control
        f.write(f"# --- Control (0x01E0 / 0xAC02) ---\n")
        f.write(encode_control_frame().hex() + "\n")

        # Short frame
        f.write(f"# --- Short frame (0x01D1 / 0xAA02, 9 bytes) ---\n")
        f.write(encode_short_frame().hex() + "\n")

        # End control
        f.write(f"# --- End control (0x01E9 / 0xAC03) ---\n")
        f.write(encode_end_control().hex() + "\n")

        # Second control
        f.write(f"# --- Control (0x01E0 / 0xAC02) ---\n")
        f.write(encode_control_frame().hex() + "\n")

        # Terminator
        f.write(f"# --- Terminator (FDFDD4) ---\n")
        f.write(encode_terminator().hex() + "\n")

        # Session end
        f.write(f"# --- Session end (0x01C5 / 0xACAC) ---\n")
        f.write(encode_session_end().hex() + "\n")

    print(f"Generated {args.output}")
    print(f"  Route:     {args.route}")
    print(f"  Points:    {len(route)}")
    print(f"  Start:     ({args.start_lat}, {args.start_lon})")
    print(f"  Timestamp: {start_ts} -> {start_ts + len(route) * args.interval}")
    print(f"\nTest with:")
    print(f"  python tools/analyze_gps_data.py {args.output} --probe --inspect 5")
    print(f"  python tools/analyze_gps_data.py {args.output} --decoder a --gpx track.gpx")


if __name__ == "__main__":
    main()
