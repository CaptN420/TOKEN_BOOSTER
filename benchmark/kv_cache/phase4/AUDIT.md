# PHASE KV CACHE 4 — Audit technique préalable

Produit **avant toute modification** (règle 3 du brief de phase 4).

## 1. État du dépôt

| Contrôle | Résultat |
|---|---|
| `HEAD` | `cc41439` — « v2.6-KV-cache: incremental memory, stable prefix, zonal block rendering » |
| `cc41439` présent dans l'historique | oui (`git cat-file -t cc41439` → `commit`) |
| Fichiers suivis modifiés | aucun (l'arbre de travail est propre hors livrables non commités de la phase 3) |
| Non suivis hors phase 3 | intacts (dont `a recu ou nn/`, qui n'est pas touché) |
| Publication | `origin/master` = `cc41439` ; rien à pousser, rien poussé |

Rapports lus avant cet audit : `KV_CACHE_AUDIT_REPORT.md` (309 l.), `KV_CACHE_PHASE2_REPORT.md` (614 l.),
`KV_CACHE_PHASE3_REPORT.md` (604 l.).

## 2. Ce que le moteur récompense (acquis de la phase 3, à ne pas re-mesurer)

Loi de réutilisation mesurée sur `llama-server` (2 builds 2.61.0 / 2.73.0) :

* **ajout pur en fin de prompt → réutilisation totale** (1 381/1 381) ;
* **modification en place → repli sur un point de reprise interne** (~63 % du prompt,
  la même valeur pour une divergence à 75 % comme à 98 %) ;
* **divergence dans la première moitié → 0**, même si la moitié du prompt reste identique.

Conséquence directe pour la phase 4 : la contrainte n'est pas « où placer le bloc »
(phase 2, mesuré sans effet) mais **« ne jamais modifier un octet déjà émis »**.

## 3. Ce qui existe déjà et doit être réutilisé (ne rien réécrire)

`boster/memory/` contient déjà la couche mémoire complète :

| Module | Ce qui est réutilisable |
|---|---|
| `models.py` | `MemoryItem` (id, content, kind, `source_ids`, created_at, **status**, confidence), `SourceRef(kind,id,index,role)`, `Entity`, `Relation`, `Decision` (value, reason, alternatives, `date`, `status`, `id`, `source_ids`), `Contradiction`, `Episode`, `SemanticMemory`, `EpisodicMemory`, `RecentContext`, **`UNKNOWN_TIME = 0.0`** (convention : jamais de date inventée) |
| `manager.py` | `MemoryManager` (add/retrieve/consolidate/expire), `classify_entry(text)`, `generate_id(content, prefix)` |
| `consolidation.py` | `consolidate_exchanges`, `consolidate_to_episodes`, `deduplicate_by_category`, `decision_id_for`, `_detect_contradictions`, `pipeline_stamp` |
| `persistence.py` | `MemoryPersistence.save_state/load_state` (JSON schema v2, écriture atomique temp+rename) |
| `rendering.py` | `Section`, `BlockMemo`, `memo_for`, `render_zones` (phase 2) |
| `core/tokenizer.py` | `get_tokenizer`, `count_tokens` (réel si un modèle est connu, sinon `CharEstimateAdapter` **déclaré comme estimation**) |

Et le point d'intégration côté pipeline : `build_context_block()` assemble des `Section`
par zone puis `render_zones(sections, memo)` (ligne ~1071) — le rendu par zones est déjà
mémoïsé **par empreinte de section**, ce qui garantit l'identité octet-pour-octet d'une
section inchangée, mais **re-rend la section entière dès que sa liste change**.

## 4. Diagnostic : pourquoi la phase 2 ne suffit pas

Le mode zonal actuel réémet, à chaque tour, des sections **re-dérivées** depuis
l'état mémoire courant : `facts` (liste dédupliquée, tronquée à une allowance),
`current decisions` (ordre et marqueurs de statut), `history`, `recent` (fenêtre
glissante). Même quand le texte est très stable (contrôle gelé : 94,6 % de stabilité
mesurée), **le moteur ne réutilise que 65,3 %** — parce qu'une section réécrite en
place, même de 3 lignes, déclenche le repli sur le point de reprise.

## 5. Architecture retenue (plan d'implémentation)

Nouveau module **isolé**, sans toucher aux modules existants :
`boster/memory/append_only.py`.

```
prompt(t) = [en-tête] [PRÉFIXE IMMUABLE] [JOURNAL FIGÉ : E1..En] [Q_t]
                   ^ jamais modifié      ^ chaque entrée gelée à l'émission
                                         ^ la seule chose qui grandit est la fin
```

1. **Préfixe immuable** (catégorie A) — figé à l'ouverture de session : instructions,
   contraintes **connues à l'ouverture**, structure d'en-tête, références stables.
   Décision assumée : une contrainte découverte **en cours** de session n'entre pas
   dans le préfixe (elle le modifierait) ; elle devient une **entrée du journal**
   (`type=constraint`), donc toujours présente dans le prompt et marquée `current` à sa
   dernière occurrence.
2. **Journal append-only** (catégorie B) — entrées typées (`fact`, `correction`,
   `decision`, `decision_change`, `relation`, `constraint`, `event`, `resolution`,
   `obsolete`, `exchange`) avec identifiant stable (`F001`, `C003`, `D002`…), contenu,
   provenance (`source_ids`, jamais inventée), horodatage **optionnel** (« date
   inconnue » si absent), référence à l'entrée corrigée, statut, confiance optionnelle.
   Le rendu de chaque entrée est **gelé à l'émission** (`frozen_lines[entry_id]`) :
   réémettre le journal ne reformate jamais une entrée passée.
3. **État courant calculé** (catégorie C) — `fold_current()` parcourt le journal dans
   l'ordre et applique les corrections : « dernier écrivain gagne » par sujet. **Aucune
   entrée passée n'est mutée** ; le statut d'une entrée est *dérivé* (elle est
   `superseded` parce qu'une entrée ultérieure la nomme). Le marqueur `(current)` /
   `(supersedes X)` est porté **par l'entrée nouvellement ajoutée**, jamais par une
   section réécrite.
4. **Consolidation contrôlée** (catégorie D) — génère une **nouvelle génération** :
   préfixe_v(n+1) = préfixe_v(n) + instantané replié (valeurs courantes + ids d'origine
   + provenance), puis journal suivant. Les entrées d'origine sont **archivées**, pas
   supprimées (récupérables). L'opération **déclare** `cache_invalidated=True` et est
   tracée ; jamais silencieuse.
5. **Contrat de stabilité** (règle 5) — un validateur compare octet par octet le texte
   de la génération précédente et le nouveau ; toute divergence hors du suffixe
   nouvellement ajouté est signalée (`stability_report()`).
6. **Budget** (règle 6) — budget total, réserve requête, réserve réponse, marge de
   sécurité, seuil d'alerte, seuil de consolidation ; comptage via le tokenizer du
   projet (réel ou **déclaré estimé**) ; saturation jamais résolue par suppression
   silencieuse.

**Exposition** : aucune modification du moteur principal dans cette phase. Le module est
autonome et le harnais le pilote directement ; le point d'accroche (3 lignes dans
`build_context_block`) est documenté dans le rapport pour une phase ultérieure, sur
autorisation.

### Fichiers touchés (diff minimal)

| Fichier | Nature |
|---|---|
| `boster/memory/append_only.py` | **nouveau** (isolé, aucune dépendance obligatoire nouvelle) |
| `benchmark/kv_cache/phase4/*` | **nouveau** (harnais, tests, résultats) |
| `KV_CACHE_PHASE4_REPORT.md` | **nouveau** (rapport) |
| tout module existant (`rendering.py`, `deterministic_pipeline.py`, `models.py`, `consolidation.py`, `persistence.py`, `manager.py`) | **non modifié** |

## 6. Risques identifiés (et mitigation)

| Risque | Gravité | Mitigation |
|---|---|---|
| **Le journal croît linéairement** → le prompt append-only finit plus gros que le budget (et plus gros que le prompt zonal borné de la phase 2). C'est la tension centrale de la phase. | élevée | Mesurer la croissance à 25/50/100/200 tours, la publier même si défavorable, et n'atteindre le budget borné que **via consolidation** (en payant l'invalidation de cache, mesurée). |
| **La consolidation détruit le préfixe** → première requête après consolidation entièrement réévaluée (loi du §2 : divergence en tête → 0 réutilisation). | élevée | La déclarer dans le résultat, la **mesurer** (`cache_n=0` attendu) et comparer le coût amorti sur N tours. |
| **`recent`/`exchange` en fin de journal** peut rejouer la fenêtre glissante et recréer une réécriture en place. | moyenne | Le journal n'a **pas** de fenêtre glissante : un échange déjà émis reste figé. Le budget est tenu par consolidation, pas par troncature. |
| **Octets non déterministes** (dict non ordonné, `set`, horloge murale) cassant le contrat de stabilité. | moyenne | `created_at` optionnel (`None` → « date inconnue »), `saved_at` paramétrable, aucune itération sur `set`, tests comparant les octets avant/après chaque opération. |
| **Dérive silencieuse du préfixe** (un appel réordonne les entrées). | élevée | `stability_report()` compare octets **et** longueur du préfixe commun ; test dédié qui échoue si un octet du préfixe bouge. |
| **Qualité mémoire sacrifiée au cache** | élevée | Les contrôles de la phase 3 (rétention des faits, relations, décisions, négations, contraintes, provenance, zéro fait inventé) sont rejoués sur l'append-only **avant** tout verdict ; un gain de cache avec perte d'information interdit le verdict positif. |
| **Fausse victoire** : lire le LCP texte comme un cache hit. | élevée | Aucun chiffre de cache sans `timings.cache_n` / `prompt_tokens_cached_total` du moteur ; les chiffres `raw` et phase 2 sont re-mesurés dans la **même** campagne. |

## 7. Protocole expérimental (règle 7)

Reprise **à l'identique** du protocole validé en phase 3 : même moteur (`llama-server`
avx2, `127.0.0.1:1235`, modèle `Jev-Style-0.8B-Decision-v3-F16`, ctx 16 384, `-t 8`,
`--parallel 1`), même fixture de conversation, `n_predict=1`, `temperature=0`,
`cache_prompt=true`, **remise à froid du cache avant chaque configuration** (prompt au
premier token divergent) avec contrôle `cache_n=0` au premier tour mesuré, paires
**chaud → mesuré** pour les événements, et recoupement de chaque somme avec le compteur
cumulatif `/metrics`.

Configurations comparées : **A** historique brut, **B** Boster phase 2 (zonal),
**C** Boster append-only, **D** append-only + consolidation contrôlée.
Tailles : 25, 50, 100, 200 tours. Événements : question identique, question différente,
ajout de fait, correction de fait, nouvelle décision, correction de décision, relation,
contrainte, budget serré, consolidation.

Mesures moteur obligatoires : tokens envoyés, réévalués, réutilisés, taux réel de
réutilisation, temps de préfill, temps de génération, latence totale. Mesures système :
croissance du contexte, taille du journal, taille de l'état courant, nombre de
consolidations, nombre d'informations conservées.

## 8. Conclusion de l'audit

L'append-only est **implémentable sans toucher à l'existant** : la couche mémoire
fournit déjà les identifiants, la provenance, les statuts et la persistance, et le
harnais de la phase 3 fournit déjà le moteur à compteurs et le protocole validé. Le
risque dominant n'est pas technique mais **expérimental** : l'append-only gagne sur la
réutilisation et perd sur la taille du contexte, et la consolidation est le seul
mécanisme qui réconcilie les deux — en invalidant le cache. Les trois chiffres à
produire sont donc : (1) la réutilisation réelle en append-only, (2) la croissance du
contexte en face, (3) le coût mesuré d'une consolidation. **Aucun de ces chiffres ne
sera déclaré sans compteur moteur.**
