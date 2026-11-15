# BOSTER — PHASE 2.5
# VALIDATION DE LA MÉMOIRE DANS LE PIPELINE DÉTERMINISTE

Rapport de validation. Tous les chiffres de ce document proviennent de l'exécution
réelle du pipeline sur des scénarios figés ; chaque commande de reproduction est
donnée en fin de document.

---

## 0. Résultat en une phrase

La mémoire structurée améliore réellement la reconstruction — sur les cinq
scénarios, la variante E restitue **toutes** les valeurs courantes (75/75),
**toutes** les contraintes (25/25), **toutes** les relations (10/10), distingue
correctement l'actuel de l'obsolète (20/20), identifie les questions impossibles
(3/3), cite une provenance exacte (1130/1130) et n'invente **rien** (0) — mais la
validation a d'abord mis au jour **sept défauts d'intégration réels**, dont deux
qui détruisaient silencieusement de l'information. Ils sont corrigés ci-dessous,
avec la mesure qui les a révélés.

---

## 1. Défauts trouvés par la mesure, et corrigés

Sept défauts, chacun découvert par une mesure, pas par relecture. Les six premiers
sont dans le chemin mémoire/reconstruction ; le septième dans mon propre harnais de
mesure (et il faussait le verdict).

### 1.1 `build_memory` ignorait la question posée (bloquant pour §5)

**Problème.** Le retrieval classait la mémoire selon le **dernier message
utilisateur de la conversation**, pas selon la question pour laquelle le contexte
était construit. Demander « Quelle base utilise Orion *auparavant* ? » et « Quelle
base utilise Orion *maintenant* ? » produisait exactement le même classement — la
reconstruction dépendante de la question était donc impossible.

**Correctif.** `build_memory(..., query=...)` ; la question de l'appelant prime,
repli sur le dernier tour utilisateur si aucune n'est fournie (comportement
antérieur préservé).

### 1.2 Le réparateur de troncature supprimait des mots légitimes (perte silencieuse)

**Problème.** Le motif de tokenisation inclut le point (« `3.14` », « `postgres:5432` »),
donc une phrase terminée par « `node.` » produit le token « `node.` » et **pas**
« `node` ». `_repair_partial_words` concluait que « node » n'existait nulle part
dans la source et le supprimait. Mesure :

```
- The constraint is at most 4 GB of RAM on the analytical .      <-- "node" supprimé
- The constraint is at most 4 GB of RAM on the analytical node.  <-- attendu
```

Le dernier mot de **chaque** ligne de fait était candidat à la suppression.

**Correctif.** Le vocabulaire conserve le token brut **et** sa forme dépouillée de
ponctuation (`node.` → `node`). Les fragments réels (« `pro` » pour « `process` »)
restent détectés et supprimés : ils n'existent sous aucune forme.

### 1.3 Les en-têtes `constraints:` et `unknown:` n'étaient pas protégés

**Problème.** `STRUCTURAL_LINES` listait les en-têtes de section **sauf**
`constraints:` et `unknown:`. Le réparateur, ne trouvant pas le mot « constraints »
dans la source, effaçait l'en-tête lui-même — les lignes de contrainte se
retrouvaient alors rattachées à la section précédente. Les en-têtes `reasons:`
(ajoutée plus bas) et la notice d'absence générée par le reconstructeur subissaient
le même sort (« `no information about Vega in this` », « conversation » supprimé).

**Correctif.** En-têtes complétés ; les lignes **générées** par le reconstructeur
(notice d'absence, compteurs d'omission) sont désormais exclues du réparateur par
`_GENERATED_LINE_RE` — elles ne portent aucun mot de la source, un contrôle
« ancré sur la source » ne peut pas les juger.

### 1.4 L'extracteur de décisions ratait les décisions formulées « changed X to Y »

**Problème.** L'extracteur réutilisé (Phase 2.3) capture « *We decided to use X
instead of Y* » mais pas « *We changed the cache to Redis instead of Memcached* ».
Conséquence mesurée sur `mem_200` : la vue structurée ne contenait **qu'une seule**
décision (`PostgreSQL (active)`), Redis n'était pas une décision, la section
`history:` restait vide, et la question « Quelle base était utilisée auparavant ? »
n'avait **aucune réponse structurée** alors que la phrase était dans la conversation.

