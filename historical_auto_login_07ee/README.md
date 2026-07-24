# Historical Auto Login 07ee

This directory is an immutable source snapshot for the historical Auto Login
engine accepted at commit:

`07ee49bba28c316e61b4fb377fe406bbd742ed2a`

Reference tree:

`3a9374912a01ebdfb705253cde11354afdc3c07e`

The 34 Python source files and 10 historical tests are byte-for-byte copies of
their 07ee Git blobs. No import adaptations were required. Runtime entry point:

`instagram_login_provisioner_cli.py`

The current Worker reaches this entry point only through the external adapter
`historical_auto_login_07ee_adapter.py`. Do not modify the historical Python
files in place; regenerate the snapshot from the accepted commit instead.

Historical test baseline: 420 tests, 417 passing, with the three intrinsic 07ee
anomalies retained and documented by the release report rather than changed in
this snapshot.
