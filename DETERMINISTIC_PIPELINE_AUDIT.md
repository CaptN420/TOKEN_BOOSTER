# BOSTER — PHASE 2.4 : audit des agents déterministes (AVANT CODAGE)

Toutes les lignes marquées **[mesuré]** viennent d'une exécution réelle (stdout collé dans
`PHASE_2.4_REPORT.md`). Les lignes **[lu]** viennent de la lecture du source. Aucun agent
n'est déclaré « intégré » parce qu'il existe : la colonne *intégration* dit qui l'appelle.

## 0. Environnement réel (déterminant pour tout le reste)

| Fait | Mesure |
|---|---|
| `tiktoken` importable | **Non** |
| `tokenizers` / `requests` importable | **Non** |
| Adaptateur résolu pour `gpt-4o` | `TiktokenAdapter` — mais `encode()` lève `ImportError: tiktoken not installed` |
| Chemin de comptage effectif | **estimation** `max(1, (len+3)//4)` — `count_tokens('def f(x): return x+1') = 5` (20 chars) |
| `boster.cli.commands` | **inatteignable** : `ModuleNotFoundError: boster.cli is not a package` (le module `cli.py` masque le dossier `cli/`) |
| `benchmark/compression_validation/` | mal nommé : teste `boster.memory`, **pas** la compression |

Conséquence : tout chiffre de tokens de cette phase est une **ESTIMATION** et doit être
étiqueté comme tel (§7 : « Ne jamais prétendre avoir économisé des tokens exacts »).

## 1. Agents déterministes existants — réutilisables

| Agent (rôle cible) | Module | Entrée → Sortie | Déterministe | Intégration actuelle | Verdict |
|---|---|---|---|---|---|
| Détection de format | `output_compressor._auto_detect_format:353` **[lu]**, `adaptive._is_json/_is_diff/_is_docker/_is_tree/_is_markdown:36-59` **[lu]** | `str` → `str\|None` | oui | appelé par `compress()` | **RÉUTILISER** |
| Compresseur générique | `output_compressor.compress:293` **[mesuré]** git-log 206→69 chars ; `safe_compress:373` 1100→137 chars | `str` → `str` | oui | `pipeline.py`, `cli.py`, plugin Hermes | **RÉUTILISER** |
| Compresseur adaptatif | `adaptive.adaptive_compress:284` **[mesuré]** JSON 9791→1536 (84,3 %), générique 14917→1036 (93,1 %), `ERROR` conservée | `str` → `(str, dict)` | oui | `engine.py:11`, CLI | **RÉUTILISER** |
| Compresseurs par format | `compression/{json,markdown,code,logs,terminal,structured}.py` **[mesuré]** | `str` → `str` | oui | `terminal` via `engine`/`ContextCompressor` ; les autres : réexportés seulement | **RÉUTILISER** (sauf `markdown`, cassé) |
| Métriques | `output_compressor.savings_report:410` **[mesuré]** | `str,str` → `dict` (chars) | oui | `pipeline.py`, CLI | **RÉUTILISER** en second rideau — mesure en **caractères** |
| Métriques tokens | `core/token_metrics.savings_report:96`, `cost_estimate:81` **[lu]** | `str` → `dict` | oui | `engine` | **RÉUTILISER** |
| Comptage tokens | `core/tokenizer.count_tokens:217` **[mesuré]** | `str` → `int` | oui | partout | **RÉUTILISER** (étiqueter ESTIMATION) |
| Budget par catégorie | `context/allocator.TokenAllocator.allocate:65` **[lu]** | 5 ints → `AllocationPlan` | oui | **aucun appelant** (`test_v1.py:43` l'importe sans l'utiliser) | **RÉUTILISER + câbler** |
| Estimations messages | `core/budget.estimate_messages_tokens:258` (+4 tok/message) **[lu]** | `list[dict]` → `int` | oui | `engine.optimize` | **RÉUTILISER** |
| Déduplication | `context/deduplication.deduplicate_messages:19` **[lu]** exact O(n) / fuzzy Jaccard 0.9, ordre préservé | `list[dict]` → `list[dict]` | oui | exporté, **aucun appelant** hors test | **RÉUTILISER + câbler** |
| Politique de préservation | `core/policies.PreservationPolicy.check:87`, `ProtectedMarker.is_protected_line:46`, `SafetyCheck.check:163` **[lu]** | `str`,`str` → `dict{integrity,missing,warnings}` | oui | exporté | **RÉUTILISER** (base du validateur) |
| Classification de contenu | `memory/manager.classify_entry:62` (CATEGORY_PATTERNS) **[lu]** | `str` → `str` | oui | `MemoryManager.add` | **RÉUTILISER** (+ étendre) |
| Résumé d'historique | `history_compressor.compress_history:150` **[mesuré]** 20 éch. → 610 chars (85 %) | `list[dict]` → `HistorySummary` | oui | `pipeline.py`, `context_budget`, CLI | **RÉUTILISER** — mais **gain négatif sur court historique** (189→261 chars mesuré) |
| Mémoire sémantique 3 niveaux | `memory/consolidation.py` (`consolidate_to_episodes`, `consolidate_exchanges`) — **déjà déterministe (Phase 2.3)** | `list[dict]` → `(RecentContext, EpisodicMemory, SemanticMemory)` | **oui** | Phase 2.2/2.3 | **RÉUTILISER** |
| Extraction structurée | `consolidation._extract_entities/_extract_relations/_extract_decisions/_detect_contradictions` | texte → objets | oui | via consolidation | **RÉUTILISER** |
| Persistance | `memory/persistence.save_state/load_state` (`saved_at=` injectable) **[mesuré]** | — → JSON | oui | Phase 2.3 | **RÉUTILISER** |
| Récupération lexicale | `memory/retrieval.{lexical,bm25_style,hybrid}_search` **[lu]** | query → `MemoryQueryResult` | oui | `engine.retrieve_memory` | **RÉUTILISER** |

