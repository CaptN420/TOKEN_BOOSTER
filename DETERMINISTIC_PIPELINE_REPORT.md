# BOSTER — PHASE 2.4 — DETERMINISTIC PIPELINE REIMPLEMENTATION

**Rapport de fin de phase.** Tous les chiffres cités ici sont **mesurés** par
exécution du code sur des conversations figées (`benchmark/deterministic_pipeline/fixtures.py`),
avec une vérité terrain déclarée indépendamment. Aucune valeur n'est estimée à la
main, aucune réussite n'est déclarée sans exécution.

---

## 1. RÉSULTAT

Un moteur d'optimisation contextuelle déterministe existe et fonctionne :
**`boster/deterministic_pipeline.py`** (module unique, 10 responsabilités).

| | Réduction agrégée | Instructions conservées | Fixtures sans information inventée | Rétention moyenne |
|---|---|---|---|---|
| **A** prompt original | 0 % | 22/22 | 7/7 | 100 % |
| **B** pipeline Boster existant | 77,24 % | **6/22** | 5/7 | **60,2 %** |
| **C** déterministe, sans mémoire | 49,13 % | 22/22 | 7/7 | 100 % |
| **D** déterministe + mémoire | **56,32 %** | 22/22 | 7/7 | 100 % |
| **E** déterministe + mémoire + reconstruction | 50,74 % | 22/22 | 7/7 | 100 % |

**Lecture.** B obtient la plus forte réduction (77 %) et **détruit 16 des 22
instructions obligatoires** sur la fixture `instructions` (0/13) et fabrique 3
lignes non supportées. C'est exactement l'échec que la phase interdit : une
compression de 90 % qui détruit une contrainte n'est pas une réussite. Les
variantes déterministes conservent **100 %** de la vérité terrain mesurée.

Comptage de tokens : **`estimated`** (chars/4). `tiktoken` n'est pas installé ;
aucun chiffre n'est présenté comme exact (voir §7).

---

## 2. ARCHITECTURE LIVRÉE

Architecture cible → réalisation. Les responsabilités ont été **fusionnées
lorsque cela réduisait la complexité sans mélanger les contrats**.

| Étage | Agent cible (§4) | Fonction réelle |
|---|---|---|
| 1 | Input Analyzer | `analyze_input` — messages, rôles, système/développeur séparés, instructions/erreurs extraites |
| 2 | Format Detector | `detect_format` — réutilise `_auto_detect_format` + discriminateurs déterministes |
| 3 | Content Classifier | `classify_content` — fait / instruction / contrainte / décision / question / réponse / erreur / historique / temporaire |
| 4 | Duplicate Detector | `drop_duplicates` — empreinte normalisée, conserve la 1ʳᵉ occurrence |
| 5 | Information Selector | `score_turn` — obligatoire > requête > entités > récence ; budget dur |
| 6 | Memory Retriever | `build_memory` — `consolidate_to_episodes` + `hybrid_search` |
| 7 | Context Reconstructor | `reconstruct_context` — bloc typé, décisions courantes ≠ historique, provenance |
| 8 | Context Compressor | `compress_fragment` — réutilise les agents existants (voir §3) |
| 9 | Token Budget Manager | niveaux `minimal/balanced/complete`, part d'historique, `within_budget` |
| 10 | Integrity Validator | `validate_output` — instructions, négations, décisions, erreurs, sécurité |

API :

```python
from boster.deterministic_pipeline import run_deterministic_pipeline, PipelineConfig
out = run_deterministic_pipeline(messages, system_prompt=..., query=...,
                                config=PipelineConfig(max_tokens=8000, level="balanced"))
out.system_prompt, out.messages, out.tokens_before, out.tokens_after
out.validation.ok          # False dès qu'un contenu obligatoire manque
out.to_dict()              # sérialisation canonique, sans horloge
```

---

## 3. AGENTS EXISTANTS RÉUTILISÉS

Aucun agent n'a été dupliqué : **aucun module de compression n'a été modifié**.

