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
```
