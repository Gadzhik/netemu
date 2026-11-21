"""MAC and IPv4 address helpers."""
from __future__ import annotations

import ipaddress
import itertools
import random

IPv4Address = ipaddress.IPv4Address
IPv4Network = ipaddress.IPv4Network
IPv4Interface = ipaddress.IPv4Interface


class MacAddress:
    """48-bit MAC address, printed in Cisco dotted notation (0001.4321.ABCD)."""

    __slots__ = ("value",)

    def __init__(self, value: int):
        self.value = value & 0xFFFFFFFFFFFF

    @classmethod
    def parse(cls, text: str) -> "MacAddress":
        digits = "".join(ch for ch in text if ch.lower() in "0123456789abcdef")
        if len(digits) != 12:
            raise ValueError(f"invalid MAC address: {text!r}")
        return cls(int(digits, 16))

    @property
    def is_broadcast(self) -> bool:
        return self.value == 0xFFFFFFFFFFFF

    @property
    def is_multicast(self) -> bool:
        return bool((self.value >> 40) & 1)

    def cisco(self) -> str:
        h = f"{self.value:012x}"
        return f"{h[0:4]}.{h[4:8]}.{h[8:12]}"

    def windows(self) -> str:
        h = f"{self.value:012X}"
        return "-".join(h[i:i + 2] for i in range(0, 12, 2))

    def __str__(self) -> str:
        return self.cisco().upper()

    def __repr__(self) -> str:
        return f"MacAddress({self})"

    def __eq__(self, other) -> bool:
        return isinstance(other, MacAddress) and other.value == self.value

    def __hash__(self) -> int:
        return hash(self.value)


BROADCAST_MAC = MacAddress(0xFFFFFFFFFFFF)

# Vendor-ish OUIs similar to those seen in Packet Tracer.
_OUIS = [0x0001_42, 0x0001_63, 0x0002_16, 0x0004_9A, 0x0005_5E, 0x000A_41,
         0x000C_85, 0x0030_F2, 0x0050_0F, 0x0060_2F, 0x0060_5C, 0x00D0_97]
_counter = itertools.count(1)
_rng = random.Random(0x5EED)


def new_mac() -> MacAddress:
    """Generate a unique unicast MAC address."""
    n = next(_counter)
    oui = _OUIS[_rng.randrange(len(_OUIS))]
    nic = ((_rng.randrange(0x100) << 16) | (n & 0xFFFF)) & 0xFFFFFF
    return MacAddress((oui << 24) | nic)


def parse_ip(text: str) -> IPv4Address:
    return IPv4Address(text.strip())


def is_valid_ip(text: str) -> bool:
    try:
        IPv4Address(text)
        return True
    except ValueError:
        return False


def is_valid_mask(text: str) -> bool:
    try:
        mask = int(IPv4Address(text))
    except ValueError:
        return False
    inverted = (~mask) & 0xFFFFFFFF
    return (inverted & (inverted + 1)) == 0


def mask_to_prefix(mask: str) -> int:
    if not is_valid_mask(mask):
        raise ValueError(f"invalid subnet mask: {mask}")
    return bin(int(IPv4Address(mask))).count("1")


def prefix_to_mask(prefix: int) -> str:
    return str(IPv4Network(f"0.0.0.0/{prefix}").netmask)


def make_interface(ip: str, mask: str) -> IPv4Interface:
    return IPv4Interface(f"{ip}/{mask_to_prefix(mask)}")


def default_mask_for(ip: str) -> str:
    """Classful default mask (like Packet Tracer fills in automatically)."""
    first = int(ip.split(".")[0])
    if first < 128:
        return "255.0.0.0"
    if first < 192:
        return "255.255.0.0"
    return "255.255.255.0"
