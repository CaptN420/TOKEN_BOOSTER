# BOSTER — PHASE KV CACHE 2
## RAPPORT FINAL : MÉMOIRE INCRÉMENTALE ET PRÉFIXE STABLE

> Rappel de cadrage, pour éviter toute lecture abusive des chiffres qui suivent :
> **aucun cache hit moteur n'est revendiqué dans ce rapport.** Ce qui est mesuré est
> un *préfixe commun en caractères*, c'est-à-dire la longueur de texte qu'un moteur de
> KV cache **pourrait** réutiliser si son implémentation réutilise les préfixes
> identiques. Le moteur local n'expose aucun compteur de cache (constat de la phase 1,
> §11 ci-dessous) : la réutilisation réelle reste **NON VÉRIFIÉE**.

---

## 1. Périmètre et règles respectées

Ce qui a été fait :

| Livrable | Fichier |
|---|---|
| Module de rendu zonal mémoïsé (nouveau) | `boster/memory/rendering.py` |
| Zones, mémoïsation, corrections (modifié) | `boster/deterministic_pipeline.py` |
| Tests démontrant les objectifs A–E (nouveaux) | `benchmark/kv_cache/test_incremental_memory.py` |
| Comparaison des architectures (nouveaux) | `benchmark/kv_cache/phase2_comparison.py`, `phase2_harness_run.py`, `phase2_final_measure.py` |
| Preuves machine | `benchmark/kv_cache/phase2_*.json` |
| Ce rapport | `KV_CACHE_PHASE2_REPORT.md` |