## 2. Agents existants — à NE PAS réutiliser tels quels (défauts mesurés)

| Agent | Défaut | Preuve | Décision |
|---|---|---|---|
| `ContextBudget._select_history:413` | Rend après la **première** itération : 15 messages → **1** message sélectionné (le plus ancien), `dropped=0` (perte silencieuse) | **[mesuré]** `select(15 msgs) → selected_messages=1`, `['m0 word']` | **NE PAS utiliser** pour l'historique ; réparer séparément |
| `context/assembler.ContextAssembler.assemble:43` | (1) ajoute `system_prompt` **et** garde le message système d'origine → 2 messages système ; (2) troncature non ré-appliquée : `max=50` → **73 tokens** | **[mesuré]** `system msgs = 2`, `total_tokens = 73` | **NE PAS utiliser** ; je reconstruis sans falsifier les rôles |
| `intelligence/integrity.ContextIntegrityEngine` | **Non idempotent** : `extract_from_messages` → facts 2 puis **0** puis 0 ; `preserve_in_compression` → `integrity_ok=False` puis **True** puis True sur entrées identiques | **[mesuré]** | **NE PAS utiliser** comme validateur → validateur **stateless** neuf sur `SafetyCheck` |
| `compression/markdown.compress_markdown:28` | **Grossit** sa cible : duplication du corps de code (`print(1)/print(2)` ×2), +17 chars ; bloc >20 lignes : +22 chars | **[mesuré]** | **RÉPARER** (réutilisé comme compresseur de format) |
| `memory/summarizer.summarize_to_memory:84` | Crée **0** entrée (3 causes : `key_points` écrasé l.78, préfixe `?` absent l.74-76 vs test l.116, regex erreur avec `\s` littéral l.64) | **[mesuré]** `created=0` alors que `extract_summary` trouve des points | **RÉPARER** (c'est le chemin d'ingestion mémoire du moteur) |
| `memory/manager.retrieve:133` | Le terme « récence » **récompense les plus anciens** : `min(1.0,(now-created_at)/86400)` → entrée vieille ⇒ score élevé ; égalités départagées par l'ordre d'insertion | **[lu]** l.160 + **[mesuré]** par le sous-agent | **RÉUTILISER** avec `now=` **et** mon propre scoreur de sélection |
| `intelligence/cache_awareness.CacheAwareness` | N'accède à **aucun** KV cache ; `estimated_recompute_tokens = lines*10` codé en dur | **[lu]** | **NE PAS exposer** comme pilotage de cache (§10 : « ne jamais prétendre contrôler le KV cache ») |
| `adapters/kv_cache.KVCacheAdapter` | **Aucun appelant** ; ne peut pas mesurer de réutilisation ; `cache_hit_rate` provient d'un champ de coût | **[lu]** | **HORS PÉRIMÈTRE** (laissé intact, non branché) |
| `analytics/__init__.py` | **Aucun appelant**, non exporté | **[lu]** | **HORS PÉRIMÈTRE** |
| `cli/commands.py` | **Inatteignable** (ombrage module/paquet) | **[mesuré]** | **HORS PÉRIMÈTRE** (ne pas supprimer) |
| `intelligence/{monitor,snapshot}.py` | Dépendants de l'horloge (`update`→`time.time()`, `uuid4`) — `last_compression_age_seconds` = 1,79e9 s avant toute compression | **[lu]** | **HORS PÉRIMÈTRE** du déterminisme |
| `core/policies.ProtectedMarker.restore_protected_blocks:64` | `placeholder = f"__PROTECTED_{hash(…)}__"` → `hash()` intégré **randomisé par processus** (4/4 seeds distincts **[mesuré]**) et **no-op** : rien n'insère jamais le placeholder | **[mesuré]** | **NE PAS utiliser** ; le validateur protège via `is_protected_line` |

