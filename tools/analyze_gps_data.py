#!/usr/bin/env python3
"""
GPS Data Analyzer for Nadal Protocol BLE Captures

Decodes 0x01D1 GPS/GNSS measurement payloads captured from watch BLE
notifications on characteristic 34F2 (service 56FF).

Usage:
    # Analyze a hex dump file (one payload per line)
    python analyze_gps_data.py hexdump.txt

    # Analyze with known reference point for validation
    python analyze_gps_data.py hexdump.txt --ref-lat -33.8688 --ref-lon 151.2093

    # Output decoded points as GPX track
    python analyze_gps_data.py hexdump.txt --gpx output.gpx

    # Output as CSV
    python analyze_gps_data.py hexdump.txt --csv output.csv

    # Run in probe mode to test all byte layout hypotheses
    python analyze_gps_data.py hexdump.txt --probe

    # Read from raw binary capture file
    python analyze_gps_data.py capture.bin --binary --frame-size 244

Input format (hex dump):
    Each line should be a hex string representing one BLE notification payload.
    Lines starting with # are comments. Empty lines are skipped.
    The full notification including command header, or just the payload portion.

Protocol context:
    Command:  0x01D1 (GPS data transfer)
    Flag:     0xAA04 (workout-associated GNSS)
    Prefix:   FD69 (timestamp header)
    Size:     244 bytes per frame (standard), 9 bytes (short), 7 bytes (terminator)
    Terminal: FDFDD4 (end-of-transfer marker)
    Session:  0x01C5/0xACAC bookends, 0x01E0/0xAC02 control, 0x01E9/0xAC03 end
"""

import argparse
import csv
import datetime
import io
import json
import math
import os
import struct
import sys
from dataclasses import dataclass, field
from typing import Optional


# --- Protocol Constants ---

CMD_GPS_DATA = 0x01D1
CMD_FRAME_INDEX = 0x01C5
CMD_GPS_CONTROL = 0x01E0
CMD_GPS_END = 0x01E9

FLAG_GPS_PAYLOAD = 0xAA04
FLAG_SESSION_BOOKEND = 0xACAC
FLAG_STATUS_CONTROL = 0xAC02
FLAG_END_CONTROL = 0xAC03

GPS_HEADER = bytes.fromhex("FD69")
END_MARKER = bytes.fromhex("FDFDD4")

STANDARD_FRAME_SIZE = 244
SHORT_FRAME_SIZE = 9
TERMINATOR_FRAME_SIZE = 7


# --- Data Structures ---

@dataclass
class GPSPoint:
    """A single decoded GPS measurement."""
    sequence: int = 0
    timestamp: int = 0
    timestamp_dt: Optional[datetime.datetime] = None
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    speed: float = 0.0
    bearing: float = 0.0
    accuracy: float = 0.0
    satellites: int = 0
    hdop: float = 0.0
    raw_hex: str = ""
    decode_method: str = ""

    @property
    def is_valid(self) -> bool:
        return (-90 <= self.latitude <= 90 and
                -180 <= self.longitude <= 180 and
                self.latitude != 0.0 and self.longitude != 0.0)


@dataclass
class GPSSession:
    """A complete GPS capture session."""
    device_mac: str = ""
    points: list = field(default_factory=list)
    raw_frames: list = field(default_factory=list)
    control_frames: list = field(default_factory=list)
    session_start: Optional[bytes] = None
    session_end: Optional[bytes] = None
    end_markers: list = field(default_factory=list)

    @property
    def total_distance_m(self) -> float:
        """Calculate total distance using haversine."""
        valid = [p for p in self.points if p.is_valid]
        total = 0.0
        for i in range(1, len(valid)):
            total += haversine(
                valid[i - 1].latitude, valid[i - 1].longitude,
                valid[i].latitude, valid[i].longitude
            )
        return total

    @property
    def duration_s(self) -> float:
        valid = [p for p in self.points if p.timestamp > 0]
        if len(valid) < 2:
            return 0.0
        return valid[-1].timestamp - valid[0].timestamp

    @property
    def avg_speed_kmh(self) -> float:
        d = self.duration_s
        if d <= 0:
            return 0.0
        return (self.total_distance_m / d) * 3.6