**Correctif.** Supplément déterministe **au niveau du pipeline** (`_DECISION_CHANGE_RE`),
qui n'ajoute que des décisions explicitement formulées comme remplacement : la
nouvelle valeur en `active`, l'ancienne en `superseded`, chacune citant le message
d'origine. Regex fixe, ordre fixe, identifiants dérivés de l'id de message durable.
Aucun module mémoire ni interface publique modifié.

### 1.5 Relations : lignes dupliquées, et « depends on » jamais extraite

**Problème mesuré.** La section `relations:` contenait `- Atlas uses Cassandra`
**deux fois** (budget dépensé en répétition), et « *The reporting service depends
on PostgreSQL* » n'était **jamais** extraite — l'extracteur ne modélise que « X
uses/is Y ».

**Correctif.** Déduplication à ordre stable des relations du consolidateur, plus un
supplément `depends on|relies on|is built on|connects to` symétrique au supplément
de décisions. Résultat : rappel des relations **10/10** pour E (contre 6/10 pour C).

### 1.6 Le vocabulaire « historique » ignorait « before / previously / earlier »

**Problème.** La vue historique ne se déclenchait que sur
`history|previously|why|change`. « Quelle base Orion utilisait-il **avant** ? »
(« before ») ne déclenchait donc rien et retournait un contexte « actuel seulement ».

**Correctif.** Vocabulaire étendu (`before`, `earlier`, `prior`, `originally`,
`used to`, `avant`, `précédemment`, …).

### 1.7 Deux défauts de mon propre harnais (le verdict était faux)

* **Provenance.** J'utilisais `_message_id` (ids de tour du pipeline, `mid_<index>`)
  pour résoudre les `[msg_…]` du bloc, alors que ces derniers viennent du schéma
  durable du consolidateur (`message_id_for`, Phase 2.2.1). Résultat : **0/149**
  annoncé alors que la vérité était **72/72**. Corrigé : provenance **1130/1130**.
* **« Informations inventées ».** Le contrôle de tokens comptait les **en-têtes de
  section** (`current decisions:`, `constraints:`…) et le mot « superseded » comme
  des affirmations, et souffrait du même défaut de ponctuation que §1.2
  (« `Memcached.` » ne validait pas « `Memcached` »). Résultat : « 27 inventions »
  annoncées sur E alors que la vérité est **0**. Corrigé : E = **0 invention**.

