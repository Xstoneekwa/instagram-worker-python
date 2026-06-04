-- LV-Web-2A: private storage bucket for live view screenshot frames (service_role only).

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'live-view-frames',
  'live-view-frames',
  false,
  5242880,
  array['image/png']::text[]
)
on conflict (id) do update
set
  public = excluded.public,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists live_view_frames_service_role_all on storage.objects;
create policy live_view_frames_service_role_all
  on storage.objects
  for all
  to service_role
  using (bucket_id = 'live-view-frames')
  with check (bucket_id = 'live-view-frames');