## 3. Correspondance architecture cible → modules réels

| # | Agent cible | Source | Statut |
|---|---|---|---|
| 1 | Input Analyzer | **neuf** (préserve system/developer, métadonnées, `conversation_id`) | à écrire |
| 2 | Format Detector | `output_compressor._auto_detect_format` + `adaptive._is_*` | réutilisé |
| 3 | Content Classifier | `memory.manager.classify_entry` + `_tool_domains.DOMAIN_KEYWORDS` + règles | réutilisé + étendu |
| 4 | Duplicate Detector | `context.deduplication.deduplicate_messages` | réutilisé + câblé |
| 5 | Information Selector | **neuf** (réutilise `relevance.score_text_fragment`, entités/relations/décisions de `SemanticMemory`) | à écrire |
| 6 | Memory Retriever | `memory.consolidation` + `memory.retrieval` + `MemoryManager.retrieve(now=)` | réutilisé |
| 7 | Context Reconstructor | **neuf** — bloc typé, **jamais** un faux rôle `user` | à écrire |
| 8 | Context Compressor | `compression/*` + `adaptive` + `output_compressor` + `history_compressor` | réutilisé |
| 9 | Token Budget Manager | `core/budget.estimate_*` + `core/tokenizer` + `context/allocator.TokenAllocator` | réutilisé + niveaux |
| 10 | Integrity Validator | `core.policies.SafetyCheck` + `PreservationPolicy` (stateless) | réutilisé, **neuf ordonnanceur** |

**Fusion assumée (§4 : « Fusionner les responsabilités lorsque cela réduit la complexité ») :**
le Duplicate Detector est une **phase du** Information Selector ; le Token Budget Manager
est une **phase du** Context Reconstructor. 10 responsabilités, **8 étages** exécutables.

**Agents non nécessaires :** `kv_cache`, `analytics`, `cli/commands.py`, `snapshot`,
`monitor`, `cache_awareness`, `integrity` (remplacé). Aucun n'est supprimé (§11) : ils
restent en place, simplement non orchestrés.

## 4. Décisions de conception qui découlent de l'audit

1. **Ne pas hériter du sélecteur legacy** : `_select_history` perd l'historique ; mon
   pipeline fait sa propre sélection sur `RecentContext` + `EpisodicMemory`.
2. **Reconstruction sans faux rôle** : le bloc de contexte est un **bloc typé** ajouté au
   prompt système (préfixe stable → §10), pas un message `user` inventé.
3. **Validateur stateless** : le verdict doit dépendre uniquement des entrées (l'existant
   se contredit au 2ᵉ appel).
4. **Tout chiffre de tokens étiqueté `estimated`** tant que `tiktoken` est absent, et
   bascule automatique en `exact` s'il est présent.
5. **Déterminisme** : `now` explicite partout (aucune lecture d'horloge), aucun `set`
   itéré, `hash()` intégré interdit.