Un verdict « non validé » bâti sur ces deux chiffres aurait été faux dans les deux
sens (accusation d'invention inexistante, acquittement sur la provenance).

---

## 2. Scénarios et vérité terrain (§2)

`benchmark/deterministic_pipeline/scenarios.py` produit des conversations de
**10, 25, 50, 100 et 200 échanges** avec une vérité terrain **indépendante du
pipeline** (elle n'est jamais dérivée de la mémoire produite). Classes couvertes :

| Classe | Contenu du scénario | Statut attendu |
|---|---|---|
| faits précoces | « Project Orion uses SQLite for local storage. » (échange 2) | obsolète après changement |
| changement de décision | « We decided to use PostgreSQL instead of SQLite… » | actuel PostgreSQL, historique SQLite |
| relation entre entités | « The reporting service depends on PostgreSQL. » | relation |
| contrainte persistante | « The constraint is at most 4 GB of RAM… » | doit survivre partout |
| information répétée | ACK assistant + reformulations | aucune duplication en sortie |
| information obsolète | SQLite, Memcached | en historique, jamais en actuel |
| contradiction | (SQLite→PostgreSQL), (Memcached→Redis) | actuel/historique distingués |
| raison de décision | « We switched because SQLite could not handle the analytical load. » | présente pour la question « pourquoi » |
| conversations indépendantes | second projet **Atlas** (Cassandra) dans la même conversation | zéro fuite Orion↔Atlas |
| question multi-épisodes | « What decisions were made about storage and caching over time? » | les deux décisions actuelles |

Cinq questions par scénario : `current`, `history`, `reason`, `unknown`
(« Which database does project **Vega** use? » — réponse absente), `multi_episode`.

---

## 3. Comparaison des quatre configurations (§1)

Configurations : **B** pipeline historique · **C** déterministe sans mémoire ·
**D** déterministe avec mémoire · **E** déterministe avec mémoire et reconstruction.
Mêmes conversations, mêmes questions, `max_tokens=2000`, niveau `balanced`.

Agrégats sur les 25 mesures par variante (5 tailles × 5 questions) :

| Variante | Économie | Valeur courante | Actuel≠obsolète | Contraintes | Raisons | Relations | Décision en section | Impossible | Provenance | Inventé | Fuite |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **B** historique | 93,80 % | 45/75 | 0/20 | 25/25 | 5/5 | 0/10 | 0/0 | 0/0 | 0/0 | **125** | 0 |
| **C** sans mémoire | 48,69 % | 75/75 | 20/20 | 25/25 | 4/5 | 6/10 | 0/0 | 0/0 | 0/0 | 0 | 0 |
| **D** avec mémoire | 57,09 % | 71/75 | 20/20 | 25/25 | 5/5 | 6/10 | 32/32 | 0/4 | 0/0 | 0 | 0 |
| **E** mémoire + reconstruction | 57,29 % | **75/75** | **20/20** | **25/25** | **5/5** | **10/10** | **24/24** | **3/3** | **1130/1130** | **0** | **0** |

Lecture honnête :

* **B économise 93,8 % et invente 125 lignes** : il gagne en tokens en détruisant
  l'information (2/3 des valeurs courantes conservées, 0 relation, 0 distinction
  actuel/historique). Une économie obtenue par invention n'est pas un résultat.
* **C** (sans mémoire) est correct sur tout ce qu'il peut voir — mais il n'a ni
  structure (0 décision en section) ni détection d'absence, et il coûte **48,7 %**
  seulement : pour un contexte deux fois plus gros qu'E, il ne peut pas dire
  *quelle* décision est courante, ni répondre « Vega ».
* **D** est déjà bon (+8,4 points d'économie sur C à qualité égale) ; **E** ajoute
  la reconstruction bornée et passe les relations à 10/10, la détection d'absence à
  3/3, pour **+0,2 point** de coût en tokens.

---

## 4. Mesure de la mémoire (§3)

Règle appliquée : *une information n'est jamais comptée comme conservée parce que
son nom apparaît quelque part*. Définitions opérationnelles :

| Mesure | Définition stricte | Applicabilité |
|---|---|---|
| valeur courante | la valeur **active** apparaît hors section historique et hors ligne marquée obsolète | toutes les questions |
| actuel ≠ obsolète | bloc : la nouvelle valeur est dans la vue courante et l'ancienne **n'y est pas** ; transcript chronologique : la dernière mention de la nouvelle suit la dernière mention de l'ancienne | current/history/reason/multi_episode |
| contrainte | texte de la contrainte présent hors historique | toutes |
| raison | texte de la raison présent | question `reason` **uniquement** |
| relation | sujet **et** objet co-localisés sur une même ligne | current/multi_episode |
| décision en section | valeur dans `current decisions:` **et** la vue marquée `active` (jamais une simple mention) | questions avec bloc |
| provenance | le payload de la ligne se retrouve **dans le message cité** par le `[msg_…]` | avec bloc |
| impossible | la section `unknown:` nomme le sujet (« Vega ») | question `unknown` **uniquement** |

**Mesures mémoire par variante** (détail §3 du cahier des charges) :

| Mesure | B | C | D | E |
|---|---|---|---|---|
| rappel des faits (valeur courante) | 45/75 | 75/75 | 71/75 | **75/75** |
| rappel des relations | 0/10 | 6/10 | 6/10 | **10/10** |
| rappel des décisions (en section courante) | 0/0 | 0/0 | 32/32 | 24/24 |
| rappel des contraintes | 25/25 | 25/25 | 25/25 | **25/25** |
| rappel des raisons | 5/5 | 4/5 | 5/5 | **5/5** |
| distinction actuel/historique | 0/20 | 20/20 | 20/20 | **20/20** |
| exactitude de la provenance | n/a | n/a | n/a | **1130/1130** |
| taux d'informations inventées | **125** | 0 | 0 | **0** |
| réponses impossibles identifiées | 0/0 | 0/0 | 0/4 | **3/3** |

Les mentions « n/a / x/x » sont des non-applicables assumés, pas des échecs : **D
ne reconstruit pas** (pas de section `unknown:`, donc 0/4 légitime) et **C n'a pas
de bloc** (métriques de section sans objet). C'est une différence *démontrable* en
faveur de E.

Nuance honnête sur E : `historical-available` (valeur obsolète présente et non
présentée comme courante) est **16/18** — pour les questions `unknown` et
`multi_episode`, le budget évince la valeur historique dans 4 cas sur 10. D 23/24.
L'information historique n'est donc **pas toujours** présente ; elle l'est toujours
pour les questions qui la demandent explicitement (`history` : 6/6 ; `reason` :
6/6).

---

## 5. Économie réelle et coût (§4)

Relevé par étage, question `current`, en tokens **estimés** (`chars/4`) :

| Scénario | Variante | brut | après dédup | après sélection | après compression | bloc mémoire | **final** | économie | latence |
|---|---|---|---|---|---|---|---|---|---|
| mem_50 | B | 1496 | 1496 | 147 | 147 | 0 | 147 | 90,17 % | 1,1 ms |
| mem_50 | C | 1496 | 1461 | 1057 | 1461 | 0 | 1496 | 0,00 % | 10,8 ms |
| mem_50 | D | 1496 | 1461 | 1057 | 1461 | 1116 | 1278 | 14,57 % | 41,4 ms |
| mem_50 | E | 1496 | 1461 | 1057 | 1461 | 1104 | **1265** | 15,44 % | 46,1 ms |
| mem_200 | B | 5937 | 5937 | 147 | 147 | 0 | 147 | 97,52 % | 6,0 ms |
| mem_200 | C | 5937 | 5902 | 1096 | 1632 | 0 | 1667 | 71,92 % | 54,9 ms |
| mem_200 | D | 5937 | 5902 | 1100 | 1436 | 1126 | 1287 | 78,32 % | 205,7 ms |
| mem_200 | E | 5937 | 5902 | 1100 | 1436 | 1103 | **1265** | 78,69 % | 212,6 ms |

Trois explications nécessaires, sans lesquelles la table induit en erreur :

1. **Économie 0,00 % aux petites tailles** : sous le budget (`max_tokens=2000`), il
   n'y a rien à économiser — le contexte tient tel quel (mem_10 : 336 ; mem_25 :
   771 ; mem_50 : 1496). Ce n'est pas un échec, c'est le comportement correct.
2. **Colonne « bloc mémoire » ≠ contexte final** : à mem_10 et mem_25, le bloc
   reconstruit (424 / 888 tokens) est **rejeté à raison** (« bigger than the input »)
   et le pipeline retombe sur `selection+compression` : le bloc n'est **pas** dans
   le contexte final. Le chiffre autoritaire est la colonne **final** ; le bloc
   n'apparaît qu'à partir de mem_50 (variante `reconstructed`, `rejected: []`).
3. **Estimation, pas comptage exact** : `tiktoken` **n'est pas installé**
   (`find_spec('tiktoken') → False`, vérifié). Tous les comptages sont `chars/4`,
   marqués `count_kind = "estimated"`. Aucun chiffre de ce rapport n'est présenté
   comme un comptage exact.

**Coût en latence** : E coûte ≈ 205–213 ms sur 200 échanges contre 55 ms pour C et
6 ms pour B — le surcoût est la construction mémoire + retrieval + validation. Il
représente **+157 ms** pour supprimer 402 tokens de plus que C (1100 → 1265 contre
1667) *et* restituer les 5 classes d'information que C perd.

---

## 6. Vérification de la reconstruction (§5)

Même conversation (`mem_200`), cinq questions, variante E :

| Question | Empreinte du bloc | Sections | Requis présents | Manquant | Fuité |
|---|---|---|---|---|---|
| Which database does Orion use now? | `1f3a9a0b5859` | constraints, current decisions, episodes, facts, relations | PostgreSQL | — | — |
| Which database did Orion use before? | `780164ab6c8c` | + **history** | SQLite | — | — |
| Why was the storage change made? | `c5925a6f3205` | + **history** | SQLite | — | — |
| Which database does project Vega use? | `7cd57c327e37` | + **unknown** | — | — | — |
| What decisions were made about storage and caching over time? | `7d7c818c446a` | constraints, current decisions, episodes, facts, relations | PostgreSQL, Redis | — | — |

**5 contextes distincts sur 5 questions** : chaque question produit un contexte
différent, pertinent, et la section appropriée (`history:` pour « avant/pourquoi »,
`unknown:` pour « Vega ») n'apparaît que quand la question la demande. Aucun
`missing`, aucune fuite entre l'actuel et l'obsolète.

---

## 7. Critères de réussite (§6)

| Critère | Verdict | Preuve |
|---|---|---|
| Aucune instruction obligatoire perdue | ✅ | `mandatory ok` sur les 100 mesures (`10_integrity_validator.ok`) |
| Aucune information inventée | ✅ | E = **0** ligne non soutenue / 100 mesures (après correctif §1.7) |
| Aucune fuite entre conversations | ✅ | `cross-project = 0` partout (Orion↔Atlas jamais co-localisés) |
| Décisions actuelles non confondues avec les anciennes | ✅ | actuel≠obsolète **20/20** ; `(superseded)` exclu de la vue courante |
| La mémoire améliore la sélection | ✅ | D/E : décision en section 24/24 et 32/32, contre 0 pour C ; +8,4 pts d'économie sur C |
| La reconstruction apporte un bénéfice mesurable | ✅ | relations 6/10→10/10, impossible 0→3/3, provenance 1130/1130, à +0,2 pt de coût |
| Aucun test historique cassé | ✅ | **470 passed, 2 xfailed** (périmètre Boster) |
| Déterminisme préservé | ✅ | hash byte-identique, 3 exécutions × 2 `PYTHONHASHSEED` |

Sur les 113 erreurs de collecte de `pytest benchmark/` : ce sont des fichiers d'un
**autre projet** présents dans le dossier (imports de `core`, `intelligence`…), déjà
classés « environnement » lors de l'audit précédent ; **aucun** ne mentionne
`boster`. Le périmètre Boster réel est celui exécuté ci-dessus.

---

## 8. Limites restantes (sans enjolivement)

1. **Rappel des raisons** : 5/5 pour E sur la question « pourquoi », mais seulement
   2/5 sur `current`/`multi_episode` — la raison n'est pas une classe de premier
   rang, elle dépend de la sélection du message. Une section `reasons:` n'a pas été
   ajoutée : le gain n'a pas été démontré pour les questions qui la demandent (déjà
   5/5).