| Agent existant | Usage dans le pipeline | État mesuré |
|---|---|---|
| `output_compressor.compress / safe_compress` | garde-fous, cap tête/queue | déterministe, réutilisé |
| `adaptive.adaptive_compress` | sortie d'outil non reconnue | 84,3 % (JSON) mesuré |
| `compression.logs.compress_logs` | logs, `keep_errors=True` | déterministe, **erreur vérifiée après coup** |
| `compression.code/json/markdown/structured` | dispatch par format | déterministe |
| `compression.terminal.compress_terminal` | sortie terminal | déterministe |
| `history_compressor` | résumé d'historique | déterministe |
| `core.tokenizer / core.budget` | comptage, estimation | `estimated` si pas de tokenizer |
| `core.policies.PreservationPolicy / SafetyCheck` | diff de sûreté | déterministe |
| `memory.consolidation.consolidate_to_episodes` | entités/relations/décisions/épisodes | déterministe (Phase 2.3) |
| `memory.retrieval.hybrid_search` | récupération liée à la requête | déterministe |

---

## 4. MESURES A→E (détail réel)

```
fixture            variant                   before   after   saved   red%  keep%   instr unsup  verb
long               A_original                  3954    3954       0    0.0  100.0 4/4         0     0
long               B_classic                   3954     106    3848  97.32   36.4 1/4         5     6
long               C_no_memory                 3954    1620    2334  59.03  100.0 4/4         0     1
long               D_memory                    3954    1158    2796  70.71  100.0 4/4         0    26
long               E_memory_reconstructed      3954    1509    2445  61.84  100.0 4/4         0     0
code               B_classic                    967      55     912  94.31  100.0 1/1         0     0
code               C/D/E_no_memory              967     471     496  51.29  100.0 1/1         0     1
logs               B_classic                   1234    1212      22   1.78  100.0 2/2         0     0
logs               C/D/E_memory                 1234     672     562  45.54  100.0 2/2         0     0
instructions       B_classic                    479      55     424  88.52    0.0 0/13        3     3
instructions       C_no_memory                  479     475       4   0.84  100.0 13/13       0     0
instructions       D_memory                     479     439      40   8.35  100.0 13/13       0    17
instructions       E_memory_reconstructed       479     475       4   0.84  100.0 13/13       0     0
short/contradiction/multi_conversation (A→E)   ~0–4 % — conversation trop courte pour gagner
```

- `unsup` = lignes dont un token n'existe **nulle part** dans la source (test
  « aucune information inventée », §5 Agent 10).
- `verb` > 0 = non-verbatim mais **supporté** : une transformation (JSON
  re-sérialisé, corps de fonction retiré) n'est pas une fabrication. La colonne
  `unsup` est le test décisif.
- Sur les conversations courtes, le pipeline **ne gagne rien et ne gonfle pas** :
  l'échelle déterministe retombe sur la variante la moins chère validée, jusqu'au
  contexte original.

Bloc reconstruit (extrait réel, variante E, fixture `long`) :

```
[BOSTER CONTEXT]
current decisions:
- PostgreSQL (active) [msg_049ec95bc273]
facts:
- Let's plan the Orion migration. The project currently uses SQLite for local storage. [msg_6621263c0922]
- The constraint is at most 4 GB of RAM on the analytical node. [msg_f5dd7e128a69]
...
[/BOSTER CONTEXT]
```

Décision courante séparée de l'historique, chaque entrée porte sa provenance,
la contrainte « at most 4 GB of RAM » survit.

---

## 5. BUGS TROUVÉS **PAR LA MESURE** ET CORRIGÉS

Chacun a été trouvé parce qu'une métrique a échoué, pas par relecture.

1. **Erreur non détectée au milieu d'une ligne.** `_ERROR_RE` était ancrée en
   début de ligne : `2026-11-15 10:00:57 ERROR connection refused postgres:5432`
   n'était pas classée obligatoire, et `compress_logs` laissait tomber la ligne.
   Un message d'erreur est obligatoire (§5 Agent 1). → sévérité marquée **n'importe où**
   + fragment à sévérité jamais concaténé sans preuve de survie.
2. **Instruction tronquée au niveau `minimal`.** Le cap à 1200 caractères
   supprimait 12 règles obligatoires. La validation le disait, mais le pipeline
   renvoyait quand même la sortie mutilée. → un fragment obligatoire **n'est
   jamais tronqué** ; un budget impossible est **signalé** (§9/§10), jamais payé
   avec du contenu perdu.