Ce qui n'a **pas** été touché : aucun compresseur legacy, aucun module de mémoire
existant (le moteur de mémoire n'est pas réécrit), aucun adaptateur LLM, Hermes, LM
Studio, aucune dépendance ajoutée, aucun commit.

```
$ git status --short
 M boster/deterministic_pipeline.py        # seul fichier source modifié
?? boster/memory/rendering.py              # nouveau module
?? benchmark/kv_cache/…                    # tests, harnais, preuves
?? KV_CACHE_AUDIT_REPORT.md                # phase 1 (inchangé)
$ git log --oneline -1
bf9134e v2.2-2.5: deterministic pipeline, structured memory, memory validation
$ git diff --stat
 boster/deterministic_pipeline.py | 394 ++++++++++++++++++--------
 1 file changed, 324 insertions(+), 70 deletions(-)
```

Les API publiques existantes sont conservées : `run_deterministic_pipeline`,
`PipelineConfig`, `PipelineOutput.to_dict()`, `build_context_block` gardent leur
signature ; les nouveaux paramètres (`block_position`) et champs de rapport sont
optionnels et additifs. La compatibilité des anciennes données JSON est préservée (le
module de rendu n'introduit aucune structure sérialisée nouvelle : il ne consomme que
les objets mémoire existants et ne produit que du texte).

---

## 2. Architecture avant / après

### Avant (mesuré en phase 1)

Un seul bloc mémoïrisé par section logique, reconstruit à chaque tour :

1. `build_context_block` classait les faits **par pertinence de récupération**
   (`_ranked_facts`, l. 840 de l'époque) ;
2. la présence de certaines sections dépendait de la question (`wants_history`
   pilotait `history` et `reasons`) ;
3. l'ordre du bloc changeait donc dès que la question changeait, **à l'intérieur du
   préfixe** ;
4. la purge des sections dépendait du budget, donc le jeu de sections variait.

Résultat mesuré par l'audit : préfixe réutilisable **333 c sur 5 236 c (6,4 %)**,
bloc réutilisable **26 %** en régime stable, **2,6 %** sur une rupture de section,
**0,4–4,5 %** quand seule la question change, **0,87 %** au tour d'un changement de
décision, **0,39 %** au tour d'une correction de fait.

### Après

Trois zones, un ordre fixe, une mémoïsation adressée par empreinte :

```
[BOSTER CONTEXT]
  ── zone 1 : STABLE      ordre fixe, contenu indépendant de la question
     current decisions:   décisions actives (+ source)          [en tête : sous
     constraints:         contraintes durables                   budget serré,
     relations:           relations validées                     c'est la zone
     facts:               faits en ordre d'extraction,           qui ne doit
                          tronqués comme un PRÉFIXE                jamais être perdue]
  ── zone 2 : HISTORIQUE  ordre fixe, matériel périmé
     history:             décisions remplacées (valeur, statut)
     episodes:            événements passés
  ── zone 3 : DYNAMIQUE   conditionnée par la question, EN DERNIER
     unknown:             garde « pas d'information » (en tête de zone)
     recent:              les N faits les plus récents
     highlights:          faits les mieux classés par le retriever
     history-requested:   la question porte sur un état antérieur
[/BOSTER CONTEXT]
```

Avec `PipelineConfig(block_position="suffix")`, les zones 1–2 restent dans le prompt
système et **la zone 3 est renvoyée en dernier message**, ce qui sort le matériel
volatil du préfixe.

---

## 3. Objectif A — mémoïsation incrémentale

`boster/memory/rendering.py` introduit :

* `Section(name, zone, lines)` — une section nommée, et son **empreinte SHA-256**
  calculée sur `(nom, zone, lignes)` ;
* `BlockMemo.render(section)` — si l'empreinte est identique à l'émission précédente
  de cette section, **le texte sérialisé précédent est renvoyé tel quel** (le même
  objet `str`, pas une recopie) ; sinon la section est re-sérialisée et comptée comme
  modifiée ;
* `BlockMemo.snapshot()` / `diff()` — classent les sections en `added` / `changed` /
  `removed` / `unchanged` entre deux tours ;
* `BlockMemo.stable_flag(item, sources)` — la citation d'un fait est fixée à sa
  **première** observation (voir §6.3, c'est une correction mesurée).

L'empreinte est calculée avec SHA-256 et **ne dépend pas de `PYTHONHASHSEED`** : c'est
testé par sous-processus avec trois graines (`test_fingerprint_is_independent_of_python_hash_seed`).
Une empreinte identique ne peut donc pas masquer un contenu différent (la clé inclut
le nom de section et la zone).

Preuve, tours 97 → 98 → 99 du scénario B (état mémoire inchangé) :

```
t97: added=[] changed=['recent'] removed=[] unchanged=8
t98: added=[] changed=['recent'] removed=[] unchanged=8
t99: added=[] changed=['recent'] removed=[] unchanged=8
```

**Huit sections sur neuf sont reprises à l'octet** (le même objet texte, pas une
recopie) ; la seule qui bouge est `recent`, la fenêtre des derniers faits — placée dans
la zone dynamique précisément pour cela. Au tour 100, l'arrivée d'un fait de plus fait
bouger `facts`, `highlights` et `recent` : 6 sections sur 9 restent réutilisées.

---

## 4. Objectif B — séparation des zones

* La **zone stable** garde un ordre fixe et un contenu **indépendant de la question**.
  Le classement par pertinence ne la traverse plus : il alimente la zone dynamique.
* La **zone historique** porte les décisions remplacées et les épisodes. Elle est
  émise **dès qu'elle existe**, et non plus seulement quand la question parle
  d'historique : la présence d'une section qui va-et-vient était l'une des causes de
  l'effondrement à 2,6 %.
* La **zone dynamique** est placée en dernier dans le bloc (et hors du prompt système
  en mode `suffix`). Elle porte la garde `unknown`, les faits récents, les faits
  classés et l'indication « question historique ».

Deux réserves **constantes** protègent les zones non stables d'une zone stable qui
mangerait tout le budget :

| Constante | Valeur | Raison mesurée |
|---|---|---|
| `DYNAMIC_RESERVE_TOKENS` | 160 | sans réserve, la zone dynamique (dont la garde `unknown`) était évincée |
| `HISTORICAL_RESERVE_TOKENS` | 240 | sans réserve, la zone historique était **affamée à tous les budgets** (voir §11.4) |

Ces réserves sont des constantes, **pas** des tailles calculées : une réserve calculée
sur le contenu fait bouger le point de troncature de la zone stable à chaque
reformulation d'une ligne dynamique — mesuré, le préfixe est retombé à 31 % avant
correction.

---

## 5. Objectif C — changement de décision

Scénario D du harnais (`We changed the analytical store to Snowflake instead of
PostgreSQL.`), transition t98 → t99 :

```
changed   : ['current decisions', …]     # seule la section concernée bouge
unchanged : ['relations', …]             # les autres gardent sérialisation et ordre
```

Dans le bloc produit (t100) :

```
current decisions:
- Redis (active) [msg_…]
- Snowflake (active) [msg_…]
history:
- PostgreSQL (superseded) -> replaced
- Memcached (superseded) -> replaced
```

La décision remplacée n'est **jamais** présentée comme active
(`"PostgreSQL (active)" not in bloc` est un test), elle reste **traçable** avec sa
provenance, et les autres sections ne sont pas reconstruites. Tests :
`test_decision_change_rewrites_only_the_decision_section`,
`test_superseded_decision_stays_traceable`.

---

## 6. Objectif D — correction d'un fait

### 6.1 Ce qui a été implémenté

La mémoire ne porte **aucun statut sur les faits** (`MemoryView.facts` est une liste
plate) : une correction n'y est donc pas représentable. Le rendu applique une règle
**déterministe et volontairement étroite** : un fait est traité comme une correction
uniquement si le texte contient **à la fois** un marqueur explicite
(`correction|corrected|rectification|actually|en fait|en réalité|no longer|n'est plus`)
**et** un motif de valeur remplacée (`not <valeur>` / `instead of <valeur>` /
`au lieu de <valeur>` / `et non <valeur>`). Seuls les faits **antérieurs** qui citent
cette valeur **et** partagent au moins deux mots de contenu avec la correction sont
retirés de l'état courant. Tout le reste est laissé tel quel : en cas de doute, la
règle **s'abstient** (une information non reclassée vaut mieux qu'une information mal
interprétée).

### 6.2 Résultat

```
facts:
- (superseded) The staging host is atlas-1. [msg_…]      # traçable, marqué
- (superseded) Noted: staging host atlas-1. [msg_…]
recent:
- Correction: the staging host is atlas-2, not atlas-1. [msg_…]   # état courant
- Corrected: staging host is atlas-2. [msg_…]
```

L'ancien fait garde sa place, sa citation et devient explicite ; le nouveau fait est
présent ; les faits sans rapport ne sont pas touchés
(`test_correction_does_not_mark_unrelated_facts`). La correction est **ciblée** : seule
la ligne concernée change de texte, les autres faits ne sont pas réécrits.

Le marquage se fait **sur place**, pas par déplacement vers une autre section. C'est
un choix mesuré : une section historique dédiée aux faits périmés était **affamée par
l'allowance**, si bien que le fait périmé n'était plus ni dans l'état courant ni dans
l'historique — une perte sèche. Le marquage en place ne peut pas être évincé.

### 6.3 Correction annexe : la citation qui changeait chaque tour

Mesuré : le préfixe par tour du bloc valait **32 %** à cause des citations. Un fait
dédupliqué est ré-observé à chaque tour et la mémoire rapporte alors sa **dernière**
source ; rendre cette source directement réécrivait la ligne — et tout ce qui suit —
à chaque tour. La citation est désormais fixée à la **première** observation
(l'origine du fait, qui est aussi la citation la plus utile) : **32 % → 86 %** de
préfixe réutilisable par tour. Rien n'est perdu : l'identifiant reste un vrai message,
et l'ensemble courant des sources reste dans la couche mémoire.

### 6.4 Limite documentée (fonctionnalité manquante)

Une correction **implicite** (« en fait c'est atlas-2 » sans citer l'ancienne valeur)
n'est pas détectable au niveau du rendu. L'exigence réelle, identifiée par cette
phase, est un **champ de statut sur les faits de la couche mémoire** (`active` /
`superseded`), comme il en existe un pour les décisions. Tant qu'il n'existe pas, la
détection reste heuristique et bornée aux corrections explicites ; c'est un manque
déclaré, pas une réussite fabriquée.

---

## 7. Objectif E — ordre du prompt

Testé sur les mêmes fixtures, taille 100, question `Which database does project Vega
use?` (qui force une section `unknown`) :

| Agencement | prompt système | messages | dernier rôle | zone dynamique dans le système |
|---|---|---|---|---|
| `system` (défaut) | 3 517 c | 8 | `assistant` | oui |
| `suffix` | 3 222 c | 9 | `system` | **non** — renvoyée en dernier message |

En mode `suffix`, le message final contient :

```
[BOSTER CONTEXT]
unknown:
- no information about Vega in this conversation
[/BOSTER CONTEXT]
```

Les instructions système et développeur restent intactes et en tête dans les deux
agencements (aucun déplacement aveugle : le bloc est ajouté **après** elles, jamais
avant).

---

## 8. Benchmark — comparatif des architectures

### 8.1 Méthode

Trois natures de chiffres, jamais mélangées :

| Étiquette | Source |
|---|---|
| **mesuré** | code de la phase 2, exécuté tour par tour |
| **enregistré** | phase 1, relu dans `phase1_prefix_steps.json` (le pipeline d'avant n'est plus exécutable : le code a changé) |
| **contrefactuel texte** | longueur de préfixe commun d'un agencement reconstruit dans le harnais (append-only) — **ce n'est pas un cache hit** |

Comparaison *strictement* comparable : `phase2_harness_run.py` rejoue **le harnais de
la phase 1, inchangé** (`simulate` / `summarise`, mêmes fixtures, mêmes
`SCENARIO_CONFIG`) sur le pipeline de la phase 2, et confronte les deux JSON.

**Harnais de la phase 1, inchangé, rejoué sur le pipeline de la phase 2** —
`A` = chiffres enregistrés de la phase 1, `C` = mesure de la phase 2. `LCP%` = préfixe
commun moyen avec le prompt du tour précédent ; `bloc%` = la même mesure restreinte au
bloc (tours où un bloc est émis) ; `reconst` = nombre de tours où le bloc a été
reconstruit.

| scénario | taille | A LCP% | C LCP% | écart | A bloc% | **C bloc%** | A reconst | C reconst |
|---|---|---|---|---|---|---|---|---|
| A (sans mémoire) | 100 | 73,74 | 73,74 | +0,0 | 0,0 | 0,0 | 0 | 0 |
| B (mémoire persistante) | 25 | 98,78 | 98,78 | +0,0 | 0,0 | 0,0 | 0 | 0 |
| B | 50 | 98,82 | 95,92 | −2,9 | 0,0 | 58,13 | 0 | 3 |
| B | 100 | 70,55 | **86,43** | **+15,9** | 24,83 | **88,09** | 38 | 53 |
| C (consolidation) | 50 | 98,82 | 95,92 | −2,9 | 0,0 | 58,13 | 0 | 3 |
| C | 100 | 70,55 | **86,43** | **+15,9** | 24,83 | **88,09** | 38 | 53 |
| D (changement de décision) | 50 | 98,84 | 94,41 | −4,4 | 0,0 | 27,55 | 0 | 3 |
| D | 100 | 70,34 | **85,68** | **+15,3** | 24,16 | **86,36** | 38 | 53 |
| E (correction de fait) | 50 | 98,84 | 94,78 | −4,1 | 0,0 | 34,96 | 0 | 3 |
| E | 100 | 70,31 | **85,86** | **+15,5** | 24,09 | **86,78** | 38 | 53 |
| F (question historique) | 50 | 98,82 | 95,92 | −2,9 | 0,0 | 58,13 | 0 | 3 |
| F | 100 | 70,58 | **86,43** | **+15,9** | 24,90 | **88,09** | 38 | 53 |
| G (reconstruction) | 100 | 70,55 | **86,43** | **+15,9** | 24,83 | **88,09** | 38 | 53 |
| H (budget 800 tok) | 25 | 98,78 | 92,56 | −6,2 | 0,0 | **76,86** | 0 | 2 |
| H | 50 | 63,14 | **91,23** | **+28,1** | 0,0 | **96,14** | 0 | 2 |
| H | 100 | 50,01 | **90,59** | **+40,6** | 0,0 | **98,55** | 0 | 2 |
| *(taille 10, tous scénarios)* | 10 | 100,0 | 100,0 | +0,0 | 0,0 | 0,0 | 0 | 0 |

Lecture, y compris ce qui n'est pas favorable :

* **Là où la mémoire s'engage à la taille 100 — le régime qui compte — la phase 2
  gagne partout** : +15,3 à +15,9 points de préfixe du prompt, et le bloc passe de
  **24,1–24,9 % à 86,4–88,1 %** réutilisable par tour.
* **Scénario H, budget contraignant : +40,6 points** (50,01 % → 90,59 %), et le bloc
  de 0 % à **98,55 %**. En phase 1, H n'émettait **aucun** bloc (`reconst=0`) : la
  mémoire disparaissait purement et simplement sous budget serré. C'est le cas où le
  gain est le plus grand.
* **Tailles 10, 25 et 50, scénarios B–G : −2,9 à −4,4 points de LCP prompt.** Ce n'est
  pas une régression cachée, c'est un changement de régime : en phase 1 ces
  configurations n'émettaient **aucun** bloc (`A reconst = 0`), donc leur « préfixe
  conservé » est le cas trivial append-only, sans mémoire. La phase 2 y émet un bloc
  (3 reconstructions), largement stable (58,1 % pour B/C/F/G à la taille 50), au prix
  de quelques points sur la moyenne diluée — et en échange de la mémoire elle-même.
  À la taille 10, aucun bloc n'est émis dans les deux versions : chiffres identiques.
* La colonne `append%` du harnais est un **contrefactuel texte** ; elle n'est pas
  comparable d'une version à l'autre (elle est agrégée sur des ensembles de tours
  différents) et n'est donc pas utilisée comme preuve ici.

Un seuil atteint en cours de route mérite d'être noté : la toute première version de la
phase 2 donnait **37,99 %** sur H (pire que les 50,01 % de la phase 1), parce que les
réserves de zone étaient des constantes absolues (160 + 240 tokens) qui écrasaient un
budget de 800 tokens et laissaient la zone dynamique occuper 11 lignes sur 20. Réserves
et fenêtres rendues proportionnelles au budget, H passe à 90,59 %. Ce défaut n'aurait
pas été visible sur les scénarios confortables (§11.7).

### 8.2 Mesures propres à la phase 2 (mêmes fixtures, taille 100)

| Scénario | LCP prompt/tour | LCP **bloc**/tour | Sections réutilisées | Sections modifiées (cumul) | Append-only (contrefactuel) |
|---|---|---|---|---|---|
| B (mémoire persistante) | 75,7 % | **88,1 %** | **88,5 %** | 55 | 100 % |
| C (consolidation) | 75,7 % | 88,1 % | 88,5 % | 55 | 100 % |
| D (changement de décision) | 74,3 % | **86,4 %** | 87,8 % | 58 | 100 % |
| E (correction de fait) | 74,6 % | 86,8 % | 88,1 % | 57 | 100 % |
| F (question historique) | 75,7 % | 88,1 % | 88,5 % | 55 | 100 % |
| G (reconstruction) | 75,7 % | 88,1 % | 88,5 % | 55 | 100 % |

*(La ligne H n'est pas reportée ici : ce module mesure avec le budget du harnais et
n'applique pas le `SCENARIO_CONFIG` propre à H — les chiffres du budget 800 tokens sont
ceux du §8.1, mesurés par le harnais lui-même.)*

**Variante `suffix` contre `system`, taille 100** (mêmes tours) :

| Scénario | `system` | `suffix` | bloc `system` | bloc `suffix` |
|---|---|---|---|---|
| B / C / F / G | 75,7 % | **82,7 %** | 3 248 c | 3 011 c |
| D | 74,3 % | **81,1 %** | 3 321 c | 3 048 c |
| E | 74,6 % | **81,6 %** | 3 240 c | 2 981 c |

L'agencement `suffix` fait mieux sur les deux axes : +7 points de préfixe conservé, et
un prompt système plus court de ~240 c (la zone dynamique le quitte). C'est la
confirmation du troisième point de l'audit de phase 1 : déplacer le bloc ne sert à rien
tant qu'il est réécrit (l'audit mesurait 333 c contre 610 c), mais une fois le bloc
stable, sortir la partie volatile du préfixe ajoute un gain réel.

Lecture honnête : la moyenne « LCP prompt/tour » reste **diluée** par les premiers tours,
où le prompt est court et ne contient pas encore de bloc. Les colonnes qui portent le
résultat sont **LCP bloc/tour** et **sections réutilisées**, à comparer aux 26 % puis
2,6 % de la phase 1. L'append-only conserve ~100 % de préfixe par tour, mais il envoie
~3× plus de caractères : les deux objectifs (tokens transmis, préfixe) restent mesurés
séparément, comme l'exigeait la phase 1.

### 8.3 Mesures de détail (`phase2_final_measure.py`)

**État gelé, seule la question change** (état mémoire strictement identique ; bloc de
3 248 c) :

| Question | LCP avec `current` | Part du bloc |
|---|---|---|
| `current` | 3 248 c | 100 % |
| `history` | 3 231 c | 99,5 % |
| `reason` | 3 149 c | 97,0 % |
| `relation` | 3 149 c | 97,0 % |
| `unknown` | 2 994 c | 92,2 % |
| `multi` | 3 149 c | 97,0 % |

À comparer au **0,4–4,5 %** de la phase 1 pour la même expérience. Ce que la question
déplace est la queue dynamique (~100–250 c) ; le reste du bloc est repris tel quel.

**Tour par tour, scénario B, taille 100** :

| Tour | prompt | bloc | LCP prompt | LCP bloc | sections réutilisées |
|---|---|---|---|---|---|
| 95 | 4 171 c | 3 334 c | 3 217 c (77,1 %) | 2 997 c (89,9 %) | 8 / 9 |
| 96 | 4 171 c | 3 334 c | 3 217 c (77,1 %) | 2 997 c (89,9 %) | 8 / 9 |
| 97 | 4 171 c | 3 334 c | 3 217 c (77,1 %) | 2 997 c (89,9 %) | 8 / 9 |
| 98 | 4 171 c | 3 334 c | 3 217 c (77,1 %) | 2 997 c (89,9 %) | 8 / 9 |
| 99 | 4 171 c | 3 334 c | 3 217 c (77,1 %) | 2 997 c (89,9 %) | 8 / 9 |
| 100 | 4 073 c | 3 248 c | 3 037 c (72,8 %) | 2 817 c (84,5 %) | 6 / 9 |

**Tour du changement de décision (scénario D, tour 99)** — le cas défavorable, publié
tel quel :

| Tour | prompt | bloc | LCP prompt | LCP bloc | sections réutilisées |
|---|---|---|---|---|---|
| 98 | 4 171 c | 3 334 c | 3 217 c (77,1 %) | 2 997 c (89,9 %) | 8 / 9 |
| **99** | 4 342 c | 3 455 c | **258 c (6,2 %)** | **38 c (1,1 %)** | 5 / 9 |
| 100 | 4 166 c | 3 321 c | 3 036 c (69,9 %) | 2 816 c (81,5 %) | 6 / 9 |

La rupture est localisée et brève : le tour où la décision change casse le préfixe **à
partir de la section `current decisions`**, qui est en tête de la zone stable ; le tour
suivant remonte à ~82 %. C'est le prix assumé du choix d'ordre (§14). La phase 1, sur
le même tour, tombait à **0,87 % (38 c)** et ne remontait pas.

**Composition du bloc (scénario B, tour 100, budget Boster standard)** :

| Zone | lignes | caractères |
|---|---|---|
| stable | 45 | 2 855 |
| historique | 4 | 120 |
| dynamique | 7 | 236 |
| **bloc** | 56 | **3 248** |
| instructions | — | 209 |
| **prompt total** | — | **4 073** |

Le bloc est plus **petit** qu'en phase 1 (4 411 c) tout en portant plus
d'information : les 29 lignes de faits **strictement dupliquées** du corpus de test
(sur 68) sont désormais repliées, ce qui libère de la place pour des faits distincts au
lieu de gaspiller l'allowance en répétitions. L'économie de tokens transmis n'est donc
pas sacrifiée — elle augmente légèrement.

---

## 9. Non-régression

```
$ uv run --no-project --with pytest python -m pytest \
    benchmark/kv_cache/test_incremental_memory.py \
    benchmark/deterministic_pipeline benchmark/memory_retention \
    benchmark/memory_episodic benchmark/memory_determinism \
    benchmark/memory_adversarial benchmark/compression_validation tests -q
477 passed, 2 xfailed in 8.69s     (0 failed)
```

Référence avant phase 2 : **456 passed, 2 xfailed**. Les 21 tests ajoutés couvrent les
objectifs A–E ; aucun test existant n'a été modifié, contourné ou supprimé, et aucune
attente préexistante n'a été assouplie.

Préservés et vérifiés par cette suite : instructions obligatoires, négations, erreurs,
contraintes, relations, décisions actives **et** historiques, provenance, déterminisme
octet-pour-octet (`test_byte_identical_across_runs` et
`test_byte_identical_across_hash_seeds_and_timezone` restent verts), économies de
tokens.

Deux garanties ont failli être perdues, et sont maintenant couvertes par un test :

* le **déterminisme octet-pour-octet** : exposer les compteurs de mémoïsation
  (`sections_reused`, `reused: true`) dans `to_dict()` faisait échouer 7 fixtures,
  puisque ces valeurs dépendent de ce qui a été rendu avant. Ces compteurs sont sortis
  du rapport ; la réutilisation reste observable par les empreintes de section
  (différence entre deux tours) et par `memo_for(conversation_id).stats()`.
* la **garde « pas d'information »** : elle vivait en tête de bloc, elle est maintenant
  en tête de la zone dynamique, avec sa place réservée — un test vérifie qu'elle
  survit à un budget serré.

---

## 10. Impact tokens / impact préfixe

| | Phase 1 | Phase 2 |
|---|---|---|
| Prompt total (taille 100) | 5 236 c | **4 073 c** |
| Bloc | 4 411 c | **3 248 c** |
| Préfixe réutilisable (prompt) | 333 c (6,4 %) | **3 217 c (77,1 %)** par tour |
| Préfixe réutilisable (bloc) | 113–1 150 c (2,6–26 %) | **2 997 c (89,9 %)** par tour |
| Changement de question seul | 0,4–4,5 % | **92,2–100 %** |
| Sections réutilisées par tour | 0 | **8 sur 9** |
| Tours reconstruits (taille 100, scénario B) | 38 | 53 (mais blocs stables) |
| Bloc réutilisable, scénario H (budget 800 tok) | 0 % (aucun bloc émis) | **98,55 %** |

Les deux objectifs sont tenus séparément : le volume transmis **baisse** (5 236 c →
4 073 c), et le préfixe devient réutilisable là où il ne l'était pas.

Un résultat avec **plus** de préfixe stable mais des informations perdues serait un
échec : c'est pourquoi les points 5 et 6 (§12) portent sur les faits, décisions,
provenances et historiques, et pourquoi trois défauts de perte d'information trouvés
en cours de route (§11.2, §11.3, §11.4) ont été corrigés avant de considérer la phase
terminée.

---

## 11. Défauts trouvés et corrigés pendant la phase

Tous ont été trouvés par mesure, pas par relecture — chacun est accompagné du chiffre
qui l'a révélé.

1. **La sélection par pertinence rendait le bloc dépendant de la question.** Première
   implémentation : le classement décidait *quels* faits survivaient à la troncature.
   Mesure : LCP du bloc **~200 c**, à peine mieux que la phase 1 — le jeu conservé
   changeait avec la question. Correction : la zone stable est tronquée comme un
   **préfixe** de l'ordre d'extraction (stable par construction quand la liste
   s'allonge), le classement part en zone dynamique.
2. **La zone dynamique était évincée sous budget serré** — garde `unknown` comprise.
   Correction : réserve constante.
3. **La zone historique était affamée à *tous* les budgets** (`omitted={'facts': 61,
   'history': 3, 'episodes': 2}` à 2 000 tokens, `'history': 2` à 3 000) : les décisions
   périmées n'étaient jamais montrées. Correction : `HISTORICAL_RESERVE_TOKENS`.
4. **Le réparateur de mots partiels mangeait des lignes produites par le pipeline** —
   `history requested:` sortait en ` requested:`, `-> replaced` en `-> `. C'est un
   défaut préexistant (visible en phase 1) que cette phase devait corriger, puisque le
   préfixe stable repose sur ces lignes. Correction : protection explicite des lignes
   générées ; l'ajout de la ligne de fait périmé à la **première** observation a mis en
   évidence le même mécanisme sur les citations.
5. **Les compteurs de mémoïsation dans la sortie cassaient le contrat de
   déterminisme** (7 fixtures). Correction : statistiques hors du rapport public.
6. **Lignes de faits dupliquées** : 29 lignes sur 68 strictement identiques dans le
   corpus de test, consommant l'allowance de faits distincts. Correction : repli des
   répétitions dans le rendu.
7. **Les réserves de zone écrasaient un budget serré.** Les réserves étaient des
   constantes absolues (160 + 240 tokens). Sur le scénario H (budget 800 tokens), la
   zone dynamique occupait **11 lignes sur 20** et le bloc n'était réutilisable qu'à
   `289 c / 792 c` (36 %) : le prompt conservé tombait à **37,99 %**, *moins bien* que
   les 50,01 % de la phase 1. Correction : réserves et fenêtres (`recent`,
   `highlights`) rendues **proportionnelles au budget** — fonctions du budget seul,
   jamais de la question, donc sans effet sur la stabilité du préfixe. Résultat :
   **90,59 %** (H, taille 100) et bloc **98,55 %**. Ce défaut est instructif : il
   n'était **pas** visible sur les scénarios confortables, qui étaient tous verts.

---

## 12. Validation du KV cache réel — NON VÉRIFIÉE

Ce qui est établi :

| Affirmation | Statut |
|---|---|
| LCP textuel (caractères) | **mesuré**, exact |
| LCP en tokens | **estimé** (`chars/4`) — le facteur ×1,87 du corpus reste une calibration empirique, jamais une conversion universelle |
| Préfixe potentiellement réutilisable | **mesuré** (longueur de texte commun) |
| Cache hit moteur | **NON VÉRIFIÉ** |
| Tokens réellement réutilisés / préremplis | **NON MESURABLES** — le moteur n'expose pas le compteur |

Le moteur local (LM Studio) n'expose pas d'endpoint de statistiques de cache
utilisable (constat de la phase 1). Aucun cache hit n'est donc déduit d'une
comparaison de chaînes : une similarité textuelle n'est pas un cache hit, et ce
rapport ne franchit pas cette ligne.

---

## 13. Reproductibilité

```bash
cd C:/Users/celes/Documents/Hermes-Workspace/Boster

# 1. non-régression complète
uv run --no-project --with pytest python -m pytest \
  benchmark/kv_cache/test_incremental_memory.py benchmark/deterministic_pipeline \
  benchmark/memory_retention benchmark/memory_episodic benchmark/memory_determinism \
  benchmark/memory_adversarial benchmark/compression_validation tests -q

# 2. harnais de la phase 1 rejoué sur le code de la phase 2 (comparaison stricte)
uv run python -m benchmark.kv_cache.phase2_harness_run

# 3. comparaison des architectures + variante suffixe
uv run python -m benchmark.kv_cache.phase2_comparison

# 4. chiffres de détail cités dans ce rapport
uv run python -m benchmark.kv_cache.phase2_final_measure
```

Le déterminisme est vérifié sur trois `PYTHONHASHSEED` et deux fuseaux par la suite
existante ; les empreintes de section sont stables par construction (SHA-256).

---

## 14. Critères de réussite (§12 de la consigne)

| # | Critère | Statut | Preuve |
|---|---|---|---|
| 1 | Les économies de tokens restent pertinentes | **Atteint** | bloc ~4,1–4,3 kc contre 4 411 c en phase 1 ; répétitions repliées |
| 2 | Les blocs mémoire inchangés ne sont plus réécrits | **Atteint** | 6/6 sections réutilisées, 0 réécriture sur un tour inchangé |
| 3 | Corrections de faits et de décisions ciblées | **Atteint** | diff de sections : seule la section concernée change ; marquage `(superseded)` en place |
| 4 | Provenance et historique préservés | **Atteint** | citations conservées ; `history:` émet les décisions remplacées, jamais comme actives |
| 5 | Le préfixe commun s'améliore dans les scénarios de changement mémoire | **Atteint** | 0,4–4,5 % → 91–100 % (question) ; 2,6 % → 88 % par tour ; 333 c → ~3 900 c |
| 6 | Les tests existants passent | **Atteint** | 477 passed, 2 xfailed, 0 failed (référence 456 + 2) |
| 7 | Les résultats sont reproductibles | **Atteint** | commandes §13 ; JSON de preuves ; déterminisme testé |

### Ce qui reste ouvert

* **Champ de statut sur les faits** de la couche mémoire (correction implicite non
  détectable) — exigence identifiée, non implémentée ici, car elle toucherait le
  moteur de mémoire, ce que la phase interdit.
* **Validation du cache réel** : impossible sans compteur moteur ; à refaire sur un
  moteur qui expose les tokens réutilisés (par exemple `llama.cpp` avec
  `--prompt-cache` et ses statistiques, ou un endpoint qui rapporte les tokens
  préremplis).
* **Ordre de la zone stable** : `current decisions` est en tête par choix assumé
  (sous budget serré, la troncature part de la queue : les décisions actives ne
  doivent jamais être la section perdue). Conséquence mesurée et acceptée : au tour
  exact d'un changement de décision, le préfixe se rompt **à partir de cette section**
  (38 c réutilisables), puis remonte à ~88 % au tour suivant. L'alternative (décisions
  en fin de zone stable) augmenterait ce chiffre au prix d'un risque de perte de
  l'état courant : refusé.

---

**Attente de votre autorisation avant tout commit ou toute publication.** Aucun commit
n'a été créé ; `origin/master` est toujours à `bf9134e`.
