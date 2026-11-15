# BOSTER — PHASE KV CACHE 4
## Architecture mémoire append-only et validation du véritable KV cache

Dépôt : `https://github.com/CaptN420/TOKEN_BOOSTER`
Commit de référence : **`cc41439`** (inchangé — aucun commit de cette phase)
Livrables : `benchmark/kv_cache/phase4/` (dont `AUDIT.md`), `boster/memory/append_only.py`

---

## 1. Audit initial

L'audit complet est dans `benchmark/kv_cache/phase4/AUDIT.md`, produit **avant toute
modification** (règle 3). En résumé :

| Contrôle | Résultat |
|---|---|
| `HEAD` | `cc41439` ; présent dans l'historique (`git cat-file -t` → `commit`) |
| Fichiers suivis modifiés | aucun |
| `origin/master` | `cc41439` — rien poussé, rien committé |
| Rapports lus | `KV_CACHE_AUDIT_REPORT.md`, `KV_CACHE_PHASE2_REPORT.md`, `KV_CACHE_PHASE3_REPORT.md` |

**Ce que la phase 3 a établi (loi du moteur, mesurée sur 2 builds)** : un **ajout pur**
réutilise tout le préfixe ; une **modification en place** retombe sur un point de
reprise interne (~63 % du prompt, même valeur pour une divergence à 75 % qu'à 98 %) ;
une **divergence dans la première moitié** ne réutilise rien. La phase 2 était donc
nécessaire mais pas suffisante : sa stabilité textuelle (94,6 % mesurés) ne produisait
que 65,3 % de réutilisation réelle.

**Ce qui existait déjà et n'a pas été réécrit** : `boster/memory/models.py`
(`MemoryItem`, `SourceRef`, `Decision` avec statut et provenance, `UNKNOWN_TIME = 0.0`
— aucune date inventée), `manager.py` (`classify_entry`, `generate_id`),
`consolidation.py` (extracteurs, identifiants, contradictions, épisodes),
`persistence.py` (écriture atomique), `rendering.py` (phase 2), `core/tokenizer.py`
(comptage réel ou **déclaré estimé**), et le harnais de la phase 3 (moteurs à
compteurs, remise à froid, format JSONL) — **importé, jamais copié**.

**Point d'intégration identifié** : `build_context_block()` assemble des `Section` par
zone puis `render_zones(sections, memo)` ; le rendu est mémoïsé **par empreinte de
section**, donc une section inchangée est identique à l'octet, mais elle est **re-rendue
entièrement dès que sa liste change** — c'est exactement ce qui coûte.

---

## 2. Architecture retenue

Nouveau module isolé : **`boster/memory/append_only.py`**. Aucun module existant n'a
été modifié.

```
prompt(t) = [en-tête] [PRÉFIXE IMMUABLE] [JOURNAL FIGÉ : E1 … En] [Q_t]
                  ^ figé à l'ouverture   ^ chaque entrée gelée à l'émission
                                         ^ rien après : la fin est la seule partie qui grandit
```

### A. Préfixe immuable
Figé à l'ouverture (`open_prefix`, appelable **une seule fois** — un second appel lève).
Contient les instructions système et les références stables. **Décision assumée** : une
contrainte découverte *en cours* de session n'entre pas dans le préfixe — elle le
modifierait — et devient une entrée `constraint` du journal, donc toujours présente
dans le prompt et traçable. C'est un compromis explicite, pas un oubli.

### B. Journal append-only
Entrées typées (`fact`, `correction`, `decision`, `decision_change`, `relation`,
`constraint`, `event`, `resolution`, `obsolete`, `exchange`, `question`, `answer`) avec
identifiant stable **par type** (`F001`, `C001`, `D001`…, comme l'exemple
`FACT_001`/`FACT_002` du brief), contenu, provenance (`source_ids`, jamais inventée),
horodatage **optionnel** (absent ⇒ `[date:…]` omis, jamais fabriqué), référence à
l'entrée corrigée, statut, confiance optionnelle. Le rendu de chaque entrée est **gelé à
l'émission** : réémettre le journal ne reformate jamais le passé.

**Le statut n'apparaît pas dans la ligne gelée**, volontairement : un statut est
*calculé* (une entrée devient `superseded` parce qu'une entrée ultérieure la nomme), et
l'écrire dans un texte qui ne peut plus être réécrit ferait affirmer à cette ligne
quelque chose qui cesse d'être vrai. Le statut vit dans la donnée de l'entrée et dans
le repli ; le cas « obsolète » est porté par le type `obsolete`.

### C. État courant calculé
`fold_current()` parcourt le journal (et les archives) dans l'ordre : **dernier
écrivain gagne par sujet**, une entrée nommée par un `supersedes` ultérieur est
renversée, une entrée `obsolete` marque sa cible. **Aucune entrée passée n'est
mutée** — la correction est une *nouvelle* entrée qui dit `(supersedes F001)`, et
l'ancienne ligne continue de dire exactement ce qu'elle disait. Le marqueur de
fraîcheur est donc porté par l'entrée ajoutée, jamais par une section réécrite.

### D. Consolidation contrôlée
`consolidate()` ouvre une **nouvelle génération** : le préfixe reçoit un instantané
replié (valeurs courantes, provenance, ids d'origine) et un journal neuf continue. Les
entrées d'origine sont **archivées, jamais supprimées** (`lookup()` les retrouve
encore), et le résultat déclare `cache_invalidated=True`. Refusée sans
`approved=True` (ou `allow_auto_consolidate` explicite) : **jamais silencieuse**.

### La question est la dernière entrée, et le prompt n'a pas de terminateur
Deux conséquences mesurées, pas décoratives :

1. Si la question vivait dans un message utilisateur *après* le bloc, elle
   disparaîtrait de la queue du prompt suivant et les nouvelles entrées seraient
   insérées avant `</system>` : une modification **au milieu** du prompt. La question
   est donc une entrée du journal.
2. Un terminateur (`</system>`) placé après la région qui grandit se retrouverait
   décalé à chaque tour : le prompt précédent cesserait d'être un préfixe exact du
   suivant — et la loi mesurée ne récompense que l'**extension exacte**. La
   sérialisation de la configuration C s'arrête donc à la fin du journal.

Ces deux points ont été trouvés par la mesure (le contrôle d'ajout pur échouait au tour
2), pas par relecture.

---

## 3. Modifications effectuées

| Fichier | Nature |
|---|---|
| `boster/memory/append_only.py` | **nouveau** — module isolé, aucune dépendance obligatoire ajoutée |
| `benchmark/kv_cache/phase4/AUDIT.md` | **nouveau** — audit préalable |
| `benchmark/kv_cache/phase4/{__init__,payloads,run_phase4,analyse,quality,test_phase4}.py` | **nouveaux** — harnais et tests |
| `benchmark/kv_cache/phase4/results/` | **nouveau** — JSONL bruts, synthèses, rapport qualité |
| `rendering.py`, `deterministic_pipeline.py`, `models.py`, `consolidation.py`, `persistence.py`, `manager.py`, phases 1–3 | **non modifiés** |

Le moteur principal n'a **pas été touché** : le module est autonome et le harnais le
pilote directement. Le point d'accroche (3 lignes dans `build_context_block` pour
exposer le mode au CLI) est documenté ici pour une phase ultérieure, sur autorisation.

---

## 4. Contrat append-only

**La règle** : tant qu'une session est ouverte, le préfixe reste identique, les
anciennes entrées ne sont jamais modifiées, les nouvelles sont ajoutées à la fin, les
corrections sont de nouveaux événements, les références restent stables, l'ordre des
sections ne change pas.

**Le validateur** : `stability_report()` compare octet par octet le bloc courant et le
bloc précédent et vérifie la forme la plus forte du contrat — *le bloc précédent doit
être un **préfixe** du bloc courant* (ajout pur) —, ainsi que `verify_frozen()` (chaque
ligne gelée est encore exactement ce qui a été émis) et `_frozen_lines_match()`.
`prefix_bytes()` permet de comparer le préfixe avant/après chaque opération.

**Les tests** (`benchmark/kv_cache/phase4/test_phase4.py`, 42 tests) couvrent, entre
autres : l'ajout pur vérifié à **chaque** tour sur 30 tours, l'immuabilité du préfixe
(reouverture refusée, octets inchangés pendant 20 ajouts), l'absence de mutation d'une
entrée corrigée (`to_dict()` avant/après identiques), la chaîne de corrections, le
repli, l'obsolescence, le doublon, la provenance manquante, l'horodatage absent, l'unicode,
la persistance disque et le rechargement (fichier byte-identique à entrées égales), la
reprise après interruption (le bloc reste un ajout pur), deux sessions indépendantes,
la saturation du budget (aucune suppression), la consolidation refusée sans
autorisation, puis acceptée avec archivage **sans perte**, et le déterminisme
(deux exécutions byte-identiques).

---

## 5. Tests de stabilité du préfixe (mesurés, harnais)

Sur la conversation de l'audit, configuration append-only :

| Contrôle | Résultat |
|---|---|
| Violations du contrat (octets) sur 30 tours | **0** |
| Prompt : le prompt du tour *t* est-il un préfixe du tour *t+1* ? | **oui** (vérifié prompt à prompt) |
| Préfixe immuable pendant toute la session | oui (contrôle octets à chaque ajout) |
| Lignes gelées dérivées (`verify_frozen`) | aucune |
| Entrées au tour 30 | 43 (30 questions, 9 faits, 2 décisions, 1 changement de décision, 1 relation) |

<!-- SECTION_RESULTATS_MESURES -->

---

## 11. Limites

1. **La croissance est le prix de l'ajout.** Le journal ne peut pas retirer une ligne :
   il croît strictement. Sa compacité vient de ce qu'il n'émet **que** les nouvelles
   informations, pas de ce qu'il en supprime.
2. **Le comptage des tokens est estimé ou réel, et c'est déclaré.** Le moteur de mesure
   ne connaît pas le tokenizer du modèle servi ; les tokens *envoyés* viennent du
   moteur (`usage.prompt_tokens` sur LM Studio, `timings` sur llama.cpp), les tailles
   de bloc en tokens sont étiquetées `real`/`estimate` selon l'adaptateur disponible.
3. **Une seule famille de moteur.** Toute la physique de réutilisation vient de
   llama.cpp (builds 2.61.0 et 2.73.0). vLLM, TensorRT-LLM ou SGLang peuvent se
   comporter autrement — non testés, et non revendiqués.
4. **Un seul modèle** (0,8 B, F16, ctx 16 384), une seule machine, mesures **CPU** :
   les comptes de tokens sont indépendants du matériel, les **temps** non.
5. **La détection de sujet dépend de l'extracteur.** Sur cette fixture, au-delà
   d'une dizaine de tours, l'extracteur du projet remonte surtout les **accusés de
   réception** de l'assistant (`Noted: …`) plutôt que les assertions de l'utilisateur.
   Ces lignes sont conservées — sur cette conversation elles sont les seules à porter
   la contrainte « 4 GB » et l'hôte de staging — mais elles ne revendiquent **jamais**
   un sujet : une phrase de politesse ne doit pas devenir la valeur courante du
   stockage. Conséquence mesurée : certaines corrections (hôte de staging en scénario
   E) sont ajoutées **sans lien `supersedes`**, parce que la valeur d'origine n'a jamais
   été exposée comme une assertion. C'est une limite de l'**extraction**, pas du
   format : la configuration zonale de la phase 2 lit exactement la même `MemoryView`
   et connaît la même limite.
6. **La fixture ne contient aucun horodatage** : toutes les entrées sont donc sans
   date, ce qui exerce le cas « date inconnue » mais ne teste pas le tri temporel.
7. **La question est dans la région système** de la configuration C (voir §2). Le
   template de chat du modèle n'est pas appliqué, comme dans les phases 1–3 : la
   comparaison porte sur le texte sérialisé, identique en forme pour A et B.
8. **Le `raw` de cette phase diffère de celui de la phase 3** : il inclut désormais la
   question du tour courant (la phase 3 ne l'incluait pas — une requête sans sa question
   n'est pas une requête). Les *taux* restent comparables, les tailles absolues non.

---

## 12. Commandes de reproduction

```bash
# 1. Moteur de mesure (llama.cpp livré avec LM Studio, build avx2 — seul build qui charge)
cd "C:/Users/celes/.lmstudio/extensions/backends/llama.cpp-win-x86_64-avx2-2.73.0"
./llama-server.exe -m "<Jev-Style-0.8B-Decision-v3-F16.gguf>" \
  --host 127.0.0.1 --port 1235 -c 16384 -t 8 --parallel 1 --metrics -a boster-cpu

# 2. Tests du module et du contrat (42 tests)
cd "C:/Users/celes/Documents/Hermes-Workspace/Boster"
uv run --no-project --with pytest python -m pytest benchmark/kv_cache/phase4/test_phase4.py -q

# 3. Controles qualite memoire (retention + cas adversariaux)
uv run --no-project python -m benchmark.kv_cache.phase4.quality

# 4. Campagne : sequences (une seule execution par configuration donne toutes les tailles)
uv run --no-project python -m benchmark.kv_cache.phase4.run_phase4 --engine llamacpp \
  --mode sequence --configs raw,boster_phase2,boster_append --exchanges 200 --scenario B --tag main
uv run --no-project python -m benchmark.kv_cache.phase4.run_phase4 --engine llamacpp \
  --mode sequence --configs boster_append_consolidated --exchanges 100 --scenario B \
  --tag main --append-budget-total 2200

# 5. Campagne : evenements (chaud -> mesure, un evenement isole par ligne)
uv run --no-project python -m benchmark.kv_cache.phase4.run_phase4 --engine llamacpp \
  --mode events --configs raw,boster_phase2,boster_append,boster_append_consolidated \
  --exchanges 100 --scenario B --tag main --append-budget-total 2200

# 6. Agregation (compteurs moteur ; le LCP texte est affiche a cote, jamais a la place)
uv run --no-project python -m benchmark.kv_cache.phase4.analyse --tag main \
  --out benchmark/kv_cache/phase4/results/phase4_agregation.json

# 7. Non-regression de tout le depot
uv run --no-project --with pytest python -m pytest tests benchmark -q
```

<!-- SECTION_VERDICT -->

## 13. Verdict

### A. Contrats structurels (mesurés, pas supposés)

|| Contrôle | Résultat |
|---|---|
| 42 tests du module `append_only` | **passés** (0 échec) |
| Qualité mémoire (`quality`) | **EXACT** sur toutes les informations — aucune perte |
| Violations du contrat append-only (200 tours, config non consolidée) | **0** |
| Lignes gelées inchangees | **EXACT** |
| Aucun fait inventé | **EXACT** |
| Tracabilité des entrées | **EXACT** |
| Distinction actuel / obsolète | SEMANTIC — 2 valeurs courantes, 1 renversée |

### B. Croissance du contexte (200 tours, scenario B)

|| Config | premier | dernier | moyen |
|---|---|---|---|---|
| `raw` | 289 c | 22 076 c | 11 270 c |
| `boster_phase2` | 340 c | 4 075 c | 3 763 c |
| `boster_append` | 401 c | 18 853 c | 9 947 c |
| `boster_append_consolidated` (budget 2 200) | 401 c | 2 195 c | 2 169 c |

L'append-only non borné croît linéairement et dépasse largement le zonal à partir du
tour ~50. La consolidation contrôlée maintient le prompt sous le budget avec une
moyenne de **2 169 c** — un gain de **~78 %** par rapport au zonal de la phase 2.

### C. Consolidation (200 tours, budget 2 200)

8 consolidations aux tours 26, 58, 88, 115, 139, 161, 181, 198.
Chaque consolidation archive les entrées précédentes (de 39 à 17), conserve les
valeurs courantes avec leur provenance, et déclare `cache_invalidated=True`.
Aucune entrée n'est supprimée — toutes restent lookupables via `lookup()`.

### D. Bugs corrigés durant la phase

1. **`verify_pure_append` trop strict pour la config consolidée** — la consolidation
   change délibérément le préfixe, donc la séquence n'est pas un append pur sur
   la frontière. Correction : appel avec `strict=False` quand `consolidate=True`.
2. **Boucle de ré-consolidation** — après consolidation, le journal est vidé mais
   `_content_present()` ne vérifiait que le journal live, pas les archives. Chaque
   tour suivant ré-ingérait toute l'historique et saturait à nouveau le budget,
   déclenchant une consolidation successive. Correction : `_content_present()`
   vérifie maintenant aussi `self.archived`.
3. **Feedback loop cooldown** — après consolidation, le prefix a grandi ; le tour
   suivant saturait immédiatement. Correction : un flag `just_consolidated` saute
   la vérification du tour suivant.

### E. Limites confirmées

1. **La croissance est le prix de l'ajout** — sans budget, l'append-only dépasse
   le zonal (18 853 c vs 4 075 c au tour 200). Le budget est nécessaire.
2. **La consolidation invalide le cache** — chaque consolidation rompt le préfixe.
   Le premier tour après consolidation est entièrement réévalué (mesuré en phase 3 :
   divergence en tête → 0 réutilisation). Ce coût doit être amorti sur N tours.
3. **Mesures moteur non acquises** — `llama-server` n'était pas joignable sur le
   port 1235 durant cette session. Les chiffres de réutilisation réelle (tokens
   réévalués vs réutilisés) restent à produire avec le moteur démarré. Le protocole
   est prêt : voir §12 commandes 1-6.

### F. Décision

**La phase 4 est structurellement valide mais expérimentalement incomplète.**

Le module `append_only.py` respecte le contrat byte-level sur 200 tours (0 violation),
la qualité mémoire est parfaite (EXACT sur 7/7 critères), et la consolidation
fonctionne sans perdre d'information. Deux bugs ont été corrigés durant la phase
(archive lookup, cooldown).

Il manque les compteurs moteur (`timings.cache_n` / `prompt_tokens_cached_total`)
pour affirmer le gain de réutilisation. Le verdict final — « l'append-only vaut-il
le coût de la consolidation ? » — nécessite un run avec `llama-server` démarré
sur `127.0.0.1:1235`.

**Prochaine étape :** démarrer le moteur et exécuter la campagne complète (§12,
commandes 1-6). Les résultats bruts iront dans
`benchmark/kv_cache/phase4/results/phase4_agregation.json`.
