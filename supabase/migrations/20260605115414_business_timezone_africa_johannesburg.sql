-- Business schedule timezone: define standard phone-farm slots in Africa/Johannesburg local time.
--
-- Existing assignments keep their stored UTC starts_at / ends_at. Updating the
-- device timezone changes how future slots are generated and how current UTC
-- windows are presented to operators.

alter table public.phone_devices
  alter column timezone set default 'Africa/Johannesburg';

alter table public.phone_rest_windows
  alter column timezone set default 'Africa/Johannesburg';

update public.phone_devices
set timezone = 'Africa/Johannesburg'
where timezone is null
   or trim(timezone) = ''
   or timezone = 'UTC';

update public.phone_rest_windows
set timezone = 'Africa/Johannesburg'
where timezone is null
   or trim(timezone) = ''
   or timezone = 'UTC';

comment on column public.phone_devices.timezone is
  'IANA business timezone used to interpret assignment slot windows for this phone/device. Default: Africa/Johannesburg.';

comment on column public.phone_rest_windows.timezone is
  'IANA business timezone used to interpret local blackout/rest windows. Default: Africa/Johannesburg.';
