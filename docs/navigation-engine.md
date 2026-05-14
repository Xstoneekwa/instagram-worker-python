# Navigation Engine

## Rôle

Le **Navigation Engine** fournit une **couche de décision** sur l’écran Instagram courant : il agrège des signaux (XML / accessibilité, indices visuels, contexte métier) et expose un **état de navigation** exploitable par le runner et les modules de navigation (`instagram_navigation`, flows followers, post-follow, etc.).

Il sert de **filet de cohérence** entre la rapidité du XML et la fiabilité requise pour les actions critiques (profil, liste followers, retour CT).

## Logique : XML / accessibilité + vision + machine d’états

1. **XML / accessibilité**  
   Signal rapide : hiérarchie UI, `resourceId`, textes, présence de listes / titres. Utile pour les transitions fréquentes et l’extraction structurée.

2. **Vision (couche associée)**  
   Utilisée en **complément** ou **fallback** lorsque le XML est **stale**, incomplet, ou trompeur (voir [vision-layer.md](vision-layer.md)).

3. **Machine d’états (NavigationEngineState)**  
   États grossiers (`UNKNOWN`, `PROFILE`, `FOLLOWERS_LIST`, `CANDIDATE_PROFILE`, `MUTE_SHEET`, `SEARCH`, etc.) avec **confiance** et **raison** textuelle. La décision “où suis-je ?” pour une action donnée doit **s’aligner** sur cet état quand l’action est critique.

## `observe_instagram_state`

Fonction centrale d’**observation** : prend le device, le package attendu, un **dernier état connu** optionnel et un **contexte** (phase, `det`, flags du type `disable_followers_visual_fallback`, etc.).

Elle retourne typiquement :

- un **état** (`state`) ;
- une **confiance** (`confidence`) ;
- des champs d’aide au debug (`reason`, `xml_guess`, etc.).

Les appelants doivent **journaliser** ces champs sur les chemins sensibles pour corréler comportement UI / décision moteur.

## Screen fingerprint

Les **fingerprints d’écran** résument des signaux stables (titre action bar, indices liste, état follow header, etc.) pour **discriminer** des surfaces proches sans confondre deux flows.

Ils participent à l’**anti false-positive** : deux écrans peuvent partager du XML similaire mais un fingerprint ou un état nav différent impose une branche différente.

## Anti false-positive

Principes alignés avec le moteur :

- **Ne jamais** décider d’une action irréversible sur un **seul** widget XML isolé.
- Croiser **détection liste / profil**, **vérification compte CT** quand applicable, et **état** issu de `observe_instagram_state`.
- Traiter `UNKNOWN` avec faible confiance comme **signal d’incertitude**, pas comme feu vert implicite.

## Règle d’or

**Ne jamais agir sur un écran non confirmé** pour une action critique (follow, ouverture DM, navigation profil→followers, retour CT post-follow, etc.) : au minimum **état nav cohérent** + **signaux corroborants** (détection écran, fingerprint, ou vision selon le flow documenté).
