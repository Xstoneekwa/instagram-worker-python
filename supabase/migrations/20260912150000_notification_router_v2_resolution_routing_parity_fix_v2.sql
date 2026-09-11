-- Notification Router V2 forward fix.
-- Preserve production resolution routing while keeping expanded Auto Login
-- ownership for active incidents introduced by the explicit-challenge repair.

create or replace function public.notification_router_general_incident_v2()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  combined_reason text := lower(coalesce(new.reason, '') || ' ' || coalesce(new.failure_reason, '') || ' ' || coalesce(new.incident_type, ''));
  dashboard_url text := 'https://www.boostmybusinesses.com/instagram-dashboard/incidents?incident_id=' || new.id::text;
  production_auto_login_owned boolean := (
    lower(new.incident_type) like '%login%'
    or combined_reason ~ '(password|challenge|identity|checkpoint|device.*login|provision)'
  );
  corrected_auto_login_owned boolean := (
    production_auto_login_owned
    or combined_reason ~ '(verification|mismatch|wrong_account|what_happened|connectivity|loading)'
  );
begin
  -- Terminal routing remains byte-for-byte equivalent in outcome to the
  -- production router. Only categories already owned by Auto Login in
  -- production are suppressed here.
  if new.status in ('resolved','ignored') then
    if production_auto_login_owned then return new; end if;

    perform public.emit_notification_business_event_v2(
      'incident.resolved:' || new.id::text || ':' || new.lifecycle_version::text,
      'incident', case when new.metadata->>'environment' = 'test' then 'test' else 'live' end,
      'incident.resolved', new.account_id, new.client_id, null,
      jsonb_strip_nulls(jsonb_build_object(
        'username', coalesce('@' || trim(leading '@' from new.account_username), '@non-renseigné'),
        'summary', 'La situation signalée a été traitée.',
        'resolutionNote', nullif(left(trim(coalesce(new.resolution_note, '')), 500), ''),
        'resolutionReason', nullif(left(trim(coalesce(new.resolution_reason, '')), 160), ''),
        'resolvedAt', new.resolved_at,
        'operatorId', new.resolved_by,
        'dashboardUrl', dashboard_url
      )),
      jsonb_build_object('incident_id', new.id, 'incident_type', new.incident_type),
      coalesce(new.resolved_at, new.last_seen_at, now())
    );
    return new;
  end if;

  if new.status not in ('open','acknowledged') then return new; end if;

  -- For active incidents, the corrected Auto Login router owns the expanded
  -- identity/network set, preventing a duplicate generic notification.
  if corrected_auto_login_owned then return new; end if;

  perform public.emit_notification_business_event_v2(
    'incident.opened:' || new.id::text || ':' || new.lifecycle_version::text,
    'incident', case when new.metadata->>'environment' = 'test' then 'test' else 'live' end,
    'incident.opened', new.account_id, new.client_id, null,
    jsonb_build_object(
      'username', coalesce('@' || trim(leading '@' from new.account_username), '@non-renseigné'),
      'summary', coalesce(new.safe_client_message, 'Une vérification opérationnelle est nécessaire.'),
      'action', coalesce(new.action_required, 'Consulter BotApp.'),
      'dashboardUrl', dashboard_url
    ),
    jsonb_build_object('incident_id', new.id, 'incident_type', new.incident_type),
    coalesce(new.last_seen_at, new.created_at)
  );
  return new;
end;
$$;

revoke all on function public.notification_router_general_incident_v2()
  from public, anon, authenticated;
grant execute on function public.notification_router_general_incident_v2()
  to service_role;
