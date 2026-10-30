"""Pulse-Eight MatrixOS constants."""

from homeassistant.const import Platform

DOMAIN = "pulse_eight"
PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.BUTTON,
    Platform.TEXT,
    Platform.MEDIA_PLAYER,
    Platform.REMOTE,
    Platform.NUMBER,
    Platform.UPDATE,
]
UDP_PORT = 8811
DEFAULT_OPTIONS = {
    "udp_port": UDP_PORT,
    "stale_seconds": 180,
    "cloud_enabled": False,
    "source_filter": False,
    "trusted_sources": "",
    "cloud_port": 18989,
    "monitoring_port": 18080,
    "raw_events": False,
    "firmware_check_enabled": True,
    "firmware_hardware": "",
}
CRASH_DIR = "pulse_eight_crashes"
CRASH_KEEP = 50
EDID_PROFILES = {
    11: "Dynamic",
    0: "1080p stereo",
    4: "720p stereo",
    3: "1080p Dolby 5.1",
    5: "1080p 7.1",
    2: "Ultra HD 4K",
    6: "Ultra HD 4K 7.1",
    7: "HDR10 Dolby 5.1",
    8: "HDR10 7.1",
}
CEC_KEYS = {
    "up": 0x01,
    "down": 0x02,
    "left": 0x03,
    "right": 0x04,
    "select": 0x00,
    "home": 0x09,
    "menu": 0x0A,
    "back": 0x0D,
    "volume_up": 0x41,
    "volume_down": 0x42,
    "mute": 0x43,
    "play": 0x44,
    "stop": 0x45,
    "pause": 0x46,
    "record": 0x47,
    "rewind": 0x48,
    "fast_forward": 0x49,
    "next": 0x4B,
    "previous": 0x4C,
}
