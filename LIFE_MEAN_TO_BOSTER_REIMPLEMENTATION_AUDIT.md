# Audit de réutilisation — Life Mean Labs vers Boster

**Source examinée :** `Life-Mean-Labs-main.zip` (archive extraite localement)  
**Objectif :** repérer les idées et composants qui peuvent renforcer Boster, sans importer aveuglément l'ancienne architecture.

## Conclusion exécutive

Oui : l'archive contient plusieurs briques pertinentes pour Boster. La plus précieuse n'est pas un compresseur prêt à copier, mais un ensemble de concepts déjà expérimentés : mémoire à plusieurs horizons, consolidation structurée, budget de contexte par catégorie, sélection adaptative, mémoire des erreurs et traçabilité des exécutions.

La meilleure voie est de **réimplémenter les concepts dans le cœur autonome de Boster**, avec des interfaces simples et des tests de rétention. Ne pas copier les modules tels quels : plusieurs sont liés à Life Mean/CaptN, à des débats ou à des dépendances externes, et certains comportent des limites techniques importantes.

## Classement des composants candidats

| Priorité | Source dans Life Mean Labs | Ce qui vaut la peine d'être repris | Décision |
|---|---|---|---|
| P0 | `philosophical_council/memory/short_term.py`, `mid_term.py`, `long_term.py` | Séparation mémoire récente / synthèses intermédiaires / connaissances persistantes | Réimplémenter comme trois niveaux de mémoire génériques, indépendants du domaine débat |
| P0 | `philosophical_council/memory/mid_term.py` | Champs explicites pour arguments, contre-arguments, accords, désaccords, contradictions, changements de position et questions ouvertes | Adapter en schéma générique : faits, relations, décisions, alternatives, contradictions, questions ouvertes |
| P0 | `code_lab/compression/history_compressor.py` | Résumé structuré distinguant faits, actions terminées, éléments en attente et pistes abandonnées | Reprendre le format conceptuel; remplacer les regex fragiles par une extraction testable et préserver la provenance |
| P0 | `code_lab/compression/context_budget.py` | Budgets séparés pour système, historique, mémoire, outils et contexte récupéré; journal des décisions de sélection | Réimplémenter le modèle de budget et les décisions explicables, pas l'algorithme de sélection actuel tel quel |
| P1 | `code_lab/compression/adaptive_manager.py`, `library/reliability/context.py` | Mesurer la complexité et la pression du contexte pour ajuster l'allocation | Extraire une petite stratégie facultative; ne pas laisser l'adaptatif modifier le contenu sans garde-fous |
| P1 | `experiments/metacognition/schemas.py`, `experiments/metacognition/memory.py` | Mémoire des exécutions avec contraintes, étapes, traces, décisions, résultats, erreurs, corrections et métriques | Réutiliser le modèle de données comme inspiration pour provenance et historique de Boster |
| P1 | `code_lab/error_memory.py` | Conserver les erreurs observées et les stratégies tentées/réussies | Réimplémenter après correction du modèle de signature et des états succès/échec |
| P2 | `code_lab/checkpoint.py`, `code_lab/persistence.py` | Snapshots et retour arrière | Utile pour versions de mémoire et compression réversible, mais le code actuel vise la restauration de fichiers de workspace, pas la mémoire de conversation |

## 1. Mémoire à trois horizons — meilleure inspiration

Les modules `short_term.py`, `mid_term.py` et `long_term.py` donnent une séparation simple :

- **Court terme :** garder les derniers échanges presque intacts.
- **Moyen terme :** condenser des segments en structures thématiques.
- **Long terme :** conserver des éléments persistants indexés par clé et catégorie.

Pour Boster, il faudrait généraliser cette séparation :

1. `RecentContext` — derniers messages bruts, ordonnés, non réécrits inutilement.
2. `EpisodicMemory` — événements et résumés de tâches avec identifiants de source.
3. `SemanticMemory` — faits et relations durables, avec statut et temporalité.

Chaque élément devrait avoir au minimum : `id`, `content`, `kind`, `source_ids`, `created_at`, `updated_at`, `confidence` ou `status`. Les relations devraient être des objets explicites (`subject`, `predicate`, `object`, `source_id`), pas seulement des phrases aplaties.

**Pourquoi c'est directement lié au benchmark Boster :** cette architecture donne un endroit où conserver les relations, décisions et changements au lieu de sélectionner uniquement les lignes qui partagent des mots avec la requête.

