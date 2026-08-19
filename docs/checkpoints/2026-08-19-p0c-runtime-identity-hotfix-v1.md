# P0C Runtime Identity / Recovery / Lease V1

Date: 2026-08-19

## Cause

Le chemin protégé lisait `_CERTIFIED_RUNTIME_IDENTITY.full_sha` alors que le
contrat expose `worker_sha`. L'exception levée à la frontière de création de
l'intent Follow était ensuite absorbée comme erreur candidat locale. La boucle
continuait après des Likes sans autoriser de Follow. En parallèle, le child
renouvelait la device lock mais pas systématiquement la lease de sa request.

## Contrat corrigé

1. SHA issu du helper canonique de release.
2. Preflight identité + stockage durable avant tout effet UI candidat.
3. Exception typée globale propagée jusqu'à la terminalisation du run.
4. Aucun Auto Restart même release pour cette signature systémique.
5. Lease request/run/worker renouvelée pendant toute la vie du child.
6. Queue de récupération générique, account-scoped, target-scoped,
   candidate-scoped et indépendante du curseur CT.
7. Le backlog est traité avant CT Resume/nouveau scan, dans la limite du quota
   restant et par lots bornés.
8. Aucun retap Like en état liked/ambigu ; aucun faux reçu ou compteur.
9. Un état Following déjà présent terminalise sans crédit bot.

## État de livraison

Approval 1 autorise code, tests et documentation uniquement. Migration,
déploiement, switch runtime, run, tick et ADB restent interdits avant Approval
2 puis les gates de promotion.

## Certification Approval 1

- Tests ciblés identité, propagation globale, récupération, persistance,
  lease, Auto Restart et consumer : 132/132 PASS.
- Matrice historique Follow60 Mainline/V2 et contrôles Lock V3/V3.1 : 35/35
  PASS.
- Compilation des modules modifiés : PASS.
- `git diff --check` : PASS.
- Périmètre signé : PASS, aucun fichier métier hors autorisation.
- Migration préparée mais non appliquée.
- Backfill des 231 candidats affectés non exécuté : il reste conditionné à
  Approval 2 et aux gates de production.
- Aucun deploy, switch, restart, run, tick, ADB ou write DB effectué.