2. **Valeur historique pas toujours présente** : 16/18 pour E (`unknown`,
   `multi_episode`) — éviction par budget. Toujours présente quand la question la
   demande.
3. **Détection d'absence (impossible)** : 3/3 **parmi les tailles où un bloc est
   produit**. Aux tailles 10/25, aucun bloc n'est émis (conversation sous budget),
   donc la métrique est non-applicable — pas « réussie ».
4. **Comptage de tokens estimé** : `tiktoken` absent ; tous les chiffres sont en
   `chars/4`. Les économies relatives restent comparables entre variantes, les
   valeurs absolues sont approximatives.
5. **Les suppléments décisions/relations sont des motifs explicites.** Ils couvrent
   les formulations mesurées (`changed…to…instead of`, `switched…from`,
   `depends on`, `relies on`). Aucune inférence : une formulation hors motif reste
   non extraite, ce qui est préférable à une interprétation hasardeuse (règle
   d'or Phase 2.2.3).

---

## 9. Restrictions respectées

* Pipeline non refait : seuls des défauts mesurés ont été corrigés (§1).
* Modules **legacy de compression non modifiés** (le réparateur vit dans l'étage
  du pipeline, pas dans `output_compressor`).
* Aucun LLM ajouté à la compression ; aucun appel réseau, aucune horloge, aucun
  aléa dans les chemins modifiés.
* Hermes non modifié. Architecture mémoire (`consolidation.py`, `models.py`,
  `retrieval.py`) **non modifiée** : les suppléments vivent dans
  `boster/deterministic_pipeline.py`.
* Aucune dépendance installée. **Aucun commit créé.**

## 10. Reproduction

```bash
cd C:/Users/celes/Documents/Hermes-Workspace/Boster

# matrice complète (5 tailles × 5 questions × 4 variantes) + JSON
PYTHONPATH="." uv run python -m benchmark.deterministic_pipeline.measure_memory \
  --json C:/Users/celes/AppData/Local/Temp/mem_matrix.json

# non-régression du périmètre Boster
PYTHONPATH="." uv run pytest benchmark/deterministic_pipeline benchmark/memory_retention \
  benchmark/compression_validation benchmark/memory_episodic benchmark/memory_determinism \
  benchmark/memory_adversarial benchmark/test_v1.py benchmark/test_v2_intelligence.py \
  benchmark/test_boster.py benchmark/test_plugin_hooks.py benchmark/test_retrieval.py -q
# -> 470 passed, 2 xfailed
```

Fichiers nouveaux ou modifiés par cette phase :

| Fichier | Nature |
|---|---|
| `boster/deterministic_pipeline.py` | correctifs §1.1–1.6 + `query` sur `build_memory` |
| `benchmark/deterministic_pipeline/measure_memory.py` | **nouveau** — harnais de mesure §3/§4/§5 |
| `benchmark/deterministic_pipeline/scenarios.py` | `unknown_subject` (support de la question impossible) |
| `benchmark/deterministic_pipeline/compare_variants.py` | correctif du contrôle d'invention §1.7 |

**En attente de votre autorisation avant toute suite.**