## 2. Historique structuré — bon concept, extracteur trop fragile

`code_lab/compression/history_compressor.py` produit un `HistorySummary` avec quatre groupes : `facts`, `completed`, `pending`, `dead_ends`. C'est une bonne base fonctionnelle pour éviter un résumé monolithique.

Limites observées dans le code :

- L'extraction repose surtout sur des expressions régulières et des formulations anglaises précises.
- Les décisions sont extraites sans véritable résolution des versions successives.
- Les questions utilisateur sont conservées comme questions ouvertes même si elles ont peut-être reçu une réponse plus tard.
- Les noms d'entités et les liens entre elles ne sont pas représentés explicitement.
- `preserve_last` est exposé par `compress_history`, mais n'est pas utilisé dans son corps.
- Le rendu tronque par nombre de lignes; il ne garantit pas la préservation des éléments les plus importants.

**À reprendre :** les catégories de sortie et le principe de garder les échanges récents séparément.  
**À refaire :** extraction, déduplication, résolution temporelle et allocation du budget. Un résumé ne devrait jamais effacer la trace brute qui l'a produit.

## 3. Budget de contexte — architecture utile, sélection à remplacer

`code_lab/compression/context_budget.py` définit des catégories de budget et un `SelectionResult` détaillé : tokens originaux/sélectionnés, dépassements, éléments supprimés, décisions de sélection et contenu final. Cette observabilité est excellente pour Boster.

