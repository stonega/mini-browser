"""Synthetic signed CRX3 packages; never copy a user's installed extensions."""

import hashlib
import io
import json
import struct
import zipfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa


def varint(value):
    encoded = bytearray()
    while value > 127:
        encoded.append((value & 127) | 128)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def field(number, value):
    return varint(number << 3 | 2) + varint(len(value)) + value


def signed_package(key=None, manifest=None, files=()):
    key = key or rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    identity = hashlib.sha256(public).digest()[:16]
    identifier = identity.hex().translate(str.maketrans("0123456789abcdef", "abcdefghijklmnop"))
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                manifest or {"manifest_version": 3, "name": "Market fixture", "version": "1.0"}
            ),
        )
        for name, data in files:
            archive.writestr(name, data)
    payload = content.getvalue()
    signed = field(1, identity)
    message = b"CRX3 SignedData\0" + struct.pack("<I", len(signed)) + signed + payload
    if isinstance(key, rsa.RSAPrivateKey):
        signature = key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        algorithm = 2
    else:
        signature = key.sign(message, ec.ECDSA(hashes.SHA256()))
        algorithm = 3
    header = field(algorithm, field(1, public) + field(2, signature)) + field(10000, signed)
    return identifier, b"Cr24" + struct.pack("<II", 3, len(header)) + header + payload