# --- Geometry ---

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in metres between two lat/lon points."""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# --- Payload Decoders ---
# Multiple hypotheses for the FD69 payload layout. The correct one will be
# identified by validating decoded coordinates against a known route or by
# checking that lat/lon fall within plausible ranges.

def decode_hypothesis_a(payload: bytes) -> Optional[GPSPoint]:
    """
    Hypothesis A: Compact GNSS record (common in Chinese wearable chipsets)
    Bytes 0-1:   FD69 header
    Bytes 2-5:   Unix timestamp (uint32 big-endian)
    Bytes 6-7:   Sequence counter (uint16 big-endian)
    Bytes 8-11:  Latitude (int32 big-endian, × 10^-6 degrees)
    Bytes 12-15: Longitude (int32 big-endian, × 10^-6 degrees)
    Bytes 16-17: Altitude (int16 big-endian, metres)
    Bytes 18-19: Speed (uint16 big-endian, cm/s)
    Byte 20:     Satellite count
    Byte 21:     Fix quality (HDOP × 10)
    Bytes 22-23: Bearing (uint16 big-endian, degrees × 10)
    """
    if len(payload) < 24:
        return None
    try:
        pt = GPSPoint(decode_method="hypothesis_a_be")
        pt.raw_hex = payload[:24].hex()
        pt.timestamp = struct.unpack(">I", payload[2:6])[0]
        pt.sequence = struct.unpack(">H", payload[6:8])[0]
        lat_raw = struct.unpack(">i", payload[8:12])[0]
        lon_raw = struct.unpack(">i", payload[12:16])[0]
        pt.latitude = lat_raw / 1_000_000.0
        pt.longitude = lon_raw / 1_000_000.0
        pt.altitude = struct.unpack(">h", payload[16:18])[0]
        pt.speed = struct.unpack(">H", payload[18:20])[0] / 100.0  # cm/s -> m/s
        pt.satellites = payload[20]
        pt.hdop = payload[21] / 10.0
        pt.bearing = struct.unpack(">H", payload[22:24])[0] / 10.0
        if pt.timestamp > 1_000_000_000:
            pt.timestamp_dt = datetime.datetime.utcfromtimestamp(pt.timestamp)
        return pt
    except (struct.error, IndexError):
        return None


def decode_hypothesis_b(payload: bytes) -> Optional[GPSPoint]:
    """
    Hypothesis B: Little-endian variant (some Realtek/Nordic chips)
    Same layout as A but with little-endian byte order.
    """
    if len(payload) < 24:
        return None
    try:
        pt = GPSPoint(decode_method="hypothesis_b_le")
        pt.raw_hex = payload[:24].hex()
        pt.timestamp = struct.unpack("<I", payload[2:6])[0]
        pt.sequence = struct.unpack("<H", payload[6:8])[0]
        lat_raw = struct.unpack("<i", payload[8:12])[0]
        lon_raw = struct.unpack("<i", payload[12:16])[0]
        pt.latitude = lat_raw / 1_000_000.0
        pt.longitude = lon_raw / 1_000_000.0
        pt.altitude = struct.unpack("<h", payload[16:18])[0]
        pt.speed = struct.unpack("<H", payload[18:20])[0] / 100.0
        pt.satellites = payload[20]
        pt.hdop = payload[21] / 10.0
        pt.bearing = struct.unpack("<H", payload[22:24])[0] / 10.0
        if pt.timestamp > 1_000_000_000:
            pt.timestamp_dt = datetime.datetime.utcfromtimestamp(pt.timestamp)
        return pt
    except (struct.error, IndexError):
        return None


def decode_hypothesis_c(payload: bytes) -> Optional[GPSPoint]:
    """
    Hypothesis C: Double-precision lat/lon (some higher-end GNSS modules)
    Bytes 0-1:   FD69 header
    Bytes 2-5:   Unix timestamp (uint32 big-endian)
    Bytes 6-7:   Sequence counter (uint16 big-endian)
    Bytes 8-11:  Latitude degrees (int16) + minutes×10000 (uint16) big-endian
    Bytes 12-15: Longitude degrees (int16) + minutes×10000 (uint16) big-endian
    Bytes 16-17: Altitude (int16 big-endian)
    Bytes 18-19: Speed (uint16, mm/s)
    Byte 20:     Satellites
    Byte 21:     Fix type
    """
    if len(payload) < 22:
        return None
    try:
        pt = GPSPoint(decode_method="hypothesis_c_degmin")
        pt.raw_hex = payload[:22].hex()
        pt.timestamp = struct.unpack(">I", payload[2:6])[0]
        pt.sequence = struct.unpack(">H", payload[6:8])[0]
        lat_deg = struct.unpack(">h", payload[8:10])[0]
        lat_min = struct.unpack(">H", payload[10:12])[0] / 10000.0
        lon_deg = struct.unpack(">h", payload[12:14])[0]
        lon_min = struct.unpack(">H", payload[14:16])[0] / 10000.0
        pt.latitude = lat_deg + (lat_min / 60.0) if lat_deg >= 0 else lat_deg - (lat_min / 60.0)
        pt.longitude = lon_deg + (lon_min / 60.0) if lon_deg >= 0 else lon_deg - (lon_min / 60.0)
        pt.altitude = struct.unpack(">h", payload[16:18])[0]
        pt.speed = struct.unpack(">H", payload[18:20])[0] / 1000.0  # mm/s -> m/s
        pt.satellites = payload[20]
        pt.accuracy = payload[21]
        if pt.timestamp > 1_000_000_000:
            pt.timestamp_dt = datetime.datetime.utcfromtimestamp(pt.timestamp)
        return pt
    except (struct.error, IndexError):
        return None


def decode_hypothesis_d(payload: bytes) -> Optional[GPSPoint]:
    """
    Hypothesis D: IEEE 754 float coordinates (some firmware use raw floats)
    Bytes 0-1:   FD69 header
    Bytes 2-5:   Unix timestamp (uint32 big-endian)
    Bytes 6-7:   Sequence counter (uint16 big-endian)
    Bytes 8-11:  Latitude (float32 big-endian)
    Bytes 12-15: Longitude (float32 big-endian)
    Bytes 16-19: Altitude (float32 big-endian)
    Bytes 20-23: Speed (float32 big-endian, m/s)
    Byte 24:     Satellites
    """
    if len(payload) < 25:
        return None
    try:
        pt = GPSPoint(decode_method="hypothesis_d_float_be")
        pt.raw_hex = payload[:25].hex()
        pt.timestamp = struct.unpack(">I", payload[2:6])[0]
        pt.sequence = struct.unpack(">H", payload[6:8])[0]
        pt.latitude = struct.unpack(">f", payload[8:12])[0]
        pt.longitude = struct.unpack(">f", payload[12:16])[0]
        pt.altitude = struct.unpack(">f", payload[16:20])[0]
        pt.speed = struct.unpack(">f", payload[20:24])[0]
        pt.satellites = payload[24]
        if pt.timestamp > 1_000_000_000:
            pt.timestamp_dt = datetime.datetime.utcfromtimestamp(pt.timestamp)
        return pt
    except (struct.error, IndexError):
        return None


def decode_hypothesis_e(payload: bytes) -> Optional[GPSPoint]:
    """
    Hypothesis E: Same as D but little-endian floats.
    """
    if len(payload) < 25:
        return None
    try:
        pt = GPSPoint(decode_method="hypothesis_e_float_le")
        pt.raw_hex = payload[:25].hex()
        pt.timestamp = struct.unpack("<I", payload[2:6])[0]
        pt.sequence = struct.unpack("<H", payload[6:8])[0]
        pt.latitude = struct.unpack("<f", payload[8:12])[0]
        pt.longitude = struct.unpack("<f", payload[12:16])[0]
        pt.altitude = struct.unpack("<f", payload[16:20])[0]
        pt.speed = struct.unpack("<f", payload[20:24])[0]
        pt.satellites = payload[24]
        if pt.timestamp > 1_000_000_000:
            pt.timestamp_dt = datetime.datetime.utcfromtimestamp(pt.timestamp)
        return pt
    except (struct.error, IndexError):
        return None


def decode_hypothesis_f(payload: bytes) -> Optional[GPSPoint]:
    """
    Hypothesis F: Offset timestamp + packed GNSS (Nadal-style TLV variant)
    Bytes 0-1:   FD69 header
    Bytes 2-3:   Sequence counter (uint16 big-endian)
    Bytes 4-7:   Unix timestamp (uint32 big-endian)
    Bytes 8-11:  Latitude (int32 big-endian, × 10^-7 degrees — NMEA convention)
    Bytes 12-15: Longitude (int32 big-endian, × 10^-7 degrees)
    Bytes 16-19: Altitude (int32 big-endian, mm)
    Bytes 20-21: Speed (uint16, cm/s)
    Bytes 22-23: Bearing (uint16, degrees × 100)
    Byte 24:     Fix type (0=none, 1=GPS, 2=DGPS, 3=PPS)
    Byte 25:     Satellites
    Bytes 26-27: HDOP (uint16, × 100)
    """
    if len(payload) < 28:
        return None
    try:
        pt = GPSPoint(decode_method="hypothesis_f_nmea7")
        pt.raw_hex = payload[:28].hex()
        pt.sequence = struct.unpack(">H", payload[2:4])[0]
        pt.timestamp = struct.unpack(">I", payload[4:8])[0]
        lat_raw = struct.unpack(">i", payload[8:12])[0]
        lon_raw = struct.unpack(">i", payload[12:16])[0]
        pt.latitude = lat_raw / 10_000_000.0
        pt.longitude = lon_raw / 10_000_000.0
        pt.altitude = struct.unpack(">i", payload[16:20])[0] / 1000.0
        pt.speed = struct.unpack(">H", payload[20:22])[0] / 100.0
        pt.bearing = struct.unpack(">H", payload[22:24])[0] / 100.0
        pt.accuracy = payload[24]
        pt.satellites = payload[25]
        pt.hdop = struct.unpack(">H", payload[26:28])[0] / 100.0
        if pt.timestamp > 1_000_000_000:
            pt.timestamp_dt = datetime.datetime.utcfromtimestamp(pt.timestamp)
        return pt
    except (struct.error, IndexError):
        return None


DECODERS = [
    ("A: int32 ×10^-6 BE", decode_hypothesis_a),
    ("B: int32 ×10^-6 LE", decode_hypothesis_b),
    ("C: deg+min BE", decode_hypothesis_c),
    ("D: float32 BE", decode_hypothesis_d),
    ("E: float32 LE", decode_hypothesis_e),
    ("F: int32 ×10^-7 NMEA BE", decode_hypothesis_f),
]


# --- Frame Parsing ---

def classify_frame(data: bytes) -> dict:
    """Classify a raw BLE notification frame."""
    info = {
        "size": len(data),
        "type": "unknown",
        "command": None,
        "flag": None,
        "payload": data,
    }

    if len(data) < 4:
        info["type"] = "short_unknown"
        return info

    # Check for end-of-transfer marker
    if END_MARKER in data:
        info["type"] = "end_marker"
        return info

    # Check for FD69 GPS header
    if data[:2] == GPS_HEADER:
        info["type"] = "gps_payload"
        info["payload"] = data
        return info

    # Try to extract command + flag from Nadal frame header
    # Nadal frames typically: [len(2)][cmd(2)][flag(2)][payload...]
    # But raw notifications may just be the payload after protocol framing
    cmd = struct.unpack(">H", data[0:2])[0] if len(data) >= 2 else None
    flag = struct.unpack(">H", data[2:4])[0] if len(data) >= 4 else None

    if cmd == CMD_GPS_DATA:
        info["type"] = "gps_data_cmd"
        info["command"] = cmd
        info["flag"] = flag
        info["payload"] = data[4:]
    elif cmd == CMD_FRAME_INDEX:
        info["type"] = "frame_index"
        info["command"] = cmd
        info["flag"] = flag
        info["payload"] = data[4:]
    elif cmd == CMD_GPS_CONTROL:
        info["type"] = "gps_control"
        info["command"] = cmd
        info["flag"] = flag
        info["payload"] = data[4:]
    elif cmd == CMD_GPS_END:
        info["type"] = "gps_end"
        info["command"] = cmd
        info["flag"] = flag
        info["payload"] = data[4:]
    else:
        # May be a raw GPS payload without command header
        # Look for FD69 anywhere in first 10 bytes
        fd69_pos = data[:10].find(GPS_HEADER)
        if fd69_pos >= 0:
            info["type"] = "gps_payload_offset"
            info["payload"] = data[fd69_pos:]

    return info


def parse_hex_dump(lines: list[str]) -> list[bytes]:
    """Parse hex dump lines into raw byte arrays."""
    frames = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        # Remove common prefixes from BLE loggers
        for prefix in ["0x", "data:", "payload:", "notify:", "<<", ">>"]:
            if line.lower().startswith(prefix):
                line = line[len(prefix):].strip()
        # Remove spaces, colons, dashes (common hex separators)
        cleaned = line.replace(" ", "").replace(":", "").replace("-", "")
        # Validate hex
        try:
            data = bytes.fromhex(cleaned)
            if len(data) > 0:
                frames.append(data)
        except ValueError:
            # Try extracting hex portion from mixed content
            hex_chars = "".join(c for c in cleaned if c in "0123456789abcdefABCDEF")
            if len(hex_chars) >= 4:
                try:
                    data = bytes.fromhex(hex_chars)
                    frames.append(data)
                except ValueError:
                    pass
    return frames


def parse_binary_file(filepath: str, frame_size: int = 244) -> list[bytes]:
    """Parse a raw binary capture file into fixed-size frames."""
    frames = []
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(frame_size)
            if not chunk:
                break
            frames.append(chunk)
    return frames


# --- Analysis ---

def probe_all_decoders(frames: list[bytes], ref_lat: float = None,
                       ref_lon: float = None) -> dict:
    """Test all decoder hypotheses against the frames and score them."""
    results = {}

    for name, decoder in DECODERS:
        points = []
        valid_count = 0
        near_ref_count = 0
        sequential_ts = 0
        sequential_seq = 0

        for frame_info in frames:
            payload = frame_info if isinstance(frame_info, bytes) else frame_info
            classified = classify_frame(payload)

            if classified["type"] in ("gps_payload", "gps_payload_offset",
                                       "gps_data_cmd"):
                gps_payload = classified["payload"]
                # Find FD69 in payload
                fd69_pos = gps_payload.find(GPS_HEADER)
                if fd69_pos >= 0:
                    pt = decoder(gps_payload[fd69_pos:])
                    if pt:
                        points.append(pt)
                        if pt.is_valid:
                            valid_count += 1
                            if ref_lat is not None and ref_lon is not None:
                                dist = haversine(pt.latitude, pt.longitude,
                                                 ref_lat, ref_lon)
                                if dist < 50_000:  # within 50km
                                    near_ref_count += 1

        # Score sequential timestamps
        for i in range(1, len(points)):
            if 0 < points[i].timestamp - points[i - 1].timestamp <= 60:
                sequential_ts += 1
            if points[i].sequence == points[i - 1].sequence + 1:
                sequential_seq += 1

        total = len(points)
        score = 0
        if total > 0:
            score += (valid_count / total) * 40  # valid coordinates: 40 pts
            score += (sequential_seq / max(total - 1, 1)) * 30  # sequential: 30 pts
            if ref_lat is not None:
                score += (near_ref_count / max(valid_count, 1)) * 30  # near ref: 30 pts
            else:
                score += (sequential_ts / max(total - 1, 1)) * 30

        results[name] = {
            "points": points,
            "total_frames": total,
            "valid_coords": valid_count,
            "near_reference": near_ref_count,
            "sequential_timestamps": sequential_ts,
            "sequential_sequences": sequential_seq,
            "score": round(score, 1),
        }

    return results


def analyze_session(frames: list[bytes], decoder_fn=None) -> GPSSession:
    """Analyze a full capture session."""
    session = GPSSession()

    for raw in frames:
        classified = classify_frame(raw)
        frame_type = classified["type"]

        if frame_type == "end_marker":
            session.end_markers.append(raw)
        elif frame_type == "frame_index":
            if classified["flag"] == FLAG_SESSION_BOOKEND:
                if session.session_start is None:
                    session.session_start = raw
                else:
                    session.session_end = raw
        elif frame_type in ("gps_control", "gps_end"):
            session.control_frames.append({
                "type": frame_type,
                "command": classified["command"],
                "flag": classified["flag"],
                "payload_hex": classified["payload"].hex(),
            })
        elif frame_type in ("gps_payload", "gps_payload_offset", "gps_data_cmd"):
            session.raw_frames.append(raw)
            payload = classified["payload"]
            fd69_pos = payload.find(GPS_HEADER)

            if fd69_pos >= 0 and decoder_fn:
                pt = decoder_fn(payload[fd69_pos:])
                if pt:
                    pt.raw_hex = raw.hex()
                    session.points.append(pt)

    return session


# --- Output Formats ---

def write_gpx(session: GPSSession, filepath: str):
    """Write decoded GPS points as a GPX track file."""
    valid = [p for p in session.points if p.is_valid]
    if not valid:
        print(f"  No valid GPS points to write to GPX.")
        return

    buf = io.StringIO()
    buf.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    buf.write('<gpx version="1.1" creator="nadal-gps-analyzer"\n')
    buf.write('     xmlns="http://www.topografix.com/GPX/1/1">\n')
    buf.write('  <trk>\n')
    buf.write('    <name>Watch GPS Track</name>\n')
    buf.write('    <trkseg>\n')

    for pt in valid:
        ts = pt.timestamp_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if pt.timestamp_dt else ""
        buf.write(f'      <trkpt lat="{pt.latitude:.7f}" lon="{pt.longitude:.7f}">\n')
        if pt.altitude != 0:
            buf.write(f'        <ele>{pt.altitude:.1f}</ele>\n')
        if ts:
            buf.write(f'        <time>{ts}</time>\n')
        if pt.speed > 0:
            buf.write(f'        <speed>{pt.speed:.2f}</speed>\n')
        if pt.satellites > 0:
            buf.write(f'        <sat>{pt.satellites}</sat>\n')
        if pt.hdop > 0:
            buf.write(f'        <hdop>{pt.hdop:.1f}</hdop>\n')
        buf.write('      </trkpt>\n')

    buf.write('    </trkseg>\n')
    buf.write('  </trk>\n')
    buf.write('</gpx>\n')

    with open(filepath, "w") as f:
        f.write(buf.getvalue())
    print(f"  GPX written: {filepath} ({len(valid)} points)")


def write_csv(session: GPSSession, filepath: str):
    """Write decoded GPS points as CSV."""
    valid = [p for p in session.points if p.is_valid]
    if not valid:
        print(f"  No valid GPS points to write to CSV.")
        return

    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "sequence", "timestamp", "datetime_utc",
            "latitude", "longitude", "altitude_m",
            "speed_ms", "bearing_deg", "satellites",
            "hdop", "accuracy", "decode_method"
        ])
        for pt in valid:
            ts_str = pt.timestamp_dt.strftime("%Y-%m-%d %H:%M:%S") if pt.timestamp_dt else ""
            writer.writerow([
                pt.sequence, pt.timestamp, ts_str,
                f"{pt.latitude:.7f}", f"{pt.longitude:.7f}", f"{pt.altitude:.1f}",
                f"{pt.speed:.2f}", f"{pt.bearing:.1f}", pt.satellites,
                f"{pt.hdop:.1f}", f"{pt.accuracy:.1f}", pt.decode_method
            ])
    print(f"  CSV written: {filepath} ({len(valid)} points)")


def write_json(session: GPSSession, filepath: str):
    """Write full session analysis as JSON."""
    valid = [p for p in session.points if p.is_valid]
    data = {
        "session": {
            "total_frames": len(session.raw_frames),
            "decoded_points": len(session.points),
            "valid_points": len(valid),
            "end_markers": len(session.end_markers),
            "control_frames": session.control_frames,
            "total_distance_m": round(session.total_distance_m, 1),
            "duration_s": round(session.duration_s, 1),
            "avg_speed_kmh": round(session.avg_speed_kmh, 1),
        },
        "points": [
            {
                "seq": p.sequence,
                "ts": p.timestamp,
                "dt": p.timestamp_dt.isoformat() if p.timestamp_dt else None,
                "lat": round(p.latitude, 7),
                "lon": round(p.longitude, 7),
                "alt": round(p.altitude, 1),
                "spd": round(p.speed, 2),
                "brg": round(p.bearing, 1),
                "sat": p.satellites,
                "hdop": round(p.hdop, 1),
            }
            for p in valid
        ],
    }
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  JSON written: {filepath} ({len(valid)} points)")


# --- Hex Dump Inspector ---

def hex_dump_frame(data: bytes, width: int = 16) -> str:
    """Pretty hex dump of a single frame."""
    lines = []
    for i in range(0, len(data), width):
        chunk = data[i:i + width]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"  {i:04x}  {hex_part:<{width * 3}}  {ascii_part}")
    return "\n".join(lines)


def inspect_frames(frames: list[bytes], max_show: int = 10):
    """Print detailed inspection of first N frames."""
    print(f"\n{'=' * 70}")
    print(f"FRAME INSPECTION (showing first {min(max_show, len(frames))} of {len(frames)})")
    print(f"{'=' * 70}")

    gps_count = 0
    for i, raw in enumerate(frames):
        classified = classify_frame(raw)
        if classified["type"] not in ("gps_payload", "gps_payload_offset",
                                       "gps_data_cmd"):
            continue
        gps_count += 1
        if gps_count > max_show:
            break

        print(f"\n--- GPS Frame #{gps_count} (raw frame #{i + 1}, {len(raw)} bytes) ---")
        print(f"  Type: {classified['type']}")
        if classified["command"]:
            print(f"  Command: 0x{classified['command']:04X}")
        if classified["flag"]:
            print(f"  Flag: 0x{classified['flag']:04X}")
        print(f"  Raw hex dump:")
        print(hex_dump_frame(raw))

        payload = classified["payload"]
        fd69_pos = payload.find(GPS_HEADER)
        if fd69_pos >= 0:
            print(f"\n  FD69 found at offset {fd69_pos} in payload")
            gps_data = payload[fd69_pos:]
            print(f"  First 32 bytes after FD69:")
            if len(gps_data) >= 2:
                print(f"    Bytes [0:2]  header:    {gps_data[0:2].hex()}")
            if len(gps_data) >= 6:
                ts_be = struct.unpack(">I", gps_data[2:6])[0]
                ts_le = struct.unpack("<I", gps_data[2:6])[0]
                print(f"    Bytes [2:6]  field1:    {gps_data[2:6].hex()}  "
                      f"(BE uint32={ts_be}, LE uint32={ts_le})")
                for ts_val, endian in [(ts_be, "BE"), (ts_le, "LE")]:
                    if 1_600_000_000 < ts_val < 2_000_000_000:
                        dt = datetime.datetime.utcfromtimestamp(ts_val)
                        print(f"             -> {endian} looks like timestamp: {dt}")
            if len(gps_data) >= 8:
                seq_be = struct.unpack(">H", gps_data[6:8])[0]
                seq_le = struct.unpack("<H", gps_data[6:8])[0]
                print(f"    Bytes [6:8]  field2:    {gps_data[6:8].hex()}  "
                      f"(BE uint16={seq_be}, LE uint16={seq_le})")
            if len(gps_data) >= 12:
                i32_be = struct.unpack(">i", gps_data[8:12])[0]
                i32_le = struct.unpack("<i", gps_data[8:12])[0]
                f32_be = struct.unpack(">f", gps_data[8:12])[0]
                f32_le = struct.unpack("<f", gps_data[8:12])[0]
                print(f"    Bytes [8:12] field3:    {gps_data[8:12].hex()}  "
                      f"(BE int32={i32_be}, LE int32={i32_le})")
                print(f"             -> as ×10^-6: BE={i32_be / 1e6:.6f}  LE={i32_le / 1e6:.6f}")
                print(f"             -> as ×10^-7: BE={i32_be / 1e7:.7f}  LE={i32_le / 1e7:.7f}")
                print(f"             -> as float:  BE={f32_be:.6f}  LE={f32_le:.6f}")
            if len(gps_data) >= 16:
                i32_be = struct.unpack(">i", gps_data[12:16])[0]
                i32_le = struct.unpack("<i", gps_data[12:16])[0]
                f32_be = struct.unpack(">f", gps_data[12:16])[0]
                f32_le = struct.unpack("<f", gps_data[12:16])[0]
                print(f"    Bytes [12:16] field4:   {gps_data[12:16].hex()}  "
                      f"(BE int32={i32_be}, LE int32={i32_le})")
                print(f"             -> as ×10^-6: BE={i32_be / 1e6:.6f}  LE={i32_le / 1e6:.6f}")
                print(f"             -> as ×10^-7: BE={i32_be / 1e7:.7f}  LE={i32_le / 1e7:.7f}")
                print(f"             -> as float:  BE={f32_be:.6f}  LE={f32_le:.6f}")


# --- Main ---

def main():
    parser = argparse.ArgumentParser(
        description="Analyze GPS data from Nadal protocol BLE captures (0x01D1)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("input", help="Hex dump file (one frame per line) or binary capture")
    parser.add_argument("--binary", action="store_true",
                        help="Input is raw binary (not hex text)")
    parser.add_argument("--frame-size", type=int, default=244,
                        help="Frame size for binary input (default: 244)")
    parser.add_argument("--ref-lat", type=float, default=None,
                        help="Reference latitude for validation")
    parser.add_argument("--ref-lon", type=float, default=None,
                        help="Reference longitude for validation")
    parser.add_argument("--gpx", type=str, default=None,
                        help="Output GPX track file path")
    parser.add_argument("--csv", type=str, default=None,
                        help="Output CSV file path")
    parser.add_argument("--json", type=str, default=None,
                        help="Output JSON analysis file path")
    parser.add_argument("--probe", action="store_true",
                        help="Test all decoder hypotheses and score them")
    parser.add_argument("--inspect", type=int, default=0, metavar="N",
                        help="Show detailed hex inspection of first N GPS frames")
    parser.add_argument("--decoder", type=str, default=None,
                        choices=["a", "b", "c", "d", "e", "f"],
                        help="Force a specific decoder hypothesis (a-f)")

    args = parser.parse_args()

    # Load frames
    if args.binary:
        frames = parse_binary_file(args.input, args.frame_size)
    else:
        with open(args.input, "r") as f:
            frames = parse_hex_dump(f.readlines())

    if not frames:
        print("ERROR: No frames parsed from input file.")
        sys.exit(1)

    # Classify all frames
    gps_frames = []
    control_frames = []
    session_frames = []
    end_frames = []
    unknown_frames = []

    for raw in frames:
        classified = classify_frame(raw)
        t = classified["type"]
        if t in ("gps_payload", "gps_payload_offset", "gps_data_cmd"):
            gps_frames.append(raw)
        elif t in ("gps_control", "gps_end"):
            control_frames.append(raw)
        elif t == "frame_index":
            session_frames.append(raw)
        elif t == "end_marker":
            end_frames.append(raw)
        else:
            unknown_frames.append(raw)

    # Summary
    print(f"\n{'=' * 70}")
    print(f"NADAL GPS DATA ANALYZER")
    print(f"{'=' * 70}")
    print(f"  Input:            {args.input}")
    print(f"  Total frames:     {len(frames)}")
    print(f"  GPS data frames:  {len(gps_frames)}")
    print(f"  Control frames:   {len(control_frames)}")
    print(f"  Session frames:   {len(session_frames)}")
    print(f"  End markers:      {len(end_frames)}")
    print(f"  Unknown/other:    {len(unknown_frames)}")

    if gps_frames:
        sizes = [len(f) for f in gps_frames]
        print(f"  GPS frame sizes:  min={min(sizes)}, max={max(sizes)}, "
              f"mode={max(set(sizes), key=sizes.count)}")

    # Frame inspection
    if args.inspect > 0:
        inspect_frames(frames, args.inspect)

    # Probe mode
    if args.probe or args.decoder is None:
        print(f"\n{'=' * 70}")
        print(f"DECODER HYPOTHESIS TESTING")
        print(f"{'=' * 70}")

        probe_results = probe_all_decoders(
            gps_frames, args.ref_lat, args.ref_lon
        )

        best_name = None
        best_score = -1

        for name, result in sorted(probe_results.items(),
                                    key=lambda x: x[1]["score"], reverse=True):
            print(f"\n  {name}:")
            print(f"    Decoded:     {result['total_frames']} frames")
            print(f"    Valid coords: {result['valid_coords']}")
            if args.ref_lat is not None:
                print(f"    Near ref:    {result['near_reference']}")
            print(f"    Seq timestamps: {result['sequential_timestamps']}")
            print(f"    Seq counters:   {result['sequential_sequences']}")
            print(f"    SCORE:       {result['score']}/100")

            if result["score"] > best_score:
                best_score = result["score"]
                best_name = name

            # Show first few decoded points
            valid_pts = [p for p in result["points"] if p.is_valid]
            if valid_pts:
                print(f"    First 3 valid points:")
                for pt in valid_pts[:3]:
                    ts = pt.timestamp_dt.strftime("%H:%M:%S") if pt.timestamp_dt else "?"
                    print(f"      seq={pt.sequence:5d}  {ts}  "
                          f"({pt.latitude:.6f}, {pt.longitude:.6f})  "
                          f"alt={pt.altitude:.0f}m  spd={pt.speed:.1f}m/s  "
                          f"sat={pt.satellites}")

        if best_name:
            print(f"\n  >>> BEST FIT: {best_name} (score: {best_score}/100)")

        if args.probe and not args.decoder:
            return

    # Decode with selected decoder
    decoder_map = {
        "a": decode_hypothesis_a,
        "b": decode_hypothesis_b,
        "c": decode_hypothesis_c,
        "d": decode_hypothesis_d,
        "e": decode_hypothesis_e,
        "f": decode_hypothesis_f,
    }

    if args.decoder:
        decoder_fn = decoder_map[args.decoder]
        decoder_name = args.decoder.upper()
    else:
        # Use best from probe
        probe_results = probe_all_decoders(gps_frames, args.ref_lat, args.ref_lon)
        best = max(probe_results.items(), key=lambda x: x[1]["score"])
        decoder_name = best[0]
        decoder_fn = dict(DECODERS)[decoder_name]

    print(f"\n{'=' * 70}")
    print(f"SESSION ANALYSIS (decoder: {decoder_name})")
    print(f"{'=' * 70}")

    session = analyze_session(frames, decoder_fn)

    valid = [p for p in session.points if p.is_valid]
    print(f"  Total GPS frames:  {len(session.raw_frames)}")
    print(f"  Decoded points:    {len(session.points)}")
    print(f"  Valid coordinates:  {len(valid)}")
    print(f"  End markers:       {len(session.end_markers)}")
    print(f"  Control frames:    {len(session.control_frames)}")

    if valid:
        print(f"\n  Track summary:")
        print(f"    Distance:      {session.total_distance_m:.0f} m "
              f"({session.total_distance_m / 1000:.2f} km)")
        print(f"    Duration:      {session.duration_s:.0f} s "
              f"({session.duration_s / 60:.1f} min)")
        print(f"    Avg speed:     {session.avg_speed_kmh:.1f} km/h")

        lats = [p.latitude for p in valid]
        lons = [p.longitude for p in valid]
        print(f"    Lat range:     {min(lats):.6f} to {max(lats):.6f}")
        print(f"    Lon range:     {min(lons):.6f} to {max(lons):.6f}")

        if valid[0].timestamp_dt:
            print(f"    Start time:    {valid[0].timestamp_dt}")
            print(f"    End time:      {valid[-1].timestamp_dt}")

        sats = [p.satellites for p in valid if p.satellites > 0]
        if sats:
            print(f"    Satellites:    avg={sum(sats) / len(sats):.1f}, "
                  f"min={min(sats)}, max={max(sats)}")

    # Output files
    if args.gpx:
        write_gpx(session, args.gpx)
    if args.csv:
        write_csv(session, args.csv)
    if args.json:
        write_json(session, args.json)

    if not any([args.gpx, args.csv, args.json]) and valid:
        print(f"\n  Tip: Use --gpx track.gpx to export for Google Earth/Maps")
        print(f"       Use --csv track.csv for spreadsheet analysis")
        print(f"       Use --json track.json for full session dump")


if __name__ == "__main__":
    main()
