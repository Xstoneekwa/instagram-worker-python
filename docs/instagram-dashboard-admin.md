# Instagram Dashboard Admin

Admin-only dashboard notes for the Phone Farm Instagram surfaces.

For the project-wide state, see `docs/project-knowledge-base.md`. For the full
settings audit matrix, see `docs/dashboard-settings-registry.md`.

## Schedule

Status: runtime-verified as of the 2026-06-01 Schedule checkpoint.

The Schedule tab is backed by:

- `GET /api/instagram-dashboard/settings/schedule`;
- `PATCH /api/instagram-dashboard/settings/schedule`;
- `public.list_available_assignment_slots(...)`;
- `public.assign_account_slot(...)`;
- `public.evaluate_account_schedule_gate(...)`;
- `public.phone_app_instances`;
- `public.account_assignments`.

Validated rules:

- `full_cycle` resolves to `slot_kind=full_cycle_6h`;
- `cinema_catchup` resolves to `full_cycle` even with the Outreach add-on;
- full-cycle accounts show four 6-hour slots and no outreach slots;
- app instance capacity is summarized from `phone_app_instances`;
- app instance rows are counted once, not duplicated via assignments;
- if an account already occupies an app instance, Schedule slot changes reuse
  that same instance before choosing a free clone;
- `no_app_instance_available` blocks assignment only when no compatible free or
  reusable app instance exists.

Validated emulator inventory, `Entry 2C Emulator Full Cycle`:

- primary app / index 0 / `com.instagram.android`: occupied by
  `cinema_catchup`;
- `Instagram 1` / index 1 / `com.instagram.androie`: available;
- `Instagram 2` / index 2 / `com.instagram.androif`: available;
- `Instagram 3` / index 3 / `com.instagram.androig`: available.

Current validated `cinema_catchup` Schedule state:

- current slot: `18:00 - 00:00`;
- assignment status: `reserved`;
- assignment source: `manual_dashboard`;
- `app_instance_id`: primary app index 0;
- `clone_id`: `null`;
- app instances summary: `3 free · 1 occupied · 0 blocked`.

## Devices Tab / Add Physical Phone

Status: V1 backend implemented in `supabase/functions/admin-dashboard/index.ts`
as internal action `add_physical_phone`. The product decision is to integrate
the workflow into the existing admin `Devices` tab, not to create a separate
top-level tab. Frontend UI remains V1.1 unless the dashboard repo wires this
action into the existing Devices surface.
Do not create account assignments, launch runs, connect accounts, touch
credentials, install APKs, or create Android clones as part of this V1 setup
flow.

Recent manual inventory checkpoint:

- `RFGL145VCKE` -> `Samsung A16-01`;
- `RFGL145LZHE` -> `Samsung A16-02`;
- both devices are `physical_phone`, `pool_type=full_cycle`, `max_clones=3`;
- both devices have four `phone_app_instances`:
  - index 0 / `primary_app` / `com.instagram.android`;
  - index 1 / `clone` / `com.instagram.androie`;
  - index 2 / `clone` / `com.instagram.androif`;
  - index 3 / `clone` / `com.instagram.androig`.

V1 backend/API:

- Edge Function: `supabase/functions/admin-dashboard/index.ts`;
- action: `add_physical_phone`;
- action read-only: `devices_overview`;
- auth: existing internal admin dashboard token, preferably sent with the
  Supabase Edge API-key header from server-side code only;
- writes only `phone_devices`, `phone_app_instances`, and best-effort
  `runtime_events` audit;
- does not read or write credentials, assignments, runs, request queues, package
  settings, or client dashboard state.

Devices Live Inventory V1:

- `devices_overview` returns `phone_devices`, `items` (same list for backwards
  compatibility), and `phone_inventory_summary`;
- source tables: `phone_devices`, `phone_app_instances`, and best-effort
  `device_heartbeats` when available;
- per phone fields include device id/name, `adb_serial`, kind/status, pool,
  max clones, host/hub labels, safe model/product/device metadata, timestamps,
  heartbeat status, app instance counts, standard package completeness, issues,
  and sanitized app instance rows;
