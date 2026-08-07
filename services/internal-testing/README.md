# Internal testing data service

This loopback-only service runs the internal evaluation data plane on the
`10.1.20.66` algorithm server. The public web server continues to enforce the
administrator session, CSRF token, role permissions, ownership checks and audit
logging, then forwards `/api/admin/testing/*` through an authenticated reverse
SSH tunnel.

Production data is stored under
`/mnt/sda1/ymk/realguard-internal-testing`. Do not place the data root on the
66 server system disk.

Required environment variables on 66:

```text
REALGUARD_INTERNAL_TESTING_TOKEN=<shared random token>
REALGUARD_INTERNAL_TEST_ROOT=/mnt/sda1/ymk/realguard-internal-testing
REALGUARD_INTERNAL_TEST_DB=/mnt/sda1/ymk/realguard-internal-testing/internal-testing.sqlite3
REALGUARD_INTERNAL_TEST_STORAGE_HOST=10.1.20.66
REALGUARD_INTERNAL_TEST_MODEL_URL_MAP={"http://127.0.0.1:15000":"http://127.0.0.1:5000","http://127.0.0.1:15002":"http://127.0.0.1:5071","http://127.0.0.1:15001":"http://127.0.0.1:15001","http://127.0.0.1:8848":"http://127.0.0.1:18848"}
```

The public backend uses the same token and:

```text
REALGUARD_INTERNAL_TESTING_REMOTE_URL=http://127.0.0.1:15072
REALGUARD_INTERNAL_TESTING_REMOTE_REQUIRED=1
```

`REALGUARD_INTERNAL_TESTING_REMOTE_REQUIRED=1` is a fail-closed production
guard: if the tunnel or its URL is missing, the public server rejects internal
evaluation writes instead of creating a cloud-local dataset.

The public backend service also mounts
`/opt/realguard-data/internal-testing` as inaccessible. This is a second,
operating-system-level guard: even an application regression cannot write
internal evaluation images to the cloud host. Nginx request buffering remains
disabled for the dataset and chunk upload routes, so request bodies are streamed
through the web process to server 66.

The admin page reports the writable capacity of the server 66 data volume.
There is no application-level dataset size or image-count limit, but imports
still require enough physical space on that volume while preserving the
configured free-space reserve.