3. **Validateur trop strict, donc compression impossible.** Le contrôle d'erreur
   exigeait le message entier (6 229 caractères de log) : il déclarait « errors: 1
   missing » alors que la ligne ERROR était bien présente — ce qui bloquait
   *toute* compression de log. → une erreur se juge sur ses **lignes de
   sévérité**, pas sur son corps entier.
4. **Mots coupés en deux par les compresseurs legacy.** `safe_compress` coupait
   `return process(` en `return pro` ; `compress_logs` joignait tête et queue en
   plein mot (`...processed` → `d batch 48`). Les modules legacy n'ont **pas** été
   touchés : réparation **ancrée sur la source** dans l'étage 8 — un mot partiel
   n'existe nulle part dans la source, un mot complet y existe toujours.
5. **Double comptage.** Le bloc de contexte *s'ajoutait* à l'historique au lieu
   de le remplacer : les mêmes tokens étaient payés deux fois. → le bloc
   **remplace** les tours anciens ; seuls les fragments obligatoires et les tours
   récents restent verbatim.
6. **Fausse étiquette « exact ».** `token_count_kind` concluait « exact » parce que
   `CharEstimateAdapter.encode()` ne lève pas — alors que c'est l'estimation
   chars/4. Un estimé présenté comme mesure exacte. → l'identité de l'adaptateur
   est vérifiée ; sortie : `estimated`.
7. **« current decisions » vide.** En écartant les messages obligatoires de la
   mémoire, j'écartais aussi les décisions (une décision est obligatoire) : la
   section des décisions courantes disparaissait. → seuls les **gros dumps déjà
   verbatim** (système/développeur, logs) sont écartés de la consolidation ; une
   décision courte reste distillée.
