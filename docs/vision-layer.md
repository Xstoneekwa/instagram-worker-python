# Vision Layer

## Rôle

La **vision** complète l’XML quand celui-ci est **fragile** ou **stale** : screenshots, détection visuelle de rangées, confirmation de surfaces, et parcours **visual followers** lorsque la hiérarchie accessibilité ne suffit pas.

## Visual followers fallback

Lorsque le moteur followers repose sur le XML mais rencontre des **sorties stale** ou des transitions mal détectées, un **fallback visuel** peut :

- resynchroniser la compréhension de la liste ;
- ouvrir ou valider une surface “followers” par signaux visuels forts.

Ce chemin est **plus coûteux** que le XML seul ; il doit rester **borné** et **observable** (logs de phase, raisons d’activation).

## Visual row mapping

Le **row mapping** relie la géométrie des rangées (bandes Y, ancres screenshot) aux **candidats** (handles, indices de tap). Il permet des **multi tap points** : plusieurs coordonnées ou régions candidates pour un même row afin de limiter les échecs de tap sur chrome dynamique.

## Multi tap points

Pour une rangée donnée, plusieurs **points de tap** ou **régions** peuvent être scorées ; le moteur choisit un ordre de tentative documenté dans le code (scores, garde-fous). L’objectif est de **réduire les faux taps** (entre ligne, chrome, suggestions).

## Screenshot-based detection

Les captures supportent :

- la **classification** de surface (liste vs profil vs overlay) ;
- l’**alignement** avec le XML quand les deux sont disponibles ;
- le **debug** prod (empreintes visuelles dans les logs, pas seulement XML).

## Quand utiliser la vision

- XML **stale** ou **vide** après transition.
- **Faux positifs** récurrents sur la détection liste / profil.
- Flows explicitement **visual-first** (followers visuels, ouverture candidat depuis screenshot).
- Besoin de **confirmer** une transition avant action critique.

## Quand ne pas réutiliser une vision stale

- Après **back**, **navigation**, **keyboard**, ou **changement d’activité** : considérer la capture / le crop précédent comme **invalides** pour décision fine.
- Après **timeout** long ou **recovery** : **re-capturer** ou re-dumper avant de réappliquer des coordonnées de tap.
- Si les logs indiquent **dérive** (mauvais écran, état `UNKNOWN`) : **ne pas** enchaîner sur d’anciennes boîtes de détection sans nouvelle observation.

Règle pratique : **une action critique = une observation fraîche** (vision et/ou XML + état nav) lorsque le dernier signal date d’avant une transition non prouvée.
