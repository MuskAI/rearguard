import os


# Production rejects missing session secrets. Tests opt into an isolated,
# process-local secret unless a case explicitly supplies one.
os.environ.setdefault("REALGUARD_ALLOW_EPHEMERAL_SECRET", "1")
