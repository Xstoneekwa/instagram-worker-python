-- Operational retirement for phone_devices: preserve released assignment FK history.

alter table public.phone_devices
  add column if not exists retired_at timestamptz;

alter table public.phone_devices
  drop constraint if exists phone_devices_status_check;

alter table public.phone_devices
  add constraint phone_devices_status_check
  check (status in (
    'available',
    'reserved',
    'active',
    'maintenance',
    'offline',
    'unauthorized',
    'disabled',
    'retired'
  ));

comment on column public.phone_devices.retired_at is
  'Timestamp when the phone was retired from operational inventory. Released assignment history remains linked for audit.';

create index if not exists phone_devices_operational_status_idx
  on public.phone_devices (device_kind, status)
  where status <> 'retired';
