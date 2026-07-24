# Historical Auto Login 07ee

This directory is derived from the immutable source snapshot for the historical
Auto Login engine accepted at commit:

`07ee49bba28c316e61b4fb377fe406bbd742ed2a`

Reference tree:

`3a9374912a01ebdfb705253cde11354afdc3c07e`

The original 34 Python source files and 10 historical tests were imported as
byte-for-byte copies of their 07ee Git blobs. No import adaptations were
required. Runtime entry point:

`instagram_login_provisioner_cli.py`

The current Worker reaches this entry point only through the external adapter
`historical_auto_login_07ee_adapter.py`. The reference commit and checksums
remain the immutable origin proof. The isolated runtime contains one certified
delta on top of that origin: after the first `connected_home`, the existing
post-submit observer keeps a short bounded stabilization window so the existing
post-login popup handlers can process a late prompt. No selector, popup handler,
login retry, credential input, logout path, or other screen route is changed.

Historical test baseline: 420 tests, 417 passing, with the three intrinsic 07ee
anomalies retained. The bounded stabilization delta adds six tests; the resulting
suite is 426 tests, 423 passing, with the same three intrinsic anomalies.