- if no heartbeat is present, the response uses
  `heartbeat_status="unknown"` and issue `adb_status_unknown`;
- the Edge Function still does not read local ADB, does not verify installed
  packages, does not create Android clones, and does not start runtime actions.

Device Heartbeat Publisher V1:

- local CLI: `python3 device_heartbeat_publisher.py --include-battery --serial <SERIAL>`;
- reads local `adb devices -l` and optional `adb shell dumpsys battery` only;
- resolves `adb_serial` to `phone_devices.id`;
- writes only safe `device_heartbeats` rows through the existing ORF heartbeat
  path;
- makes `devices_overview` show fresh `heartbeat_status="online"` for devices
  whose ADB state is `device`;
- never starts Instagram, launches a runner/dispatcher, creates assignments, or
  touches credentials.

Current V1 status:

- yes: Add Physical Phone V1 is complete for DB registration and admin
  inventory;
- no: it is not yet a fully automated physical phone onboarding flow.

What an operator can do today:

1. Prepare the phone physically: Developer Options on, USB debugging on, ADB
   authorization accepted, and app-store/update locks applied where needed.
2. Install the validated Instagram build and standard clone packages if this
   phone will use them:
   `com.instagram.android`, `com.instagram.androie`,
   `com.instagram.androif`, `com.instagram.androig`.
3. Open the admin Devices tab, use `Add phone`, and fill `display_name`,
   `adb_serial`, model/product/device, pool, `max_clones`, hub metadata, and host
   label.
4. Save. The backend creates or updates `phone_devices` and creates the four
   standard `phone_app_instances` idempotently.
5. Run the local heartbeat publisher for that serial.
6. Verify the Devices live inventory: phone visible, expected app instances,
   heartbeat online/fresh, and issues empty or explicit.
7. Only after that, use separate assignment/schedule/run-control flows.

What Add phone does not do yet:

- no automatic ADB detection from the dashboard;
- no `adb devices` read from the Edge Function;
- no Instagram install;
- no real Android clone creation;
- no installed-package verification beyond stored metadata;
- no automatic heartbeat publisher launch;
- no complete schedule/capacity setup;
- no account assignment;
- no run, login, provisioning, credential, password, Vault, follow, DM, or
  unfollow action.

Planned Add Phone V1.1/V2 improvements:

- `Detect from ADB` and `Refresh ADB inventory`;
- auto-fill model/product/device from ADB;
- verify installed Android packages and mark missing/setup-required packages;
- heartbeat validation action;
- archive/delete placeholder phones;
- optional real clone creation if ever automated;
- richer schedule/capacity preparation by pool;
- an `Add + verify phone` action that still does not launch Instagram runs.

V1.1 Devices tab surface:

- add an `Add phone` button inside the existing admin `Devices` tab;
- open a modal/drawer or inline panel with the Add Physical Phone form;
- include a `Refresh ADB inventory` action for operator diagnostics;
- include a `Detect from ADB` action when the API/worker host can query local
  ADB safely. This is not implemented in V1 because the current Edge Function
  cannot inspect the local worker host's ADB devices;
- include a `Save phone` action that calls `admin-dashboard` action
  `add_physical_phone` and writes inventory only.

Operator form fields:

- readable phone name, for example `Samsung A16-03`;
- ADB serial, for example `RFGL...`;
- detected or manually entered model, for example `SM-A165F`;
- optional product and Android device values, for example `a16nsxx` / `a16`;
- pool: `full_cycle` or `outreach_only`;
- clone count, default `3`;
- optional hub label;
- optional hub port;
- optional host/Mac label, for example `dev-mac` or `prod-mac-hub-01`.

Backend validation required:

- require a non-empty `adb_serial`;
- treat an existing `phone_devices.adb_serial` as an idempotent update of the
  same device row;
- reject a true multi-row duplicate `adb_serial` as `duplicate_adb_serial`;
- if ADB is available, verify the serial is visible in `adb devices -l`;
- create or update only the matching `phone_devices` row with:
  `device_kind='physical_phone'`, display name, `adb_serial`,
  model/product/device metadata, pool, `max_clones`, `status='available'`,
  and ops-only hub/host metadata;
