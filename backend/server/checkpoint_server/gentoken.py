"""Generate a strong workspace token: python -m checkpoint_server.gentoken"""

import hashlib
import secrets

if __name__ == "__main__":
    t = secrets.token_urlsafe(40)
    print("CHECKPOINT_SERVER_TOKEN=" + t)
    print("# or store only the hash on the server:")
    print("CHECKPOINT_SERVER_TOKEN_SHA256=" + hashlib.sha256(t.encode()).hexdigest())
