# Dernière session dev — état du projet (mémoire courte)

*Document volatil : à mettre à jour après les prochains jalons produit / tech.*

## Ce qui fonctionne

- **Ouverture followers visuelle** : chemin opérationnel dans les runs récents.
- **Row mapping** : géométrie / candidats cohérents pour la sélection de ligne.
- **Ouverture profil candidat** : le candidat peut être ouvert depuis le flow visual.
- **Follow exact / harvester** : **partiellement** stable — reste sensible aux variations UI / timing.

## Garde-fous Contact / Follow

- **Contact** ne doit **plus** être tapé comme **Follow** (exclusions et scoring dans le Follow Action Engine).
- **Contact** reste une surface **à préserver** pour un **futur usage** type scraper email / contact Instagram (`future_use` / tagging métier selon implémentation).

## Dernier problème critique

- **Dérive post-follow** et **retour CT instable** : après follow / request OK (y compris privé + mute skipped), le return CT pouvait entraîner des surfaces **dangereuses** (posts/stories, composer commentaire, message).
- Impact : risque pour le compte, sessions arrêtées manuellement, logs incohérents avec l’UI réelle si la désynchronisation n’était pas corrigée.

## Priorité actuelle

- **Post-follow drift safety guard** : détection de surfaces à risque, **un seul** back contrôlé si safe, **budget temps** par round, **abort** propre sans navigation exploratoire, logs explicites, traitement **partial success** côté runner quand le follow est validé mais le CT return échoue / abort.

## Fichiers de référence (lecture)

- Détails navigation / vision / follow / post-follow / recovery : voir les autres fichiers sous `docs/` et la carte dans `AGENTS.md`.