- create standard `phone_app_instances` idempotently for index 0..3;
- if an expected app instance already exists and is `occupied` or has
  `current_account_id`, block the save with `app_instance_occupied` instead of
  overwriting capacity state;
- if an existing index or package conflicts with the standard package map, block
  with an explicit conflict reason;
- never create `account_assignments`;
- never launch `ig_runs` or `account_run_requests`;
- never connect an Instagram account;
- never read, write, or display credentials.

Package and clone strategy:

- V1 does not create real Android clone packages. It only inventories packages
  already installed on the phone.
- Standard expected packages are:
  `com.instagram.android`, `com.instagram.androie`,
  `com.instagram.androif`, and `com.instagram.androig`.
- If ADB confirms a package is missing, the backend must not invent a false
  healthy state. Acceptable V1 behaviors are:
  - block `Save phone` with a clear setup message;
  - save `phone_devices` only and skip app instance creation;
  - or create affected instances as setup-required/blocked once the schema
    supports that state.
- Until a `setup_required` status exists in `phone_app_instances`, prefer either
  blocking full inventory creation or saving device-only with an explicit issue.
  Current V1 assumes manual package verification and sets
  `metadata.adb_package_verified=false`; Detect from ADB should close this gap
  in V1.1.

Inventory list should show:

- phone name;
- `adb_serial`;
- online/offline from latest ADB scan or heartbeat;
- pool and `max_clones`;
- detected packages;
- DB app instances;
- hub/port;
- last heartbeat;
- issues.

Devices tab save behavior:

- after successful save, refresh the Devices list;
- display `app_instances_created_count` and `app_instances_existing_count`;
- display warnings such as `hub_port_changed` and
  `audit_event_not_published`;
- display stable backend errors clearly:
  `duplicate_adb_serial`, `app_instance_occupied`,
  `app_instance_index_conflict`, and `app_instance_package_conflict`;
- never ask for credentials in this flow;
- never create assignments from this flow;
- never launch a run from this flow.

Warnings to surface:

- `adb_serial` exists in DB but is absent from ADB;
- ADB serial is visible but missing in DB;
- Android packages are present but `phone_app_instances` are missing;
- `phone_app_instances` exist but package is absent from Android;
- hub port changed since the last scan;
- duplicate serial;
- active assignment on an offline device.

Ports / hub rule:

- `adb_serial` is the stable runtime execution key.
- `hub_label`, `hub_port`, USB path and ADB `transport_id` are ops/inventory
  metadata only.
- The runner and dispatcher must use `adb_serial`, not USB port or
  `transport_id`, to route execution.
- Moving a phone to another USB port must not break assignment as long as the
  ADB serial remains unchanged.
- `transport_id` must never be stored or treated as a stable identity because it
  can change on reconnect.

Safe audit events to add later:

- `phone_added`;
- `phone_app_instances_created`;
- source `admin_dashboard`;
- actor/admin when available;
- no password, credential refs, tokens, service-role keys, raw logs, XML,
  screenshot paths, full device secrets, or client-visible hub/USB details.

## Account Lifecycle Actions

Status: admin UI wired as of frontend commit
`8bfab6f93b2ffe870c8fd74f6c45d3972f06fb78`.

The Client Accounts table uses a compact status/lifecycle action menu in the
Actions column:

- Pause account;
- Cancel account;
- Mark needs assistance;
- Reactivate account.

The old standalone `Needs assistance` action was removed. `Archived` is not
shown in this menu.

Backend source of truth:

- `ig_accounts.admin_lifecycle_status`.

Validated capacity rules:

- `paused` keeps assignment, slot and app instance;
- `needs_assistance` keeps assignment, slot and app instance;
- runtime stopped keeps assignment, slot and app instance;
- `cancelled` releases capacity only when safe;
- assignment release, archive and reassignment release capacity only when safe;
- `stopped` does not release capacity automatically.

Security notes:

- admin lifecycle writes are server-side only;
- no credentials, password, raw XML, screenshots, serials, UDIDs or tokens are
  exposed by these dashboard controls;
- no run should be launched from Schedule validation or lifecycle status edits.