8. **En-têtes de section mangés par la réparation.** `current decisions:` devenait
   `decisions:` (le mot « current » n'est pas un mot de la conversation). → les
   lignes structurelles écrites par le pipeline sont protégées.

---

## 6. CONTRAT DE DÉTERMINISME (vérifié)

Aucun écart, sur 3 graines de hachage et 2 fuseaux horaires :

```
PYTHONHASHSEED=0      TZ=UTC        1ab991508d1a5b3c2c3b66d31cb6b0bd8745db491911445151a751073a918512
PYTHONHASHSEED=1      TZ=UTC        1ab991508d1a5b3c2c3b66d31cb6b0bd8745db491911445151a751073a918512
PYTHONHASHSEED=12345  TZ=UTC        1ab991508d1a5b3c2c3b66d31cb6b0bd8745db491911445151a751073a918512
PYTHONHASHSEED=0      TZ=Etc/GMT-9  1ab991508d1a5b3c2c3b66d31cb6b0bd8745db491911445151a751073a918512
```

- Le module n'importe **ni `time`, ni `random`, ni `datetime`, ni `secrets`**
  (vérifié par test statique).
- Ordre des sections fixe, entrées triées, provenance stable.
- Le budget est **mesuré** : `within_budget` en étage 7, problème `budget:` en
  étage 10 quand il ne peut pas être tenu.
- Le pipeline ne renvoie **jamais** plus de tokens qu'il n'en a reçu (test sur
  toutes les fixtures × tous les niveaux).

---

## 7. LIMITES HONNÊTES

- **Comptage de tokens = estimation chars/4.** `tiktoken` absent ; `count_kind`
  vaut `estimated`. Les pourcentages de réduction sont donc indicatifs ; ils ne
  sont pas présentés comme des économies exactes.
- **Les conversations courtes ne se compressent pas.** À ~100 tokens, tout
  travail de structure coûte plus qu'il ne rapporte : le pipeline choisit alors
  légitimement la variante la moins chère, voire l'original. C'est un résultat,
  pas un échec.
- **E < D en réduction sur `long`** (61,8 % vs 70,7 %). La reconstruction
  structurée coûte des tokens en plus ; elle apporte la séparation
  décisions-courantes/historique et la provenance, pas la réduction maximale.
  Le choix entre D et E est un arbitrage, pas une supériorité.
- **`verb > 0` pour D** : la liste de faits est normalisée en une ligne (donc
  non-verbatim) mais chaque token est supporté par la source (`unsup = 0`).
- **Fixtures synthétiques.** 7 conversations écrites à la main couvrent erreurs,
  JSON, code, logs, contradiction, instructions, multi-projets — pas du trafic
  réel. Les chiffres valent pour ce corpus.
- **Pas encore intégré à Hermes** (interdit dans cette phase) et **aucun commit
  créé** (autorisation requise).

---

## 8. RÈGLES DE LA PHASE — ÉTAT

| Règle | État |
|---|---|
| Économie par transformations déterministes uniquement | ✅ aucun appel LLM dans le pipeline |
| Aucun LLM pour analyser/détecter/sélectionner/reconstruire/budgeter/valider | ✅ |
| Le LLM est consommateur, pas optimiseur | ✅ |
| Ne pas restructurer le projet | ✅ 1 module ajouté, 0 déplacé |
| Ne pas reconstruire l'architecture mémoire | ✅ `consolidate_to_episodes` réutilisé |
| Ne pas modifier les anciens modules de compression | ✅ `git diff` : aucun fichier `boster/compression/*`, `output_compressor.py`, `history_compressor.py`, `adaptive.py` |
| Ne pas supprimer de fonctionnalité | ✅ 470 passed, 2 xfailed, 0 failed |
| Ne pas créer un 2ᵉ agent si un existant convient | ✅ §3 |
| Ne pas considérer un module intégré parce qu'il existe | ✅ §3 colonne « état mesuré » |
| Diff limité au strict nécessaire | ✅ `boster/deterministic_pipeline.py` + `benchmark/deterministic_pipeline/` + ce rapport |
| Signaler, ne pas maquiller | ✅ validation `ok=False` remontée, budget impossible signalé |

---

## 9. VÉRIFICATION

```
benchmark/deterministic_pipeline/                       107 passed
suites de référence + deterministic_pipeline/           470 passed, 2 xfailed, 0 failed
```

Les 107 nouveaux tests couvrent : déterminisme byte-identique, digests
multi-seed/multi-TZ, absence d'horloge/aléa, survie des fragments obligatoires
(3 niveaux × 7 fixtures), erreurs jamais perdues, aucun rôle inventé,
**aucune information inventée** (3 configurations × 7 fixtures), non-gonflage,
budget mesuré et signalé, ordre des niveaux, déduplication, sélection liée à la
requête, décision supplantée jamais présentée comme courante, provenance,
non-duplication, idempotence sur sa propre sortie, détection de l'erreur en
milieu de ligne, validateur capable d'échouer.

`benchmark/` contient 113 erreurs de collecte **préexistantes et étrangères**
(`ModuleNotFoundError: No module named 'data'` — modules d'un autre projet).
Vérifié : **0** de ces erreurs provient de `benchmark/deterministic_pipeline/`.

---

## 10. FICHIERS

Ajoutés (non commités) :
- `boster/deterministic_pipeline.py`
- `benchmark/deterministic_pipeline/__init__.py`
- `benchmark/deterministic_pipeline/fixtures.py`
- `benchmark/deterministic_pipeline/compare_variants.py`
- `benchmark/deterministic_pipeline/test_deterministic_pipeline.py`
- `DETERMINISTIC_PIPELINE_REPORT.md` (ce document)
- `DETERMINISTIC_PIPELINE_AUDIT.md` (audit des agents, phase précédente)

Reproduire :

```bash
cd C:/Users/celes/Documents/Hermes-Workspace/Boster
PYTHONPATH="." uv run python -m benchmark.deterministic_pipeline.compare_variants
PYTHONPATH="." uv run pytest benchmark/deterministic_pipeline/ -q
```

---

## 11. PROCHAINE ÉTAPE — EN ATTENTE D'AUTORISATION

Non commencé, conformément à la consigne :

1. Installer `tiktoken` et remplacer les estimations par des comptages exacts.
2. Intégration Hermes (plugin) — **interdite dans cette phase**.
3. Trafic réel : mesurer sur des conversations réelles plutôt que sur fixtures.
4. Arbitrage D/E : décider si la reconstruction structurée justifie son surcoût.
5. Commit — **en attente explicite**.
