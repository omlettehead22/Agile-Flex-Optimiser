"""Generate the VAPID key pair for Web Push. Run once; store the values as environment variables."""
import base64

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


if __name__ == "__main__":
    v = Vapid()
    v.generate_keys()
    public = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    private = v.private_key.private_numbers().private_value.to_bytes(32, "big")
    print(f"VAPID_PUBLIC_KEY={b64(public)}")
    print(f"VAPID_PRIVATE_KEY={b64(private)}")
    print("VAPID_SUBJECT=mailto:you@example.com   # change to your email")
