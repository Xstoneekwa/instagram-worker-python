-- Notification Router V2: challenge routing requires explicit challenge evidence.
-- Generic identity verification failures remain truthful auto-login failures.

create or replace function public.notification_router_auto_login_failure_v2()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  combined_reason text := lower(coalesce(new.reason, '') || ' ' || coalesce(new.failure_reason, '') || ' ' || coalesce(new.incident_type, ''));
  explicit_challenge boolean := (
    lower(coalesce(new.incident_type, '')) = 'auto_login_verification_required'
    or combined_reason ~ '(^|[^a-z])(challenge|checkpoint)([^a-z]|$)'
  );
  confirmed_mismatch boolean := (
    lower(coalesce(new.incident_type, '')) in ('active_instagram_account_mismatch', 'auto_login_identity_mismatch')
    or combined_reason ~ '(^|[^a-z])(mismatch|wrong_account)([^a-z]|$)'
  );
  routed_type text;
  business_problem text;
  business_action text;
begin
  if new.status not in ('open','acknowledged') or not (
    lower(new.incident_type) like '%login%'
    or combined_reason ~ '(password|challenge|identity|checkpoint|device|provision|verification|mismatch|wrong_account|what_happened|connectivity|loading)'
  ) then return new; end if;

  if combined_reason ~ '(password|credential.*reject)' then
    routed_type := 'auto_login.wrong_password';
    business_problem := 'Le mot de passe Instagram a été refusé.';
    business_action := 'Demander au client de mettre à jour son mot de passe.';
  elsif explicit_challenge then
    routed_type := 'auto_login.challenge';
    business_problem := 'Instagram demande une vérification supplémentaire.';
    business_action := 'Une intervention est nécessaire avant de poursuivre.';
  elsif confirmed_mismatch then
    routed_type := 'auto_login.identity_mismatch';
    business_problem := 'Le compte Instagram ouvert ne correspond pas au compte attendu.';
    business_action := 'Vérifier l’identité du compte avant de poursuivre.';
  elsif combined_reason ~ '(device|app.*unavailable)' then
    routed_type := 'auto_login.device_unavailable';
    business_problem := 'Le téléphone ou l’application Instagram n’est pas disponible.';
    business_action := 'Vérifier la disponibilité du téléphone dans BotApp.';
  elsif combined_reason ~ '(login_required|logged_out|unexpected_login_surface)' then
    routed_type := 'auto_login.failure';
    business_problem := 'Instagram demande une nouvelle connexion.';
    business_action := 'Reconnecter le compte Instagram avant de poursuivre.';
  elsif combined_reason ~ '(identity|verification|what_happened|connectivity|loading)' then
    routed_type := 'auto_login.failure';
    business_problem := 'L’identité Instagram n’a pas pu être vérifiée.';
    business_action := 'Consulter BotApp et vérifier l’état du compte avant de poursuivre.';
  else
    routed_type := 'auto_login.failure';
    business_problem := 'La connexion Instagram n’a pas pu être terminée.';
    business_action := 'Consulter BotApp pour poursuivre la vérification.';
  end if;

  perform public.emit_notification_business_event_v2(
    'auto_login.failure:' || new.id::text || ':' || new.lifecycle_version::text,
    'auto_login', case when new.metadata->>'environment' = 'test' then 'test' else 'live' end, routed_type,
    new.account_id, new.client_id, null,
    jsonb_build_object(
      'username', coalesce('@' || trim(leading '@' from new.account_username), '@non-renseigné'),
      'summary', business_problem, 'action', business_action
    ),
    jsonb_build_object('incident_id', new.id, 'incident_type', new.incident_type, 'reason', new.reason, 'run_id', new.run_id),
    coalesce(new.last_seen_at, new.created_at)
  );
  return new;
end;
$$;

revoke all on function public.notification_router_auto_login_failure_v2() from public, anon, authenticated;
grant execute on function public.notification_router_auto_login_failure_v2() to service_role;

create or replace function public.notification_router_general_incident_v2()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  combined_reason text := lower(coalesce(new.reason, '') || ' ' || coalesce(new.failure_reason, '') || ' ' || coalesce(new.incident_type, ''));
begin
  -- Auto Login owns each login/identity outcome below. Suppress the generic
  -- path for the same lifecycle version so one incident emits one message.
  if lower(new.incident_type) like '%login%'
    or combined_reason ~ '(password|challenge|identity|checkpoint|device.*login|provision|verification|mismatch|wrong_account|what_happened|connectivity|loading)'
  then return new; end if;

  if new.status in ('resolved','ignored') then
    perform public.emit_notification_business_event_v2(
      'incident.resolved:' || new.id::text || ':' || new.lifecycle_version::text,
      'incident', case when new.metadata->>'environment' = 'test' then 'test' else 'live' end,
      'incident.resolved', new.account_id, new.client_id, null,
      jsonb_build_object(
        'username', coalesce('@' || trim(leading '@' from new.account_username), '@non-renseigné'),
        'summary', 'La situation signalée a été traitée.'
      ),
      jsonb_build_object('incident_id', new.id, 'incident_type', new.incident_type, 'resolution_reason', new.resolution_reason),
      coalesce(new.resolved_at, new.last_seen_at, now())
    );
    return new;
  end if;
  if new.status not in ('open','acknowledged') then return new; end if;

  perform public.emit_notification_business_event_v2(
    'incident.opened:' || new.id::text || ':' || new.lifecycle_version::text,
    'incident', coalesce(case when new.metadata->>'environment' = 'test' then 'test' else 'live' end, 'live'),
    'incident.opened', new.account_id, new.client_id, null,
    jsonb_build_object(
      'username', coalesce('@' || trim(leading '@' from new.account_username), '@non-renseigné'),
      'summary', coalesce(new.safe_client_message, 'Une vérification opérationnelle est nécessaire.'),
      'action', coalesce(new.action_required, 'Consulter BotApp.')
    ),
    jsonb_build_object('incident_id', new.id, 'incident_type', new.incident_type, 'reason', new.reason),
    coalesce(new.last_seen_at, new.created_at)
  );
  return new;
end;
$$;

revoke all on function public.notification_router_general_incident_v2() from public, anon, authenticated;
grant execute on function public.notification_router_general_incident_v2() to service_role;