Mais le sélecteur actuel utilise notamment le chevauchement de mots (`split()` et intersection d'ensembles), plus la récence. Cela explique pourquoi il peut perdre des noms d'entités et des relations exprimées avec d'autres mots.

Autres points à corriger avant toute réutilisation :

- Le module tente de télécharger un tokenizer Hugging Face au chargement du module. Un import de cœur ne devrait jamais déclencher un accès réseau.
- `tiktoken` et `tokenizers` sont optionnels; l'estimation de secours `caractères / 4` doit être présentée comme une estimation, jamais comme un comptage exact.
- La sélection d'historique ajoute les messages dans l'ordre du score; il faut vérifier et garantir que l'ordre chronologique est restauré avant de reconstruire le prompt.
- Une synthèse compressée est ajoutée comme message `user`; Boster devrait utiliser un bloc de contexte typé, sans falsifier le rôle d'origine.
- Le chemin d'import vers `captn.runtime.history_compressor` couple le composant à un autre paquet.

**À reprendre :** budgets par catégorie, rapport explicable, décisions de sélection, comptage interchangeable par tokenizer.  
**À refaire :** score de pertinence, conservation des relations, ordre chronologique, gestion des rôles et intégration hors ligne.

## 4. Budget adaptatif — à garder facultatif

`code_lab/compression/adaptive_manager.py` et `library/reliability/context.py` calculent des signaux déterministes (longueur de requête, profondeur d'historique, outils, mémoire, pression du contexte, complexité multi-dimensionnelle), puis proposent une allocation.

C'est intéressant pour une future stratégie `adaptive`, mais ce n'est pas la première priorité. Boster doit d'abord prouver qu'il conserve les connaissances importantes avec un budget fixe. Ensuite seulement, on pourra tester si l'adaptation améliore le compromis rétention/coût.

## 5. Mémoire des exécutions et provenance

`experiments/metacognition/schemas.py` décrit un `RunMemory` assez riche : prompt initial, contraintes, domaine, modèles, plan, étapes, traces d'agents, critiques, révisions, décision, résultats de tests, erreurs, corrections, durée, tokens et timestamps.

Le concept utile pour Boster est la **provenance** : chaque fait mémorisé doit pouvoir pointer vers l'échange ou l'événement d'où il vient. Pour Boster, un schéma plus léger suffit :

- `MemoryItem` : contenu atomique ou synthèse.
- `SourceRef` : conversation, message, tâche ou fichier source.
- `Relation` : lien explicite entre deux éléments.
- `Revision` : remplacement ou correction d'un élément antérieur.

Le stockage JSON de ce module est une référence simple, mais il faudra prévoir des écritures atomiques, une validation de schéma et des migrations de version.

## 6. Mémoire des erreurs — idée pertinente, ne pas copier le code actuel

`code_lab/error_memory.py` mémorise les types d'erreurs, stratégies tentées et corrections réussies. Le concept peut aider Boster à apprendre quelles opérations de compression ou de récupération ont échoué.

Mais le code présente des incohérences :

- `record_error()` calcule sa signature avec `error_type` et un message vide; deux erreurs différentes du même type sont fusionnées.
- `get_failed_strategies()` retourne toutes les stratégies tentées, pas seulement celles qui ont échoué.
- `should_avoid_strategy()` évite une stratégie après trois tentatives, même si certaines ou toutes ont réussi.

Pour Boster, distinguer explicitement `attempted`, `succeeded`, `failed`, `validated` et `reverted`. Ne jamais apprendre qu'une stratégie est mauvaise à partir du seul nombre de tentatives.

## 7. Ce qu'il ne faut pas importer dans le cœur de Boster

- Les intégrations LLM, dashboards, orchestration multi-agents et modules scientifiques ne sont pas nécessaires à un moteur autonome d'optimisation du contexte.
- Les dépendances comme Pydantic, requests, tokenizers ou tiktoken ne doivent pas devenir obligatoires dans le cœur stdlib-only.
- Le chargement réseau au moment de l'import doit être supprimé.
- Les modules spécialisés débat/philosophie doivent être convertis en abstractions génériques avant toute réutilisation.
- L'archive contient `Personal_NOTES/JOURNAL.txt` et d'autres données historiques. Ne pas les copier dans le dépôt public Boster sans revue explicite de confidentialité.

## 8. Licence et provenance

L'archive inclut une licence Apache License 2.0 à la racine. Avant de copier du code source, vérifier les avis de copyright et conserver les obligations de licence/attribution applicables. Si l'objectif est de garder Boster sous MIT, la voie la plus propre est de réimplémenter les idées et interfaces de façon indépendante, sans copier des blocs de code, tout en faisant vérifier les obligations applicables si du code est repris.

## 9. Plan d'intégration recommandé dans Boster

### Étape A — modèle de mémoire (priorité absolue)

Créer un petit modèle stdlib-only :

- `MemoryItem` avec type, contenu, sources, timestamps, statut et version.
- `Entity` et `Relation(subject, predicate, object, source_id)`.
- `Decision` avec valeur, raison, alternatives, date et statut (`active`, `superseded`, `rejected`, `uncertain`).
- `Contradiction` reliant les éléments incompatibles sans en supprimer un.

### Étape B — consolidation sans perte

- Garder les derniers échanges bruts.
- Consolider les échanges plus anciens en épisodes structurés.
- Extraire faits, entités, relations, décisions, contraintes, questions ouvertes et erreurs.
- Conserver les références aux messages d'origine et permettre de revenir au brut.

### Étape C — sélection relationnelle

- Rechercher d'abord les éléments liés aux entités mentionnées.
- Ajouter les relations nécessaires pour interpréter les faits sélectionnés.
- Ajouter ensuite récence, importance, statut et correspondance lexicale.
- Sélectionner par coût token estimé, mais ne jamais couper une relation de son sujet/objet.
- Préserver l'ordre chronologique des messages récents.

### Étape D — budgets et cache

- Garder un `ContextBudget` explicable par catégorie.
- Surveiller passivement la pression du contexte.
- Consolider en une seule fois au seuil configuré ou à la fin d'une tâche, sans réécrire le préfixe à chaque tour.
- Ne prétendre à un gain de cache KV que si mesuré avec le runtime ciblé.

### Étape E — benchmark de rétention

Reprendre le benchmark déjà lancé pour mesurer séparément :

- rétention des faits;
- rétention des relations;
- rétention des décisions et raisons;
- gestion des contradictions et de leur résolution;
- exactitude temporelle;
- rappel à longue distance;
- taux de réponses non soutenues;
- tokens retenus et latence.

Comparer au minimum : aucune compression, sélecteur actuel, mémoire structurée relationnelle, mémoire relationnelle + consolidation au seuil.

## Verdict

**Les éléments les plus réutilisables sont la mémoire à trois horizons, les catégories de consolidation, le budget par catégorie et la provenance des exécutions.** Ils répondent précisément aux faiblesses observées dans le benchmark Boster.

La bonne décision n'est pas de transformer Boster en Life Mean Labs ni d'y importer tout le système. C'est de récupérer les idées qui ont fait leurs preuves, de les réduire à des composants autonomes, puis de les valider avec le benchmark de rétention avant de les intégrer au pipeline principal.
