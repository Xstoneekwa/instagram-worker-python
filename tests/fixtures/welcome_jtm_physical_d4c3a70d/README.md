# Physical JTM Welcome replay fixtures

These fixtures are immutable evidence for the physical chain observed on
2026-07-18 with Tracker on Samsung A16-01:

`Followers -> jtm.signature profile -> jtm.signature DM thread`

The Followers and profile artifacts were captured read-only after Liam placed
the device on each surface. The thread artifacts are the exact pre-cleanup
artifacts from run `d4c3a70d-460a-483e-995a-8beecb786430` at baseline
`c7c1e1f06660d9f25c6fc5cdeb6f6d73e1be5c66`.
The thread XML is gzip-compressed without timestamp metadata so its original
CRLF bytes remain exact; the manifest records both compressed and uncompressed
SHA-256 values.

The attached-capture inventory is intentionally strict: three supplied images
show the Followers surface or `pepito_bravo_`, not JTM. They are classified in
`provenance.json` and are not used as substitutes for JTM XML evidence.

No fixture contains a typed draft or a sent message. `provenance.json` records
the capture times, source run, app instance, Instagram version, and SHA-256 for
each file.
